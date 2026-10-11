"""State store, sessions, login throttling and the theme resolver (no HTTP involved)."""
import os
import time

import pytest

from pluteus.config import User
from pluteus.ratelimit import FailureLimiter
from pluteus.sessions import SessionManager, hash_token, new_token
from pluteus.state import StateDB
from pluteus.themes import Themes


# ---- state: sessions ---------------------------------------------------------------
def test_session_roundtrip_expiry_and_delete():
    s = StateDB()
    s.create_session("h1", "alice", "fp", ttl=100, now=1000)
    assert s.get_session("h1", now=1050)["username"] == "alice"
    assert s.get_session("h1", now=1101) is None          # expired sessions are deleted on sight
    assert s.get_session("h1", now=1050) is None
    s.create_session("h2", "alice", "fp", 100, now=1000)
    s.create_session("h3", "bob", "fp", 100, now=1000)
    assert s.delete_user_sessions("alice") == 1 and s.get_session("h3", now=1001)
    s.touch_session("h3", ttl=500, now=1090)
    assert s.get_session("h3", now=1500)["expires"] == 1590
    assert s.purge_expired(now=2000) == 1


# ---- state: progress and prefs ----------------------------------------------------------
def test_progress_upsert_clamp_recent_and_badges():
    s = StateDB()
    s.put_progress("alice", "books", "a/b.epub", "epubcfi(/6/4)", 0.25, now=10)
    s.put_progress("alice", "books", "a/b.epub", "epubcfi(/6/8)", 0.5, now=20)        # replaces
    s.put_progress("alice", "books", "c.epub", "x", 7.0, now=30)                       # clamped to 1
    s.put_progress("bob", "books", "a/b.epub", "other", 0.9, now=40)
    assert s.get_progress("alice", "books", "a/b.epub") == {"locator": "epubcfi(/6/8)", "percent": 0.5, "updated": 20}
    assert s.get_progress("alice", "books", "nope") is None
    assert [r["path"] for r in s.recent_progress("alice")] == ["c.epub", "a/b.epub"]
    assert s.recent_progress("alice", limit=1)[0]["percent"] == 1.0
    assert s.progress_for("alice", "books", ["a/b.epub", "c.epub", "zzz"]) == {"a/b.epub": 0.5, "c.epub": 1.0}
    many = ["f%d" % i for i in range(1000)]
    s.put_progress("alice", "books", "f999", "l", 0.1)
    assert s.progress_for("alice", "books", many) == {"f999": 0.1}                    # more than one query chunk


def test_prefs_roundtrip():
    s = StateDB()
    assert s.get_prefs("alice") == {}
    s.set_prefs("alice", {"theme": "sepia", "größe": 3})
    assert s.get_prefs("alice") == {"theme": "sepia", "größe": 3}


# ---- state: persistence, backups, damage -----------------------------------------------------
def test_state_persists_and_backups_are_made_and_pruned(tmp_path):
    d = str(tmp_path / "data")
    s = StateDB(d)
    s.put_progress("alice", "books", "x.epub", "loc", 0.3)
    s.close()
    s2 = StateDB(d)
    assert s2.get_progress("alice", "books", "x.epub")["percent"] == 0.3
    backups = str(tmp_path / "bk")
    for i in range(9):
        s2.backup(backups, keep=7, now=1_700_000_000 + i)
    names = sorted(os.listdir(backups))
    assert len(names) == 7
    import sqlite3
    copy = sqlite3.connect(os.path.join(backups, names[-1]))
    assert copy.execute("SELECT percent FROM progress").fetchone()[0] == 0.3
    copy.close()
    assert StateDB().backup(backups) is None                                           # in-memory: nothing to back up


def test_housekeeping_backs_up_only_when_the_newest_backup_is_old(tmp_path):
    s = StateDB(str(tmp_path / "data"))
    backups = str(tmp_path / "data" / "backups")
    s.housekeeping(backups, now=time.time())
    assert len(os.listdir(backups)) == 1
    s.housekeeping(backups, now=time.time() + 60)
    assert len(os.listdir(backups)) == 1                                               # fresh enough
    s.housekeeping(backups, now=time.time() + 25 * 3600)
    assert len(os.listdir(backups)) == 2


def test_a_damaged_state_file_is_kept_aside_and_a_new_one_started(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "state.sqlite3").write_bytes(b"not a database at all " * 200)
    s = StateDB(str(d))
    s.put_progress("alice", "books", "x", "l", 0.1)
    assert s.get_progress("alice", "books", "x")["percent"] == 0.1
    assert any(n.startswith("state.sqlite3.corrupt-") for n in os.listdir(d))          # never deleted
    s.close()


# ---- sessions ---------------------------------------------------------------------------------------
def users():
    return {"alice": User("alice", "scrypt$1$1$1$aa$bb"), "bob": User("bob", "scrypt$1$1$1$cc$dd")}


def test_session_manager_validates_token_user_and_password():
    state = StateDB()
    sm = SessionManager(state, days=30)
    us = users()
    token = sm.start(us["alice"])
    assert len(token) >= 40 and hash_token(token) != token                              # only the hash is stored
    assert state.get_session(token) is None
    assert sm.user_for(token, us).name == "alice"
    assert sm.user_for("garbage", us) is None and sm.user_for(None, us) is None and sm.user_for("", us) is None
    changed = dict(us, alice=User("alice", "scrypt$1$1$1$NEW$HASH"))
    assert sm.user_for(token, changed) is None                                          # password change logs out
    assert sm.user_for(token, us) is None                                               # and the session is gone for good
    t2 = sm.start(us["bob"])
    assert sm.user_for(t2, {"alice": us["alice"]}) is None                              # user removed
    t3 = sm.start(us["bob"])
    sm.end(t3)
    assert sm.user_for(t3, us) is None
    assert new_token() != new_token()


# ---- throttling ----------------------------------------------------------------------------------------
def test_limiter_locks_a_name_after_five_failures_and_recovers():
    lim = FailureLimiter(max_per_user=5, max_per_ip=30, window=300)
    for i in range(4):
        lim.failure("Alice", "1.1.1.1", now=100 + i)
    assert lim.wait_seconds("alice", "9.9.9.9", now=110) == 0
    lim.failure("alice", "2.2.2.2", now=111)
    assert 0 < lim.wait_seconds("ALICE", "9.9.9.9", now=112) <= 300                      # any address, any case
    assert lim.wait_seconds("bob", "9.9.9.9", now=112) == 0                               # other users unaffected
    assert lim.wait_seconds("alice", "9.9.9.9", now=100 + 301) == 0                       # window passed
    lim.failure("alice", "1.1.1.1", now=500)
    lim.success("alice")
    assert lim.wait_seconds("alice", "9.9.9.9", now=501) == 0


def test_limiter_has_a_looser_per_address_limit():
    lim = FailureLimiter(max_per_user=5, max_per_ip=30, window=300)
    for i in range(30):
        lim.failure("user%d" % i, "3.3.3.3", now=100)                                    # many names, one address
    assert lim.wait_seconds("fresh", "3.3.3.3", now=101) > 0
    assert lim.wait_seconds("fresh", "4.4.4.4", now=101) == 0


# ---- themes ------------------------------------------------------------------------------------------------
@pytest.fixture
def themes(tmp_path):
    mine = tmp_path / "themes"
    (mine / "ocean" / "icons").mkdir(parents=True)
    (mine / "ocean" / "theme.css").write_text(":root{--pl-accent:#08c}", encoding="utf-8")
    (mine / "ocean" / "icons" / "next.svg").write_text("<svg/>", encoding="utf-8")
    (mine / "ocean" / "notes.txt").write_text("not allowed", encoding="utf-8")
    (tmp_path / "secret.css").write_text("outside", encoding="utf-8")
    return Themes(str(mine))


def test_theme_names_and_existence(themes):
    names = themes.names()
    assert names[0] == "default" and {"sepia", "night", "ocean"} <= set(names)
    assert themes.exists("ocean") and not themes.exists("nope") and not themes.exists("../x")


def test_theme_files_fall_back_to_the_default_theme(themes):
    assert themes.resolve("ocean", "icons/next.svg").read_text("utf-8") == "<svg/>"               # overridden
    assert themes.resolve("ocean", "icons/prev.svg").parts[-3:] == ("default", "icons", "prev.svg")  # inherited
    assert themes.resolve("sepia", "icons/menu.svg") is not None
    assert themes.resolve("ocean", "nothing.css") is None


@pytest.mark.parametrize("rel", ["../secret.css", "icons/../../secret.css", "/etc/passwd.css", "a\\b.css",
                                 "C:/x.css", "notes.txt", "", "icons//next.svg", "theme.css\x00.png"])
def test_theme_resolver_refuses_unsafe_or_disallowed_paths(themes, rel):
    assert themes.resolve("ocean", rel) is None


def test_theme_names_are_validated(themes):
    assert themes.resolve("../themes", "theme.css") is None and themes.resolve("", "theme.css") is None


# ---- escalating locks and known devices (login throttling for a server on the internet) ------------------------------
def fill(lim, name="alice", start=0.0, n=5, ip="9.9.9.9", **kw):
    last = (0, 0)
    for i in range(n):
        last = lim.failure(name, ip, now=start + i, **kw)
    return last


def test_each_lock_of_the_same_name_is_longer_up_to_a_cap():
    lim = FailureLimiter(max_per_user=5, max_per_ip=999, window=300)
    now, lengths = 0.0, []
    for _ in range(6):
        seconds, number = fill(lim, start=now)
        lengths.append(seconds)
        until = now + 4 + seconds
        assert lim.wait_seconds("alice", "9.9.9.9", now=now + 10) > 0                    # locked
        assert lim.wait_seconds("alice", "9.9.9.9", now=until + 1) == 0                  # and over again
        now = until + 2
    # 5 minutes (counted from the first failure, so a little less), then 30 minutes, 3 hours, then 6 hours for good
    assert 290 <= lengths[0] <= 300 and lengths[1] == 1800 and lengths[2] == 10800 and lengths[3:] == [21600] * 3


def test_other_names_are_not_affected_by_a_lock_and_more_guesses_do_not_stack_up(cfg_free=None):
    lim = FailureLimiter(max_per_user=5, max_per_ip=999, window=300)
    first = fill(lim)
    assert first[1] == 1
    assert lim.failure("alice", "9.9.9.9", now=20) == (0, 0)                              # a guess while locked: nothing new
    assert lim.wait_seconds("bob", "9.9.9.9", now=20) == 0
    assert fill(lim, name="alice", start=400)[1] == 2                                     # the next round is lock number two


def test_a_good_login_or_a_quiet_day_starts_again_from_the_short_lock():
    lim = FailureLimiter(max_per_user=5, max_per_ip=999, window=300)
    fill(lim)
    fill(lim, start=1000)
    assert fill(lim, start=100000)[1] == 1                                                # a day later: forgiven
    lim.success("alice")
    fill(lim, start=200000)
    assert lim.failure("alice", "9.9.9.9", now=200100) == (0, 0)
    lim.success("alice")
    assert lim.wait_seconds("alice", "9.9.9.9", now=200101) == 0                          # success clears the lock too


def test_a_known_device_is_not_stopped_by_a_lock_and_its_typos_do_not_count_against_the_account():
    lim = FailureLimiter(max_per_user=5, max_per_ip=999, window=300)
    fill(lim)                                                                             # strangers lock the name
    assert lim.wait_seconds("alice", "9.9.9.9", now=10) > 0
    assert lim.wait_seconds("alice", "9.9.9.9", now=10, device="dev1", known=True) == 0   # the owner's browser still gets in
    lim2 = FailureLimiter(max_per_user=5, max_per_ip=999, window=300)
    for i in range(4):
        lim2.failure("alice", "9.9.9.9", now=i, device="dev1", known=True)               # the owner mistyped four times
    assert lim2.wait_seconds("alice", "8.8.8.8", now=5) == 0                              # a stranger is not locked out by that
    lim2.failure("alice", "9.9.9.9", now=5, device="dev1", known=True)
    assert lim2.wait_seconds("alice", "9.9.9.9", now=6, device="dev1", known=True) > 0    # but a device has its own limit
    assert lim2.wait_seconds("alice", "8.8.8.8", now=6) == 0
    lim2.success("alice", device="dev1")
    assert lim2.wait_seconds("alice", "9.9.9.9", now=7, device="dev1", known=True) == 0


def test_snapshot_lists_locks_and_failures_and_unlock_clears_them():
    lim = FailureLimiter(max_per_user=5, max_per_ip=999, window=300)
    fill(lim, name="locked")
    for i in range(2):
        lim.failure("busy", "9.9.9.9", now=10 + i)
    rows = {r["name"]: r for r in lim.snapshot(now=20)}
    assert rows["locked"]["locked_for"] > 0 and rows["locked"]["locks"] == 1
    assert rows["busy"] == {"name": "busy", "locked_for": 0, "locks": 0, "recent_failures": 2}
    assert lim.snapshot(now=20)[0]["name"] == "locked"                                  # locked names first
    assert lim.unlock("LOCKED") is True and lim.unlock("locked") is False               # case-insensitive; nothing left to clear
    assert lim.wait_seconds("locked", "9.9.9.9", now=20) == 0
    assert {r["name"] for r in lim.snapshot(now=20)} == {"busy"}
    assert lim.snapshot(now=10000) == []                                                 # everything ages out


def test_forget_user_drops_remembered_reader_app_logins():
    from pluteus.auth import Authenticator, hash_password
    from pluteus.config import User
    users = {"a": User("a", hash_password("a-password-1", n=2 ** 4)), "b": User("b", hash_password("b-password-1", n=2 ** 4))}
    auth = Authenticator(users)
    import base64
    head = lambda n, p: "Basic " + base64.b64encode(("%s:%s" % (n, p)).encode()).decode()
    assert auth.authenticate(head("a", "a-password-1")) and auth.authenticate(head("b", "b-password-1"))
    auth.forget_user("a")
    assert auth.cached_user(head("a", "a-password-1")) is None and auth.trusted_user(head("a", "a-password-1")) is None
    assert auth.cached_user(head("b", "b-password-1")) is not None                      # others keep theirs

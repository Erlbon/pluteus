"""Code review of 2026-11-12, the config, state and admin side: cards 4, 7, 25, 26, 34, 35, 36, 59, 63, 64, 66, 67, 69 and 74 (c)."""
import os
import shutil
import socket
import sqlite3
import sys
import threading

import pytest
from conftest import FAST, basic
from starlette.testclient import TestClient

from pluteus import __main__ as cli
from pluteus import auth as authmod
from pluteus import config as configmod
from pluteus import dbutil, firstrun, libedit, share, supervise, useredit, vault
from pluteus.admin import AdminModel
from pluteus.adminapp import create_admin_app, start_admin_server
from pluteus.app import create_app
from pluteus.config import ConfigError, load_config, parse_config
from pluteus.reload import UsersReloader
from pluteus.sessions import SessionManager, hash_token
from pluteus.state import StateDB
from pluteus.useredit import EditError

FORM = {"content-type": "application/x-www-form-urlencoded", "origin": "http://testserver"}
BOSS = basic("boss", "boss-password-1")


def slashes(path) -> str:
    return str(path).replace("\\", "/")


def make_config(tmp_path, server_extra: str = "", where=None):
    """A config file in its own folder (tmp_path/etc), libraries and data beside it: nothing overlaps."""
    for name in ("books", "comics", "etc"):
        (tmp_path / name).mkdir(exist_ok=True)
    users = ""
    for name, password, extra in (("boss", "boss-password-1", "admin = true\n"), ("alice", "alice-password-1", ""),
                                  ("deputy", "deputy-password-1", "admin = true\n")):
        users += '[[users]]\nname = "%s"\npassword_hash = "%s"\n%s\n' % (name, authmod.hash_password(password, **FAST), extra)
    text = ('[server]\nport = 8080\nadmin_port = 8101\ndata_dir = "%s"\nuploads_dir = "%s"\n%s\n'
            '[[roots]]\nname = "books"\npath = "%s"\n\n[[roots]]\nname = "comics"\npath = "%s"\n\n%s'
            % (slashes(tmp_path / "data"), slashes(tmp_path / "up"), server_extra, slashes(tmp_path / "books"),
               slashes(tmp_path / "comics"), users))
    path = where or tmp_path / "etc" / "config.toml"
    path.write_text(text, encoding="utf-8", newline="")
    return path


@pytest.fixture
def admin(tmp_path):
    path = make_config(tmp_path)
    app = create_app(load_config(str(path)), config_path=str(path), reload_interval=0.0)
    return TestClient(create_admin_app(app), follow_redirects=False), app, path


def base_data(**server):
    return {"server": server, "roots": [{"name": "r", "path": "."}],
            "users": [{"name": "u", "password_hash": "scrypt$1$2$3$4$5"}]}


# ---- card 4: the config file may not sit inside anything that is served ---------------------------------------------------------------
def test_card4_a_config_file_inside_a_library_is_refused_also_in_book_server_mode(tmp_path):
    (tmp_path / "books").mkdir()
    path = make_config(tmp_path, "show_all_files = false\n", where=tmp_path / "books" / "config.toml")
    with pytest.raises(ConfigError, match="inside root 'books'"):
        load_config(str(path))


def test_card4_the_editors_refuse_a_library_that_holds_the_config_file(tmp_path):
    path = make_config(tmp_path)
    before = path.read_text(encoding="utf-8")
    for folder in (tmp_path / "etc", tmp_path):                          # the config folder itself, and its parent
        with pytest.raises(EditError, match="inside root 'own'"):
            libedit.add_root(str(path), "own", str(folder))
    assert path.read_text(encoding="utf-8") == before and not os.path.exists(str(path) + ".bak")
    load_config(str(path))                                              # the file on disk still starts the server


def test_card4_the_admin_text_save_runs_the_same_overlap_checks(tmp_path):
    path = make_config(tmp_path)
    model = AdminModel(str(path))
    text = model.read_text()
    with pytest.raises(EditError, match="inside root 'own'"):
        model.save_text(text + '\n[[roots]]\nname = "own"\npath = "%s"\n' % slashes(tmp_path / "etc"))
    with pytest.raises(EditError, match="inside uploads_dir"):
        model.save_text(text.replace('uploads_dir = "%s"' % slashes(tmp_path / "up"), 'uploads_dir = "%s"' % slashes(tmp_path / "etc")))
    with pytest.raises(EditError, match="inside root"):
        model.check_text(text + '\n[[roots]]\nname = "own"\npath = "%s"\n' % slashes(tmp_path / "etc"))
    assert path.read_text(encoding="utf-8") == text
    model.save_text(text + "# a harmless comment\n")                    # an ordinary edit still saves


def test_card4_the_admin_page_refuses_the_config_folder_as_a_library(admin, tmp_path):
    client, app, path = admin
    r = client.post("/libraries/add", content="name=own&path=%s" % slashes(tmp_path / "etc"), headers={**BOSS, **FORM})
    assert r.status_code == 400 and "inside root" in r.text
    assert "own" not in load_config(str(path)).roots and "own" not in app.state.library.cfg.roots


# ---- card 7: user changes made on the admin page are in force at once ------------------------------------------------------------------
def test_card7_a_password_change_on_the_admin_page_reaches_the_login_table_without_a_site_request(admin):
    client, app, path = admin
    auth = app.state.auth
    old = basic("alice", "alice-password-1")["Authorization"]
    assert auth.authenticate(old) is not None and auth.cached_user(old) is not None
    r = client.post("/users/password", content="name=alice&password=new-password-22&again=new-password-22", headers={**BOSS, **FORM})
    assert r.status_code == 303
    assert auth.cached_user(old) is None and auth.trusted_user(old) is None          # the remembered login is gone
    assert authmod.verify_password("new-password-22", auth.users["alice"].password_hash)
    assert auth.authenticate(old) is None


def test_card7_a_removed_user_is_gone_from_the_login_table_at_once(admin):
    client, app, path = admin
    assert client.post("/users/remove", content="name=alice", headers={**BOSS, **FORM}).status_code == 303
    assert "alice" not in app.state.auth.users
    assert client.post("/users/add", content="name=dora&password=dora-password-1&again=dora-password-1", headers={**BOSS, **FORM}).status_code == 303
    assert "dora" in app.state.auth.users


# ---- card 25: the backup does not hold the state lock; prefs change under it; session look-ups stay off the disk --------------------------
def test_card25_a_backup_runs_while_the_main_connection_is_busy(tmp_path):
    s = StateDB(str(tmp_path / "data"))
    s.put_progress("alice", "books", "a.epub", "p:3", 0.5)
    made = []
    with s._lock:                                                       # a reader or writer on the main connection
        worker = threading.Thread(target=lambda: made.append(s.backup(str(tmp_path / "backups"))), daemon=True)
        worker.start()
        worker.join(20)
        finished = not worker.is_alive()
    worker.join(20)
    assert finished, "the backup waited for the lock of the main connection"
    copy = sqlite3.connect(made[0])
    try:
        assert copy.execute("SELECT locator FROM progress").fetchall() == [("p:3",)]
    finally:
        copy.close()
    s.close()


def test_card25_update_prefs_reads_changes_and_writes_under_one_lock():
    s = StateDB()
    s.set_prefs("alice", {"theme": "dark"})
    held = []

    def change(prefs):
        held.append(s._lock.locked())
        prefs["view"] = "covers"
        return prefs

    assert s.update_prefs("alice", change) == {"theme": "dark", "view": "covers"}
    assert held == [True] and s.get_prefs("alice") == {"theme": "dark", "view": "covers"}
    assert s.update_prefs("alice", lambda p: {"theme": "light"}) == {"theme": "light"} and s.get_prefs("alice") == {"theme": "light"}
    s.update_prefs("bob", lambda p: p.update(view="list"))              # a function that changes in place and returns nothing
    assert s.get_prefs("bob") == {"view": "list"}


class Counting:
    """Counts the session reads that reach the database."""
    def __init__(self, state, monkeypatch):
        self.calls = 0
        real = state.get_session

        def get_session(*args, **kwargs):
            self.calls += 1
            return real(*args, **kwargs)

        monkeypatch.setattr(state, "get_session", get_session)


def users_of(**passwords):
    return {name: configmod.User(name, "scrypt$" + pw) for name, pw in passwords.items()}


def test_card25_a_session_look_up_is_answered_from_memory_the_second_time(monkeypatch):
    state = StateDB()
    sessions = SessionManager(state, days=30)
    users = users_of(alice="1", bob="2")
    token = sessions.start(users["alice"])
    reads = Counting(state, monkeypatch)
    assert sessions.user_for(token, users).name == "alice" and reads.calls == 1
    for _ in range(5):
        assert sessions.user_for(token, users).name == "alice"
    assert reads.calls == 1
    assert sessions.user_for("not-a-token", users) is None


def test_card25_the_session_cache_honours_logout_removal_password_change_and_expiry_at_once(monkeypatch):
    state = StateDB()
    sessions = SessionManager(state, days=30)
    users = users_of(alice="1", bob="2")

    def fresh(name="alice"):
        token = sessions.start(users[name])
        assert sessions.user_for(token, users).name == name            # now cached
        return token

    token = fresh()
    sessions.end(token)                                                 # logout
    assert sessions.user_for(token, users) is None
    token = fresh()
    state.delete_user_sessions("alice")                                 # "sign out everywhere" on the admin page goes straight to the state
    assert sessions.user_for(token, users) is None
    token = fresh()
    assert sessions.user_for(token, users_of(alice="changed", bob="2")) is None      # password change
    assert state.get_session(hash_token(token)) is None                 # ... and the row is gone, as before
    token = fresh()
    assert sessions.user_for(token, users_of(bob="2")) is None          # user removed
    token = fresh()
    state.purge_user("alice")                                           # the reload saw the name disappear
    assert sessions.user_for(token, users) is None
    token, other = fresh(), fresh("bob")
    import time
    assert sessions.user_for(token, users, now=time.time() + 31 * 86400) is None    # expired, although cached
    state.delete_session(hash_token(other))
    assert sessions.user_for(other, users) is None


# ---- card 26: only a damaged database is set aside ------------------------------------------------------------------------------------
def test_card26_a_locked_database_stops_the_start_and_stays_where_it_is(tmp_path, monkeypatch):
    d = tmp_path / "data"
    s = StateDB(str(d))
    s.put_progress("alice", "books", "a.epub", "p:3", 0.5)
    s.close()
    real = sqlite3.connect

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(dbutil.sqlite3, "connect", locked)
    with pytest.raises(dbutil.DatabaseUnusable, match="state.sqlite3") as err:
        StateDB(str(d))
    assert "database is locked" in str(err.value) and "left where it is" in str(err.value)
    monkeypatch.setattr(dbutil.sqlite3, "connect", real)
    assert not any(".corrupt-" in n for n in os.listdir(d))
    again = StateDB(str(d))
    assert again.get_progress("alice", "books", "a.epub")["locator"] == "p:3"
    again.close()


def test_card26_a_full_disk_is_not_damage_either(tmp_path, monkeypatch):
    d = tmp_path / "data"
    StateDB(str(d)).close()
    real = sqlite3.connect

    class Full:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, *args):
            if "journal_mode" in sql:
                raise sqlite3.OperationalError("database or disk is full")
            return self.db.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self.db, name)

    monkeypatch.setattr(dbutil.sqlite3, "connect", lambda *a, **k: Full(real(*a, **k)))
    with pytest.raises(dbutil.DatabaseUnusable, match="disk is full"):
        StateDB(str(d))
    assert not any(".corrupt-" in n for n in os.listdir(d))


def test_card26_a_file_that_is_not_a_database_is_still_set_aside(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "state.sqlite3").write_bytes(b"this is not a database at all, " * 200)
    s = StateDB(str(d))
    s.put_progress("alice", "books", "a.epub", "p:1", 0.1)
    s.close()
    assert any(n.startswith("state.sqlite3.corrupt-") for n in os.listdir(d))


def test_card26_the_command_line_says_it_in_one_line(tmp_path, monkeypatch, capsys):
    path = make_config(tmp_path)
    cfg = load_config(str(path))

    def refuse(*args, **kwargs):
        raise dbutil.DatabaseUnusable("the database X cannot be opened: database is locked")

    import pluteus.app
    monkeypatch.setattr(pluteus.app, "create_app", refuse)
    assert cli._run_server(cfg, str(path)) == 2
    assert "Error: the database X cannot be opened" in capsys.readouterr().err


# ---- card 34: one lock around read, edit and save ----------------------------------------------------------------------------------------
def test_card34_every_config_edit_holds_the_edit_lock_from_read_to_save(tmp_path, monkeypatch):
    path = str(make_config(tmp_path))
    seen = []
    real_read, real_save = useredit.read_config_text, useredit._save

    def free_for_another_thread() -> bool:
        got = []

        def probe():
            got.append(useredit.EDIT_LOCK.acquire(blocking=False))
            if got[0]:
                useredit.EDIT_LOCK.release()                             # a re-entrant lock is released by the thread that took it

        t = threading.Thread(target=probe)
        t.start()
        t.join()
        return got[0]

    def read(p):
        seen.append(("read", free_for_another_thread()))
        return real_read(p)

    def save(p, text):
        seen.append(("save", free_for_another_thread()))
        return real_save(p, text)

    monkeypatch.setattr(useredit, "read_config_text", read)
    monkeypatch.setattr(libedit, "read_config_text", read)
    monkeypatch.setattr(useredit, "_save", save)
    (tmp_path / "films").mkdir()
    model = AdminModel(path)
    edits = [lambda: useredit.add_user(path, "dora", authmod.hash_password("dora-password-1", **FAST)),
             lambda: useredit.set_user(path, "dora", adult=True),
             lambda: useredit.remove_user(path, "dora"),
             lambda: libedit.add_root(path, "films", str(tmp_path / "films")),
             lambda: libedit.set_subsonic(path, {"books"}, True),
             lambda: libedit.remove_root(path, "films"),
             lambda: model.save_text(real_read(path) + "# note\n")]
    for edit in edits:
        seen.clear()
        edit()
        assert seen and seen[-1][0] == "save", seen
        assert not any(free for _, free in seen), "the edit lock was not held: %r" % seen


# ---- card 35: an admin server that cannot start says so ------------------------------------------------------------------------------------
def test_card35_a_taken_admin_port_is_reported_not_announced(tmp_path):
    import dataclasses
    path = make_config(tmp_path)
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        cfg = dataclasses.replace(load_config(str(path)), admin_port=port, admin_host="127.0.0.1")
        server = start_admin_server(create_app(cfg, config_path=str(path)))
        try:
            assert server.started is False
            assert str(port) in server.problem
        finally:
            server.stop()


def test_card35_the_console_does_not_claim_an_admin_page_that_is_not_there(tmp_path, monkeypatch, capsys):
    import uvicorn

    import pluteus.adminapp
    path = make_config(tmp_path)
    cfg = load_config(str(path))
    opened = []

    class Dead:
        started, problem = False, "the admin page could not start on 127.0.0.1 port 8101 (is the port in use by another program?)"

        def stop(self):
            pass

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr(pluteus.adminapp, "start_admin_server", lambda app: Dead())
    assert cli._run_server(cfg, str(path), on_admin=lambda: opened.append(1)) == 0
    err = capsys.readouterr().err
    assert "Admin page on" not in err and "port 8101" in err and "Error" in err and opened == []

    class Alive(Dead):
        started, problem = True, ""

    monkeypatch.setattr(pluteus.adminapp, "start_admin_server", lambda app: Alive())
    assert cli._run_server(cfg, str(path), on_admin=lambda: opened.append(1)) == 0
    assert "Admin page on http://127.0.0.1:8101/" in capsys.readouterr().err and opened == [1]


def feed(answers):
    items = iter(answers)
    return lambda prompt="": next(items)


def test_card35_first_run_opens_the_browser_only_once_the_admin_page_answers(tmp_path, monkeypatch):
    import webbrowser
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    opened, timers, was_opened = [], [], threading.Event()
    monkeypatch.setattr(webbrowser, "open", lambda url: (opened.append(url), was_opened.set()))

    class NoTimer:
        def __init__(self, *args, **kwargs):
            timers.append(args)

        def start(self):
            pass

    monkeypatch.setattr(threading, "Timer", NoTimer)

    def dead_admin(cfg, path, on_admin=None):
        return 0                                                        # the admin page did not start: on_admin is never called

    def live_admin(cfg, path, on_admin=None):
        on_admin()
        return 0

    for number, (run, expected) in enumerate(((dead_admin, 0), (live_admin, 1))):
        opened.clear()
        path = str(tmp_path / ("home%d" % number) / "config.toml")
        code = firstrun.start(path, run, read=feed(["boss", ""]), read_password=feed(["long-enough-1", "long-enough-1"]),
                              say=lambda *a: None, open_browser=True)
        if expected:
            assert was_opened.wait(10)                                  # the browser is opened from a thread of its own
        assert code == 0 and len(opened) == expected and timers == []
    assert opened[0].endswith("/libraries")


# ---- card 36: backups, the supervisor, quick share ---------------------------------------------------------------------------------------
def test_card36_a_backup_that_fails_midway_leaves_nothing_that_counts_as_a_backup(tmp_path, monkeypatch):
    import time
    s = StateDB(str(tmp_path / "data"))
    backups = str(tmp_path / "data" / "backups")
    real = StateDB._copy_to

    def half(self, target):
        with open(target, "wb") as f:
            f.write(b"SQLite format 3\x00 and then the disk was full")
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(StateDB, "_copy_to", half)
    now = time.time()
    s.housekeeping(backups, now=now)                                    # logs the failure, raises nothing
    assert os.listdir(backups) == []
    with pytest.raises(sqlite3.OperationalError):
        s.backup(backups, now=now)
    assert os.listdir(backups) == []
    monkeypatch.setattr(StateDB, "_copy_to", real)
    s.housekeeping(backups, now=now + 60)                               # so the next round tries again at once
    names = os.listdir(backups)
    assert len(names) == 1 and names[0].startswith("state-") and names[0].endswith(".sqlite3")
    s.close()


def test_card36_a_leftover_partial_file_is_neither_the_newest_backup_nor_kept(tmp_path):
    import time
    s = StateDB(str(tmp_path / "data"))
    backups = tmp_path / "data" / "backups"
    backups.mkdir()
    (backups / "state-29991231-235959.sqlite3.tmp").write_bytes(b"half")          # from a server killed mid-backup
    s.housekeeping(str(backups), now=time.time())
    names = sorted(os.listdir(backups))
    assert len(names) == 1 and names[0].endswith(".sqlite3")
    s.close()


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_card36_the_supervisor_backs_off_after_fast_exits_and_starts_over_after_a_long_run(tmp_path, monkeypatch):
    assert [supervise.backoff_delay(n, 10.0) for n in range(1, 7)] == [10, 30, 60, 300, 300, 300]
    clock, slept = FakeClock(), []
    lengths = iter([1, 1, 1, 1, 1, 700, 1, 1])                         # seconds each run lasts

    class Child:
        def __init__(self, command, **kwargs):
            pass

        def wait(self):
            clock.now += next(lengths)
            return 1

    monkeypatch.setattr(supervise.subprocess, "Popen", Child)
    log = tmp_path / "service.log"
    supervise.run("x.toml", str(log), delay=10.0, max_runs=8, sleep=slept.append, clock=clock)
    assert slept == [10, 30, 60, 300, 300, 10, 10]                      # the 700-second run resets the count
    assert "starting again in 300 seconds" in log.read_text(encoding="utf-8")


def test_card36_the_supervisor_rotates_its_log_by_size(tmp_path, monkeypatch):
    class Child:
        def __init__(self, command, **kwargs):
            pass

        def wait(self):
            return 0

    monkeypatch.setattr(supervise.subprocess, "Popen", Child)
    log = tmp_path / "service.log"
    log.write_bytes(b"old line\n" * 100)
    supervise.run("x.toml", str(log), delay=0, max_runs=1, max_log_bytes=500)
    assert (tmp_path / "service.log.1").read_bytes() == b"old line\n" * 100
    assert b"old line" not in log.read_bytes() and b"starting pluteus" in log.read_bytes()
    small = tmp_path / "small.log"
    small.write_bytes(b"old line\n")
    supervise.run("x.toml", str(small), delay=0, max_runs=1, max_log_bytes=500)
    assert small.read_bytes().startswith(b"old line\n") and not (tmp_path / "small.log.1").exists()


def test_card36_quick_share_closes_its_databases_so_the_temp_folder_can_go(tmp_path):
    shared, data = tmp_path / "shared", tmp_path / "share-data"
    shared.mkdir()
    (shared / "a.txt").write_text("x")
    cfg = share.build_config([str(shared)], port=8080, data_dir=str(data))
    app = create_app(cfg)
    TestClient(app).get("/")
    assert os.path.exists(data / "state.sqlite3")
    share.close_databases(str(data))
    shutil.rmtree(str(data))                                            # raises on Windows while a database is open
    assert not data.exists()
    assert app.state.state is not None


# ---- card 59: a typed library path is confined like the picker ---------------------------------------------------------------------------
def test_card59_a_typed_library_path_must_be_inside_library_bases(tmp_path):
    base, outside = tmp_path / "mounted", tmp_path / "elsewhere"
    (base / "music").mkdir(parents=True)
    outside.mkdir()
    bases = (str(base),)
    assert libedit.check_folder(str(base / "music"), bases) == str(base / "music")
    assert libedit.check_folder(str(base), bases) == str(base)
    for bad in (str(outside), str(base / "music" / ".." / ".." / "elsewhere")):
        with pytest.raises(EditError, match="library_bases"):
            libedit.check_folder(bad, bases)
    assert libedit.check_folder(str(outside)) == str(outside)           # no bases: anywhere but a filesystem root


def test_card59_without_bases_a_filesystem_root_is_refused(tmp_path, monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("the root was listed")

    monkeypatch.setattr(libedit.os, "scandir", never)
    with pytest.raises(EditError, match="whole"):
        libedit.check_folder(tmp_path.anchor)


def test_card59_the_admin_page_refuses_a_typed_path_outside_the_bases(tmp_path):
    (tmp_path / "mounted" / "music").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    path = make_config(tmp_path, 'library_bases = ["%s"]\n' % slashes(tmp_path / "mounted"))
    app = create_app(load_config(str(path)), config_path=str(path), reload_interval=0.0)
    client = TestClient(create_admin_app(app), follow_redirects=False)
    r = client.post("/libraries/add", content="name=other&path=%s" % slashes(tmp_path / "elsewhere"), headers={**BOSS, **FORM})
    assert r.status_code == 400 and "library_bases" in r.text and "other" not in load_config(str(path)).roots
    r = client.post("/libraries/add", content="name=music&path=%s" % slashes(tmp_path / "mounted" / "music"), headers={**BOSS, **FORM})
    assert r.status_code == 303 and "music" in load_config(str(path)).roots


# ---- card 63: a quoted boolean is an error, not "true" ------------------------------------------------------------------------------------
@pytest.mark.parametrize("key,value", [("admin", "false"), ("adult", "no"), ("upload", 0), ("notes", "false")])
def test_card63_user_flags_must_be_real_booleans(key, value):
    data = base_data()
    data["users"][0][key] = value
    with pytest.raises(ConfigError, match="%s must be true or false" % key):
        parse_config(data, check_roots=False)


@pytest.mark.parametrize("key", ["trust_proxy", "webdav", "opds_pse", "show_all_files", "cast", "relay", "debug", "share_target", "subsonic",
                                 "subsonic_uploads", "kosync", "thumbnails", "update_check", "metadata"])
def test_card63_server_flags_must_be_real_booleans(key):
    with pytest.raises(ConfigError, match="%s must be true or false" % key):
        parse_config(base_data(**{key: "false"}), check_roots=False)
    assert getattr(parse_config(base_data(**{key: False}), check_roots=False), key) is False
    assert getattr(parse_config(base_data(**{key: True}), check_roots=False), key) is True


def test_card63_a_root_flag_too_and_cookie_secure_keeps_its_words():
    data = base_data()
    data["roots"][0]["subsonic"] = "false"
    with pytest.raises(ConfigError, match="subsonic must be true or false"):
        parse_config(data, check_roots=False)
    assert parse_config(base_data(cookie_secure="always"), check_roots=False).cookie_secure == "always"
    assert parse_config(base_data(cookie_secure=False), check_roots=False).cookie_secure == "never"


# ---- card 64: first run never writes a configuration the next start refuses ----------------------------------------------------------------
def test_card64_the_administrator_name_follows_the_upload_folder_rule_up_front():
    said = []
    name, password, folder = firstrun.ask_setup(feed(["Bjørn", "ole.", "Nul", "a:b", "Erlend B", ""]),
                                                feed(["long-enough-1", "long-enough-1"]), said.append)
    assert name == "Erlend B"
    assert sum("letters" in s for s in said) == 4


def test_card64_a_configuration_that_would_not_load_is_not_written(tmp_path):
    (tmp_path / "library").mkdir()
    home = tmp_path / "library" / "settings"
    path = home / "config.toml"
    with pytest.raises(ValueError, match="inside root"):
        firstrun.create_config(str(path), "admin", "long-enough-1", str(tmp_path / "library"))     # the library would hold the config
    assert not path.exists()
    with pytest.raises(ValueError):
        firstrun.create_config(str(path), "Bjørn", "long-enough-1", "")
    assert not path.exists()


def test_card64_first_run_asks_for_the_folder_again_instead_of_leaving_a_dead_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    (tmp_path / "library").mkdir()
    (tmp_path / "books").mkdir()
    path = str(tmp_path / "library" / "settings" / "config.toml")
    started, said = [], []
    code = firstrun.start(path, lambda cfg, p: started.append(cfg) or 0, read=feed(["boss", str(tmp_path / "library"), str(tmp_path / "books")]),
                          read_password=feed(["long-enough-1", "long-enough-1"]), say=said.append, open_browser=False)
    assert code == 0 and started[0].roots["library"].path == str(tmp_path / "books")
    assert any("inside root" in s for s in said)
    load_config(path)


# ---- card 66: a re-created user starts empty -------------------------------------------------------------------------------------------------
def fill(state, name):
    state.create_session("hash-" + name, name, "fp", 3600)
    state.remember_device("device-" + name, name, "fp", "10.0.0.1")
    state.put_progress(name, "books", "a.epub", "p:3", 0.5)
    state.dismiss_progress(name, "books", "b.epub")
    state.set_prefs(name, {"theme": "dark"})
    state.playqueue_put(name, {"entries": []})
    state.set_appkey(name, "secret-" + name)
    state.stream_put(name, "Sub", b"blob", "key")
    state.shelf_add(name, "favourite", "books", "a.epub", False)
    state.add_bookmark(name, "books", "a.mp3", 12.0, "here")
    state.put_kosync(name, "doc", "p", 0.3, "kindle", "id")


USER_TABLES = ("sessions", "devices", "progress", "dismissed", "prefs", "playqueue", "appkeys", "streams", "shelves", "bookmarks", "kosync")


def rows(state, name):
    return {t: state._one("SELECT COUNT(*) FROM %s WHERE username=?" % t, (name,))[0] for t in USER_TABLES}


def test_card66_purge_user_empties_every_per_user_table_for_that_name_only():
    s = StateDB()
    fill(s, "alice")
    fill(s, "bob")
    assert all(n == 1 for n in rows(s, "alice").values())
    s.purge_user("alice")
    assert all(n == 0 for n in rows(s, "alice").values()), rows(s, "alice")
    assert all(n == 1 for n in rows(s, "bob").values())


def test_card66_a_reload_that_sees_a_name_disappear_reports_it(tmp_path):
    path = make_config(tmp_path)
    auth = authmod.Authenticator(load_config(str(path)).users)
    gone, configs = [], []
    reloader = UsersReloader(str(path), auth, interval=0.0, on_config=configs.append, on_removed=gone.append)
    assert reloader.maybe_reload() is False and gone == []
    useredit.remove_user(str(path), "alice")
    assert reloader.maybe_reload() is True
    assert gone == ["alice"] and "alice" not in auth.users and len(configs) == 1
    useredit.add_user(str(path), "alice", authmod.hash_password("another-password-1", **FAST))
    assert reloader.maybe_reload() is True and gone == ["alice"]


def test_card66_the_reload_reports_a_removal_the_admin_page_already_applied(tmp_path):
    path = make_config(tmp_path)
    auth = authmod.Authenticator(load_config(str(path)).users)
    gone = []
    reloader = UsersReloader(str(path), auth, interval=0.0, on_removed=gone.append)
    auth.set_users(useredit.remove_user(str(path), "alice").users)       # what the admin page does (card 7)
    assert reloader.maybe_reload() is True and gone == ["alice"]


def test_card66_removing_a_user_on_the_admin_page_purges_their_state(admin):
    client, app, path = admin
    state = app.state.state
    fill(state, "alice")
    assert client.post("/users/remove", content="name=alice", headers={**BOSS, **FORM}).status_code == 303
    assert all(n == 0 for n in rows(state, "alice").values()), rows(state, "alice")


# ---- card 67: comments above the next table survive a removed user ---------------------------------------------------------------------------
SAMPLE = """# top
[[roots]]
name = "books"
path = "/b"

[[users]]
name = "alice"
password_hash = "scrypt$a"

# Bob reads comics only.
# (ask before changing)
[[users]]
name = "bob"
password_hash = "scrypt$b"

# The devices in the living room.
[[cast_devices]]
name = "TV"
address = "192.168.1.50"
"""


def test_card67_removing_a_user_keeps_the_comments_of_the_next_table():
    new = useredit.remove_user_text(SAMPLE, "alice")
    assert "alice" not in new and "# Bob reads comics only.\n# (ask before changing)\n[[users]]\nname = \"bob\"" in new
    new = useredit.remove_user_text(SAMPLE, "bob")
    assert "bob" not in new.replace("# Bob reads comics only.", "") and "# The devices in the living room.\n[[cast_devices]]" in new
    assert "\n\n\n" not in new
    assert useredit.remove_user_text(useredit.remove_user_text(SAMPLE, "alice"), "bob").count("[[users]]") == 0


def test_card67_users_and_libraries_share_one_helper():
    assert libedit._cut_block is useredit._cut_block
    assert libedit.remove_root_text(SAMPLE, "books").startswith("# top\n[[users]]") or "[[roots]]" not in libedit.remove_root_text(SAMPLE, "books")


# ---- card 69: permissions and durability of the config file ------------------------------------------------------------------------------------
def test_card69_the_backup_takes_the_permissions_of_the_config_and_the_new_file_is_synced(tmp_path, monkeypatch):
    path = str(make_config(tmp_path))
    modes, synced = [], []
    real_copymode, real_fsync = shutil.copymode, os.fsync
    monkeypatch.setattr(useredit.shutil, "copymode", lambda src, dst, **k: (modes.append((src, dst)), real_copymode(src, dst, **k))[1])
    monkeypatch.setattr(useredit.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])
    useredit.set_user(path, "alice", adult=True)
    assert (path, path + ".bak") in modes
    assert len(synced) == 1
    assert load_config(path).users["alice"].adult is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_card69_a_private_config_gets_a_private_backup(tmp_path):
    path = str(make_config(tmp_path))
    os.chmod(path, 0o600)
    useredit.set_user(path, "alice", adult=True)
    assert os.stat(path + ".bak").st_mode & 0o777 == 0o600 and os.stat(path).st_mode & 0o777 == 0o600


def test_card69_the_first_configuration_is_created_owner_only_and_synced(tmp_path, monkeypatch):
    path = str(tmp_path / "home" / "config.toml")
    opened, synced = [], []
    real_open, real_fsync = os.open, os.fsync

    def recording_open(p, flags, mode=0o777, **kwargs):
        opened.append((str(p), mode))
        return real_open(p, flags, mode, **kwargs)

    monkeypatch.setattr(firstrun.os, "open", recording_open)
    monkeypatch.setattr(firstrun.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])
    firstrun.create_config(path, "admin", "long-enough-1", "")
    assert (path, 0o600) in opened and len(synced) == 1
    if os.name != "nt":
        assert os.stat(path).st_mode & 0o777 == 0o600
    load_config(path, check_roots=False)


# ---- card 74 (c): a sealed entry belongs to one owner and one name -----------------------------------------------------------------------------
KEY = bytes(range(32))
ENTRY = {"auth": {"type": "basic", "user": "me", "password": "s3cret"}, "headers": {}, "hosts": ["stream.example.org"]}


def test_card74_a_sealed_entry_cannot_be_moved_to_another_user_or_name():
    v = vault.Vault(KEY)
    blob = v.seal(ENTRY, "alice", "My subscription")
    assert v.open(blob, "alice", "My subscription") == ENTRY
    for owner, name in (("bob", "My subscription"), ("alice", "Other"), ("alic", "eMy subscription"), ("alice|My", "subscription")):
        with pytest.raises(vault.VaultError):
            v.open(blob, owner, name)
    with pytest.raises(vault.VaultError):
        v.open(blob)                                                    # nor read as an entry of the old form
    assert v.is_bound(blob, "alice", "My subscription") and not v.is_bound(blob, "bob", "My subscription")


def test_card74_entries_sealed_the_old_way_still_open_and_are_bound_by_the_next_write():
    v = vault.Vault(KEY)
    old = v.seal(ENTRY)                                                 # the form every stored entry has today
    assert v.open(old) == ENTRY
    assert v.open(old, "alice", "My subscription") == ENTRY             # accepted on read
    assert not v.is_bound(old, "alice", "My subscription")
    new = v.seal(v.open(old, "alice", "My subscription"), "alice", "My subscription")
    assert v.is_bound(new, "alice", "My subscription") and v.open(new, "alice", "My subscription") == ENTRY
    with pytest.raises(vault.VaultError):
        vault.Vault(bytes(reversed(KEY))).open(old, "alice", "My subscription")
    bad = bytearray(new)
    bad[20] ^= 1
    with pytest.raises(vault.VaultError):
        v.open(bytes(bad), "alice", "My subscription")


def test_card74_open_stored_reseals_an_old_entry_in_the_state():
    v, s = vault.Vault(KEY), StateDB()
    s.stream_put("alice", "Sub", v.seal(ENTRY), v.key_id)
    assert vault.open_stored(v, s, "alice", "Sub") == ENTRY
    blob, key_id = s.stream_get("alice", "Sub")
    assert v.is_bound(blob, "alice", "Sub") and key_id == v.key_id
    assert vault.open_stored(v, s, "alice", "Sub") == ENTRY and vault.open_stored(v, s, "alice", "Missing") is None
    s.stream_put("bob", "Sub", blob, v.key_id)                          # someone with write access to the database moves it
    with pytest.raises(vault.VaultError):
        vault.open_stored(v, s, "bob", "Sub")

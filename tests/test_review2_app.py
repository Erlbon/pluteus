"""Regression tests for the 2026-11-12 review (docs/code-review-2026-11-12.md): the cards that live in app.py, auth.py and
ratelimit.py. Each was seen to fail before its fix."""
import asyncio
import dataclasses
import logging
import os

import httpx
from conftest import basic
from test_login_web import HTML, login, make, post

from pluteus import app as app_module
from pluteus.fs import Unavailable
from pluteus.ratelimit import FailureLimiter

ORIGIN = {"origin": "http://testserver"}


def burst(app, requests):
    """Send `requests` ((method, url, kwargs) each) at the same moment; the status codes, in order."""
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            answers = await asyncio.gather(*(client.request(m, u, **kw) for m, u, kw in requests))
            return [a.status_code for a in answers]
    return asyncio.run(run())


# ---- card 1: a parallel burst of guesses -------------------------------------------------------------------------
def test_a_burst_of_basic_guesses_is_stopped_at_the_limit(cfg, tmp_path):
    _, app = make(cfg, tmp_path)
    codes = burst(app, [("GET", "/opds", {"headers": basic("alice", "guess-%d" % i)}) for i in range(40)])
    assert codes.count(401) == 5, codes                    # five guesses were looked at ...
    assert codes.count(429) == 35, codes                   # ... and every other one was turned away unhashed


def test_a_burst_of_guesses_on_the_login_form_is_stopped_at_the_limit(cfg, tmp_path):
    _, app = make(cfg, tmp_path)
    form = {"content-type": "application/x-www-form-urlencoded", **ORIGIN}
    codes = burst(app, [("POST", "/login", {"content": "username=alice&password=guess-%d" % i, "headers": form}) for i in range(40)])
    assert codes.count(401) == 5 and codes.count(429) == 35, codes


def test_a_reader_app_with_one_stale_password_on_many_connections_does_not_lock_its_user(cfg, tmp_path):
    client, app = make(cfg, tmp_path)
    codes = burst(app, [("GET", "/opds", {"headers": basic("alice", "the-old-password")}) for _ in range(12)])
    assert set(codes) == {401}, codes
    assert app.state.limiter.snapshot() == [{"name": "alice", "locked_for": 0, "locks": 0, "recent_failures": 1}]
    assert client.get("/opds", headers=basic("alice", "pw-alice")).status_code == 200


def test_the_right_password_on_many_connections_at_once_is_let_in_on_all(cfg, tmp_path):
    _, app = make(cfg, tmp_path)
    codes = burst(app, [("GET", "/opds", {"headers": basic("alice", "pw-alice")}) for _ in range(12)])
    assert set(codes) == {200}, codes


# ---- cards 2 and 11: the app key and the lock -------------------------------------------------------------------
def lock(client, name="alice"):
    for i in range(5):
        assert client.get("/opds", headers=basic(name, "wrong-%d" % i)).status_code == 401
    assert client.get("/opds", headers=basic(name, "wrong-again")).status_code == 429


def test_an_app_key_works_while_its_name_is_locked_and_does_not_lift_the_lock(cfg, tmp_path):
    client, app = make(cfg, tmp_path)
    app.state.state.set_appkey("alice", "k" * 24)
    lock(client)
    assert client.get("/opds", headers=basic("alice", "k" * 24)).status_code == 200            # card 11: the owner's reader app
    assert client.get("/opds", headers=basic("alice", "one-more-guess")).status_code == 429    # card 2: the lock still stands
    assert app.state.limiter.snapshot()[0]["locks"] == 1
    assert client.get("/opds", headers=basic("alice", "pw-alice")).status_code == 429          # a stranger with the password waits too


def test_an_app_key_login_is_read_only_and_never_clears_failures(cfg, tmp_path):
    client, app = make(cfg, tmp_path)
    app.state.state.set_appkey("alice", "k" * 24)
    for i in range(3):
        client.get("/opds", headers=basic("alice", "wrong-%d" % i))
    assert client.get("/opds", headers=basic("alice", "k" * 24)).status_code == 200
    assert app.state.limiter.snapshot()[0]["recent_failures"] == 3
    r = client.put("/api/progress/books/Good%20Author/Real%20Book.epub", json={"locator": "x", "percent": 0.5},
                   headers={**basic("alice", "k" * 24), **ORIGIN})
    assert r.status_code == 403


# ---- card 68: the normal log -------------------------------------------------------------------------------------
def test_the_log_does_not_say_how_long_a_wrong_password_was(cfg, tmp_path, caplog):
    client, _ = make(cfg, tmp_path)
    with caplog.at_level(logging.WARNING, logger="pluteus"):
        client.get("/opds", headers=basic("alice", "1234567"))
    lines = [r.getMessage() for r in caplog.records if "login refused" in r.getMessage()]
    assert lines and "wrong password" in lines[0]
    assert "characters" not in lines[0] and "7" not in lines[0].split(":", 1)[1]


# ---- card 70: the access log -------------------------------------------------------------------------------------
def test_a_line_break_in_the_address_cannot_start_a_new_line_in_the_access_log():
    scope = {"path": '/healthz\n203.0.113.9 - "GET /settings" 200', "query_string": b"a=1\rb"}
    shown = app_module._logged_target(scope)
    assert "\n" not in shown and "\r" not in shown and '"' not in shown
    assert shown.startswith("/healthz%0A203.0.113.9") and shown.endswith("?a=1%0Db")
    assert app_module._logged_target({"path": "/browse/books/Bjørn og æøå", "query_string": b""}) == "/browse/books/Bjørn og æøå"


# ---- card 27: errors are told apart and logged -------------------------------------------------------------------
def test_a_storage_error_is_logged_and_only_an_offline_share_says_try_again(cfg, tmp_path, monkeypatch, caplog):
    client, app = make(cfg, tmp_path)
    lib = app.state.library
    url = "/dl/books/Good%20Author/Real%20Book.epub"

    def denied(*a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(lib, "file_info", denied)
    with caplog.at_level(logging.WARNING, logger="pluteus"):
        r = client.get(url, headers=basic("alice", "pw-alice"))
    assert r.status_code == 500 and "Retry-After" not in r.headers
    assert any("/dl/books" in rec.getMessage() and "Permission denied" in rec.getMessage() for rec in caplog.records)

    def away(*a, **k):
        raise Unavailable(5, "root 'books' is not reachable")
    monkeypatch.setattr(lib, "file_info", away)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="pluteus"):
        r = client.get(url, headers=basic("alice", "pw-alice"))
    assert r.status_code == 503 and r.headers["Retry-After"] == "30"
    assert any("not available" in rec.getMessage() for rec in caplog.records)


# ---- cards 23 and 33: downloads -----------------------------------------------------------------------------------
def test_a_downloaded_script_is_plain_bytes_and_a_book_keeps_its_type(cfg, tmp_path, library):
    (library / "comics" / "Plain" / "x.js").write_text("alert(1)", encoding="utf-8")
    (library / "comics" / "Plain" / "x.css").write_text("body{}", encoding="utf-8")
    client, _ = make(cfg, tmp_path, show_all_files=True)
    for name in ("x.js", "x.css"):
        r = client.get("/dl/comics/Plain/" + name, headers=basic("alice", "pw-alice"))
        assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream", name
        assert r.headers["content-disposition"].startswith("attachment")
    r = client.get("/dl/books/Good%20Author/Real%20Book.epub", headers=basic("alice", "pw-alice"))
    assert r.headers["content-type"] == "application/epub+zip"


def test_raw_and_dl_answer_304_also_for_a_weak_tag_or_a_list(cfg, tmp_path, library):
    picture = library / "comics" / "Plain" / "p.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    client, _ = make(cfg, tmp_path, show_all_files=True)
    me = basic("alice", "pw-alice")
    for number, url in enumerate(("/raw/comics/Plain/p.png", "/dl/comics/Plain/p.png")):
        first = client.get(url, headers=me)
        assert first.status_code == 200, url
        tag = first.headers["etag"]
        for sent in (tag, "W/" + tag, '"other", W/%s' % tag):
            again = client.get(url, headers={**me, "if-none-match": sent})
            assert again.status_code == 304 and again.content == b"", (url, sent)
        assert client.get(url, headers={**me, "if-none-match": '"something-else"'}).status_code == 200
        os.utime(picture, (1_600_000_000 + number, 1_600_000_000 + number))      # the file changed: the old tag no longer matches
        assert client.get(url, headers={**me, "if-none-match": tag}).status_code == 200


# ---- card 72: the shared anonymous user ----------------------------------------------------------------------------
def test_the_anonymous_user_cannot_change_settings_for_everyone(cfg, tmp_path):
    client, app = make(cfg, tmp_path, anonymous_user="carol")
    r = post(client, "/settings", {"theme": "ocean"})
    assert r.status_code == 403
    assert app.state.state.get_prefs("carol").get("theme") is None
    assert client.get("/settings", headers=HTML).status_code == 200                # reading them is fine
    signed_in, _ = make(cfg, tmp_path)
    assert login(signed_in).status_code == 303
    assert post(signed_in, "/settings", {"theme": "ocean"}).status_code == 303     # a real user still can


# ---- card 74: caches and tables -------------------------------------------------------------------------------------
def test_a_login_checked_while_the_users_were_reloaded_is_not_remembered(cfg, tmp_path):
    _, app = make(cfg, tmp_path)
    auth = app.state.auth
    header = basic("alice", "pw-alice")["Authorization"]
    before = auth._generation
    auth.forget_user("alice")                                       # "sign out everywhere" lands while the hash is being computed
    auth._remember("some-key", "alice", False, before)
    assert auth._cache == {} and auth._trusted == {}
    assert auth.authenticate(header).name == "alice" and auth.cached_user(header).name == "alice"      # a fresh check is remembered
    auth.set_users(dict(auth.users))
    assert auth.cached_user(header) is None


def test_invented_names_do_not_grow_the_lock_tables_without_end():
    limiter = FailureLimiter()
    now = 1_000_000.0
    for i in range(6000):
        for _ in range(5):
            limiter.failure("ghost-%d" % i, "", now=now)
        now += 400                                                  # each lock is over before the next name is tried
    assert len(limiter._locked) <= 5001 and len(limiter._strikes) <= 5001
    limiter.failure("alice", "", now=now + 90_000)                  # a day later the old counts are forgiven and gone
    for _ in range(5):
        limiter.failure("mallory", "", now=now + 90_000)
    assert limiter.wait_seconds("mallory", "", now=now + 90_001) > 0


# ---- card 24: waiting for a thumbnail holds no worker thread --------------------------------------------------------
def test_thumbnails_wait_for_their_turn_on_the_event_loop(cfg, tmp_path, library, monkeypatch):
    import threading
    import time

    from pluteus import thumbs
    for i in range(12):
        (library / "comics" / "Plain" / ("p%02d.png" % i)).write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([i]) * 64)
    _, app = make(cfg, tmp_path, show_all_files=True)
    busy, most = [0], [0]
    guard = threading.Lock()

    def slow_source(path):
        with guard:
            busy[0] += 1
            most[0] = max(most[0], busy[0])
        time.sleep(0.05)
        with guard:
            busy[0] -= 1
        return None
    monkeypatch.setattr(thumbs, "image_file", slow_source)
    codes = burst(app, [("GET", "/thumb/comics/Plain/p%02d.png" % i, {"headers": basic("alice", "pw-alice")}) for i in range(12)])
    assert set(codes) == {404}, codes                               # no picture in these files: "no cover", for each
    assert most[0] <= 3                                             # never more than three worker threads on thumbnails

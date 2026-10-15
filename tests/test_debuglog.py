"""Debug logging: off by default, switched on from the admin page (15 minutes) or kept on by debug = true, and it never records secrets."""
import base64
import logging

from starlette.testclient import TestClient
from test_adminweb import BOSS, FORM, make_config

from pluteus import __version__, debuglog
from pluteus.adminapp import create_admin_app
from pluteus.app import create_app
from pluteus.config import load_config

SECRET_PASSWORD = "boss-password-1"
SECRET_KEY = "KEY-VALUE-9f3a"


def pair(tmp_path, debug=False):
    path = make_config(tmp_path)
    if debug:
        path.write_text(path.read_text(encoding="utf-8").replace("[server]\n", "[server]\ndebug = true\n", 1), encoding="utf-8", newline="")
    app = create_app(load_config(str(path)), config_path=str(path), reload_interval=0.0)
    return TestClient(create_admin_app(app), follow_redirects=False), TestClient(app, follow_redirects=False), app


def basic(name, password):
    return {"Authorization": "Basic " + base64.b64encode(("%s:%s" % (name, password)).encode()).decode("ascii")}


def test_the_switch_is_a_timer_that_ends_by_itself():
    now = [100.0]
    d = debuglog.DebugLog(clock=lambda: now[0])
    assert not d.active() and d.remaining() == 0
    d.record({"method": "GET", "target": "/x", "status": 200})
    assert d.lines() == []                                                       # off: nothing is kept
    assert d.enable() == 15 and d.active() and d.remaining() == 900
    d.record({"method": "GET", "target": "/x", "status": 200, "client": "1.2.3.4"})
    now[0] += 901
    assert not d.active()
    d.record({"method": "GET", "target": "/late", "status": 200})
    assert [t for _, t in d.lines()] == ["GET /x -> 200; from 1.2.3.4"]
    d.enable(500)
    assert d.remaining() == 3600                                                 # at most an hour
    d.disable()
    assert not d.active()


def test_the_config_keeps_it_on_without_a_timer_or_a_switch():
    d = debuglog.DebugLog(always=True)
    assert d.active() and d.remaining() == 0
    d.disable()
    assert d.active() and d.enable() == 0 and d.active()


def test_it_is_off_until_switched_on_and_the_admin_page_says_so(tmp_path):
    admin, site, app = pair(tmp_path)
    site.get("/opds", headers=basic("boss", SECRET_PASSWORD))
    page = admin.get("/debug", headers=BOSS).text
    assert "Switch on for 15 minutes" in page and "Nothing recorded" not in page.replace("Switch it on", "") or "Switch it on" in page
    assert app.state.debuglog.lines() == []
    r = admin.post("/debug/on", headers={**BOSS, **FORM})
    assert r.status_code == 303 and r.headers["location"] == "/debug?ok=debugon"
    assert "switches itself off in" in admin.get("/debug", headers=BOSS).text


def test_the_lines_describe_the_request_and_never_hold_a_secret(tmp_path, caplog):
    admin, site, app = pair(tmp_path)
    admin.post("/debug/on", headers={**BOSS, **FORM})
    with caplog.at_level(logging.INFO):
        site.get("/opds", headers={**basic("Boss", "wrong-password-99"), "User-Agent": "PocketBook/6.1", "X-Forwarded-For": "203.0.113.9"})
        site.get("/opds?apiKey=%s&q=hello" % SECRET_KEY, headers=basic("boss", SECRET_PASSWORD), cookies={"other": "COOKIE-VALUE-77"})
        site.get("/opds")
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("pluteus"))                # (the test client's own log line is not ours)
    text = "\n".join(t for _, t in app.state.debuglog.lines()) + ours
    assert SECRET_PASSWORD not in text and "wrong-password-99" not in text and SECRET_KEY not in text and "COOKIE-VALUE-77" not in text
    assert "hello" not in "".join(t for _, t in app.state.debuglog.lines())                # query values stay out of the debug lines
    assert "apiKey&q (names only)" in text and "PocketBook/6.1" in text and "forwarded: " not in text and "via: forwarded-for=203.0.113.9" in text
    assert "name 4 characters (matches a user: ignoring case and spaces), password 17 characters" in text
    assert "Refused: wrong password" in text and "accepted" in text.lower() and "-> 401" in text and "cookies: other" in text
    assert "debug GET /opds" in ours                                       # the same lines reach the ordinary log


def test_clear_and_download(tmp_path):
    admin, site, app = pair(tmp_path)
    admin.post("/debug/on", headers={**BOSS, **FORM})
    site.get("/healthz")
    text = admin.get("/debug/download", headers=BOSS)
    assert text.status_code == 200 and "attachment" in text.headers["content-disposition"]
    body = text.text
    assert "pluteus %s" % __version__ in body and "GET /healthz -> 200" in body and "libraries: books, comics" in body
    assert SECRET_PASSWORD not in body and str(tmp_path) not in body and "password_hash" not in body       # no paths, no secrets
    r = admin.post("/debug/clear", headers={**BOSS, **FORM})
    assert r.headers["location"] == "/debug?ok=debugcleared" and app.state.debuglog.lines() == []
    r = admin.post("/debug/off", headers={**BOSS, **FORM})
    assert r.headers["location"] == "/debug?ok=debugoff" and not app.state.debuglog.active()


def test_debug_true_in_the_config_is_on_from_the_start_and_cannot_be_switched_off(tmp_path):
    admin, site, app = pair(tmp_path, debug=True)
    site.get("/healthz")
    assert app.state.debuglog.active() and app.state.debuglog.lines()
    page = admin.get("/debug", headers=BOSS).text
    assert "debug = true" in page and "/debug/off" not in page and "/debug/on" not in page
    admin.post("/debug/off", headers={**BOSS, **FORM})
    assert app.state.debuglog.active()


def test_the_debug_pages_need_an_admin_and_a_same_site_post(tmp_path):
    admin, site, app = pair(tmp_path)
    assert admin.get("/debug").status_code == 401 and admin.get("/debug/download").status_code == 401
    assert admin.post("/debug/on", headers=FORM).status_code == 401
    assert admin.post("/debug/on", headers={**BOSS, "content-type": FORM["content-type"]}).status_code == 403          # no Origin
    assert not app.state.debuglog.active()
    assert site.get("/debug").status_code in (401, 404, 303)                                                        # not on the public site


def test_the_challenge_has_only_a_realm_and_the_debug_line_lists_the_other_header_names(tmp_path):
    admin, site, app = pair(tmp_path)
    admin.post("/debug/on", headers={**BOSS, **FORM})
    r = site.get("/opds", headers={"X-Odd-Login": "secret-value-1", "Accept-Language": "nb"})
    assert r.status_code == 401 and r.headers["www-authenticate"] == 'Basic realm="pluteus"'
    text = " ".join(t for _, t in app.state.debuglog.lines())
    assert "other headers: " in text and "x-odd-login" in text and "accept-language" in text and "secret-value-1" not in text

"""The browser side: login page, sessions, redirects, CSRF, throttling, themes, settings, security headers."""
import dataclasses
import os
from urllib.parse import urlencode

import pytest
from conftest import basic
from starlette.testclient import TestClient

from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import User

HTML = {"accept": "text/html,application/xhtml+xml"}
FORM = {"content-type": "application/x-www-form-urlencoded"}
BOOK = "/dl/books/Lescroart%2C%20John%20T./Il%20Tredicesimo%20Giurato.epub"


def post(client, url, data, origin="http://testserver", **headers):
    h = dict(FORM, **headers)
    if origin:
        h["origin"] = origin
    return client.post(url, content=urlencode(data), headers=h)


def login(client, user="alice", pw="pw-alice", next_url=""):
    return post(client, "/login", {"username": user, "password": pw, "next": next_url})


def make(cfg, tmp_path, **changes):
    themes = tmp_path / "themes" / "ocean"
    themes.mkdir(parents=True, exist_ok=True)
    (themes / "theme.css").write_text(":root { --pl-accent: #0088cc; }", encoding="utf-8")
    cfg = dataclasses.replace(cfg, data_dir=str(tmp_path / "data"), themes_dir=str(tmp_path / "themes"), **changes)
    app = create_app(cfg)
    return TestClient(app, follow_redirects=False), app


@pytest.fixture
def web(cfg, tmp_path):
    return make(cfg, tmp_path)


# ---- the login page, assets and headers ---------------------------------------------------------------
def test_login_page_and_security_headers(web):
    client, _ = web
    r = client.get("/login")
    assert r.status_code == 200 and "Sign in" in r.text and "name='password'" in r.text
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "SAMEORIGIN"
    assert "/static/base.css" in r.text and "/theme/default/theme.css" in r.text
    assert client.get("/healthz").headers["x-frame-options"] == "SAMEORIGIN"       # on every response


def test_static_and_theme_assets_are_public_uncached_and_safe(web):
    client, _ = web
    css = client.get("/static/base.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert css.headers["cache-control"] == "no-cache"
    assert "Sepia" in client.get("/theme/sepia/theme.css").text
    icon = client.get("/theme/sepia/icons/next.svg")                                # sepia has none: default's is used
    assert icon.status_code == 200 and "svg" in icon.headers["content-type"]
    for bad in ("/static/%2e%2e/app.py", "/static/themes/default/theme.css", "/static/nothing.css",
                "/theme/default/%2e%2e/%2e%2e/app.py", "/theme/default/icons/", "/theme/default/theme.txt",
                "/theme/..%5Cx/theme.css", "/static/%5Capp.py"):
        assert client.get(bad).status_code == 404, bad


def test_themes_can_be_edited_and_reloaded_without_a_restart(web, tmp_path):
    client, _ = web
    first = client.get("/theme/ocean/theme.css")
    assert "#0088cc" in first.text and first.headers["etag"]
    assert client.get("/theme/ocean/theme.css", headers={"if-none-match": first.headers["etag"]}).status_code == 304
    path = tmp_path / "themes" / "ocean" / "theme.css"
    path.write_text(":root { --pl-accent: #ff00aa; /* edited */ }", encoding="utf-8")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    second = client.get("/theme/ocean/theme.css", headers={"if-none-match": first.headers["etag"]})
    assert second.status_code == 200 and "#ff00aa" in second.text


def test_the_underscore_alias_serves_the_users_own_theme_with_fallback(web, tmp_path):
    client, app = web
    icons = tmp_path / "themes" / "ocean" / "icons"
    icons.mkdir()
    (icons / "next.svg").write_text("<svg>ocean-next</svg>", encoding="utf-8")
    assert "ocean-next" not in client.get("/theme/_/icons/next.svg").text            # signed out: the server theme
    login(client)
    post(client, "/settings", {"theme": "ocean"})
    mine = client.get("/theme/_/icons/next.svg")
    assert "ocean-next" in mine.text and mine.headers["vary"] == "Cookie"           # per-user answer, so Vary
    assert client.get("/theme/_/icons/prev.svg").status_code == 200                  # not overridden: default's file
    assert "vary" not in client.get("/theme/ocean/icons/next.svg").headers           # a named theme is the same for all
    other = TestClient(app, follow_redirects=False)
    login(other, "bob", "pw-bob")
    assert "ocean-next" not in other.get("/theme/_/icons/next.svg").text             # bob has the default theme
    assert client.get("/theme/_/../../app.py").status_code == 404


def test_icon_rules_never_hide_a_url_inside_a_css_variable():
    # Browsers resolve a relative url() in a custom property against the file that USES the variable,
    # not the one that defines it: the icons were all 404 until the rules carried their own URLs.
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "pluteus" / "static"
    default_css = (root / "themes" / "default" / "theme.css").read_text(encoding="utf-8")
    assert "--pl-icon" not in default_css and "url(../_/icons/next.svg)" in default_css
    for css in root.rglob("*.css"):
        text = css.read_text(encoding="utf-8")
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("/*", "*", "Do not")))
        assert "--pl-icon:" not in code, css


def test_scheme_toggle_script_and_favicon(web):
    client, _ = web
    js = client.get("/static/scheme.js")
    assert js.status_code == 200 and "data-scheme" in js.text and "localStorage" in js.text
    page = client.get("/login").text
    assert "pl-scheme" in page and "rel='icon'" in page
    assert "scheme.js' defer" not in page and "<script src='/static/scheme.js'></script>" in page   # runs before first paint
    assert page.index("scheme.js") < page.index("<body")


def test_the_book_icon_is_served_as_favicon_home_screen_icon_and_manifest(web):
    import json
    client, _ = web
    page = client.get("/login").text
    for href in ("/static/icon/favicon.ico", "/static/icon/favicon-32.png", "/static/icon/apple-touch-icon.png",
                 "/static/manifest.webmanifest"):
        assert "href='%s'" % href in page, href
    png, ico = b"\x89PNG\r\n\x1a\n", b"\x00\x00\x01\x00"
    for url, magic in (("/static/icon/icon-512.png", png), ("/static/icon/icon-192.png", png),
                       ("/static/icon/apple-touch-icon.png", png), ("/static/icon/favicon-32.png", png),
                       ("/static/icon/favicon-16.png", png), ("/static/icon/favicon.ico", ico), ("/favicon.ico", ico)):
        r = client.get(url)
        assert r.status_code == 200 and r.content.startswith(magic), url
    manifest = client.get("/static/manifest.webmanifest")
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    data = json.loads(manifest.text)
    assert data["name"] == "pluteus" and {i["sizes"] for i in data["icons"]} == {"192x192", "512x512"}
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(client.get("/static/icon/icon-512.png").content)).size == (512, 512)
    assert "icon-192.png" in client.get("/theme/default/theme.css").text                    # the login-card logo


def test_all_built_in_themes_are_selectable(web):
    client, _ = web
    login(client)
    options = client.get("/settings", headers=HTML).text
    for name in ("default", "night", "northernlights", "ocean", "sepia"):
        assert "<option value='%s'" % name in options
        assert client.get("/theme/%s/theme.css" % name).status_code == 200
    css = client.get("/theme/northernlights/theme.css").text
    assert "backdrop-filter" in css and 'data-scheme="light"' in css


# ---- redirects versus reader-app challenges ------------------------------------------------------------------
def test_browsers_are_redirected_to_login_but_apps_get_a_challenge(web):
    client, _ = web
    r = client.get("/browse/comics?page=2", headers=HTML)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=%2Fbrowse%2Fcomics%3Fpage%3D2"
    assert client.get("/", headers=HTML).headers["location"] == "/login?next=%2F"
    r = client.get("/browse/comics")                                                 # no html Accept: a script or app
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    r = client.get("/opds", headers=HTML)                                            # OPDS never redirects
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")


# ---- logging in ---------------------------------------------------------------------------------------------------
def test_login_sets_a_safe_cookie_and_opens_the_site(web):
    client, _ = web
    r = login(client)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("pluteus_session=") and "HttpOnly" in cookie and "Path=/" in cookie
    assert "SameSite=lax" in cookie and "Max-Age=2592000" in cookie and "Secure" not in cookie
    page = client.get("/", headers=HTML)
    assert page.status_code == 200 and "Sign out (alice)" in page.text and "comics" in page.text
    assert client.get("/opds").status_code == 200                                    # the cookie also opens OPDS
    assert client.get(BOOK).status_code == 200                                       # ... and downloads
    assert client.get("/login", headers=HTML).status_code == 303                     # already in: skip the form


def test_wrong_password_and_unknown_user_look_the_same(web):
    client, _ = web
    for user, pw in (("alice", "bad"), ("ghost", "bad")):
        r = login(client, user, pw)
        assert r.status_code == 401 and "Wrong user name or password." in r.text
        assert "set-cookie" not in r.headers
    assert client.get("/", headers=HTML).status_code == 303


def test_login_is_refused_without_a_same_site_origin(web):
    client, _ = web
    assert post(client, "/login", {"username": "alice", "password": "pw-alice"}, origin=None).status_code == 403
    assert post(client, "/login", {"username": "alice", "password": "pw-alice"},
                origin="http://evil.example").status_code == 403
    ok = post(client, "/login", {"username": "alice", "password": "pw-alice"}, origin=None,
              referer="http://testserver/login")                                    # Referer is an accepted fallback
    assert ok.status_code == 303


@pytest.mark.parametrize("target,expected", [
    ("/browse/comics", "/browse/comics"), ("/browse/comics?page=2", "/browse/comics?page=2"),
    ("//evil.example/x", "/"), ("https://evil.example/", "/"), ("\\\\evil.example", "/"),
    ("javascript:alert(1)", "/"), ("", "/"), ("/ok\r\nSet-Cookie: x=1", "/"), ("relative/path", "/")])
def test_post_login_redirect_is_only_ever_a_local_path(web, target, expected):
    client, _ = web
    assert login(client, next_url=target).headers["location"] == expected


def test_logout_ends_the_session_for_good(web):
    client, _ = web
    token = login(client).cookies.get("pluteus_session") or client.cookies.get("pluteus_session")
    assert post(client, "/logout", {}, origin=None).status_code == 403              # CSRF: nothing happened
    assert client.get("/", headers=HTML).status_code == 200
    r = post(client, "/logout", {})
    assert r.status_code == 303 and r.headers["location"] == "/login" and "Max-Age=0" in r.headers["set-cookie"]
    stolen = client.get("/", headers=dict(HTML, cookie="pluteus_session=%s" % token))
    assert stolen.status_code == 303                                                  # the old cookie is dead


def test_removing_a_user_or_changing_their_password_ends_their_sessions(web, cfg):
    client, app = web
    login(client)
    assert client.get("/", headers=HTML).status_code == 200
    changed = dict(cfg.users, alice=User("alice", hash_password("new-password", n=2 ** 4)))
    app.state.auth.set_users(changed)
    assert client.get("/", headers=HTML).status_code == 303                           # password changed -> signed out
    app.state.auth.set_users(cfg.users)
    login(client, "bob", "pw-bob")
    assert client.get("/", headers=HTML).status_code == 200
    app.state.auth.set_users({k: v for k, v in cfg.users.items() if k != "bob"})
    assert client.get("/", headers=HTML).status_code == 303                           # user removed -> signed out


# ---- throttling ------------------------------------------------------------------------------------------------------
def test_login_form_locks_after_five_failures_even_for_the_right_password(web):
    client, _ = web
    for _ in range(5):
        assert login(client, pw="wrong").status_code == 401
    r = login(client)                                                                  # correct, but locked
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0 and "Too many failed attempts" in r.text
    assert "set-cookie" not in r.headers
    assert login(client, "bob", "pw-bob").status_code == 303                           # other accounts are not affected


def test_basic_logins_are_throttled_but_recent_good_ones_keep_working(web):
    client, _ = web
    assert client.get("/opds", headers=basic("alice", "pw-alice")).status_code == 200    # verified and remembered
    for _ in range(5):
        assert client.get("/opds", headers=basic("alice", "wrong")).status_code == 401
    r = client.get("/opds", headers=basic("alice", "wrong"))
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert client.get("/opds", headers=basic("alice", "pw-alice")).status_code == 200   # a locked-out attacker can't lock the owner out
    assert client.get("/opds", headers=basic("bob", "pw-bob")).status_code == 200


# ---- themes and settings ----------------------------------------------------------------------------------------------
def test_theme_choice_is_per_user_and_applied_to_every_page(web):
    client, app = web
    login(client)
    s = client.get("/settings", headers=HTML)
    assert s.status_code == 200 and all(("<option value='%s'" % t) in s.text for t in ("default", "night", "ocean", "sepia"))
    r = post(client, "/settings", {"theme": "ocean"})
    assert r.status_code == 303 and r.headers["location"] == "/settings?saved=1"
    home = client.get("/", headers=HTML).text
    assert "/theme/ocean/theme.css" in home and "/theme/default/theme.css" in home
    assert "Saved" in client.get("/settings?saved=1", headers=HTML).text
    post(client, "/settings", {"theme": "../nope"})                                    # invalid names are ignored
    assert "/theme/ocean/theme.css" in client.get("/", headers=HTML).text
    other = TestClient(app, follow_redirects=False)
    login(other, "bob", "pw-bob")
    assert "/theme/ocean/" not in other.get("/", headers=HTML).text                    # bob still has the default


def test_settings_needs_a_login_and_a_same_site_post(web):
    client, _ = web
    assert client.get("/settings", headers=HTML).status_code == 303
    login(client)
    assert post(client, "/settings", {"theme": "sepia"}, origin=None).status_code == 403
    assert post(client, "/settings", {"theme": "sepia"}, origin="http://evil.example").status_code == 403


def test_the_server_wide_theme_is_used_until_a_user_picks_one(cfg, tmp_path):
    client, _ = make(cfg, tmp_path, theme="sepia")
    assert "/theme/sepia/theme.css" in client.get("/login").text
    login(client)
    assert "/theme/sepia/theme.css" in client.get("/", headers=HTML).text


# ---- odds and ends ---------------------------------------------------------------------------------------------------------
def test_the_anonymous_user_does_not_hide_the_login_form(cfg, tmp_path):
    client, _ = make(cfg, tmp_path, anonymous_user="carol")
    assert client.get("/login").status_code == 200
    page = client.get("/", headers=HTML)
    assert page.status_code == 200 and "Sign out (carol)" in page.text                  # browsing as the guest
    assert login(client).status_code == 303


@pytest.mark.parametrize("changes,headers,secure", [
    ({}, {}, False), ({"cookie_secure": "always"}, {}, True), ({"cookie_secure": "never"}, {"x-forwarded-proto": "https"}, False),
    ({"trust_proxy": True}, {"x-forwarded-proto": "https"}, True), ({"trust_proxy": False}, {"x-forwarded-proto": "https"}, False)])
def test_cookie_secure_flag_follows_the_config(cfg, tmp_path, changes, headers, secure):
    client, _ = make(cfg, tmp_path, **changes)
    r = post(client, "/login", {"username": "alice", "password": "pw-alice"}, **headers)
    assert ("Secure" in r.headers["set-cookie"]) is secure


def test_reading_progress_shows_up_as_a_badge(web):
    client, app = web
    login(client)
    app.state.state.put_progress("alice", "books", "Good Author/Real Book.epub", "loc", 0.42)
    html = client.get("/browse/books/Good%20Author", headers=HTML).text
    assert "42%" in html

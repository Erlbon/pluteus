"""Every route of both apps (the site and the admin page) is guarded, checked by walking the route tables.

Review card 57: the older tests name some routes by hand, so a new route without its guard would pass. Here every route
that the apps register is asked for (each of its methods):
  * without any credentials: the answer must be 401, or the redirect to the login page;
  * for an unsafe method, signed in, from another website (a foreign Origin): the answer must be 403.
The routes that are public ON PURPOSE, and the ones that carry their own kind of login, are listed below by name. A new
route that is in neither list and is not guarded fails this file; so does a listed route that no longer exists.
"""
import dataclasses
import re

import pytest
from conftest import basic
from starlette.routing import Route
from starlette.testclient import TestClient
from test_adminweb import BOSS, make_config

from pluteus.adminapp import create_admin_app
from pluteus.app import create_app
from pluteus.config import load_config

SAFE = {"GET", "HEAD", "OPTIONS", "PROPFIND"}          # they only read (PROPFIND is the WebDAV listing)
EVIL = {"origin": "http://evil.example", "sec-fetch-site": "cross-site"}
FORM = {"content-type": "application/x-www-form-urlencoded"}

# ---- the site ---------------------------------------------------------------------------------------------------------------------
# Public on purpose: answered without a login. (method, route path) -> why.
MAIN_PUBLIC = {
    ("GET", "/healthz"): "the health check of Docker and the proxy",
    ("GET", "/static/{path:path}"): "stylesheets, scripts and icons (the login page needs them)",
    ("GET", "/favicon.ico"): "the icon",
    ("GET", "/theme/{name}/{path:path}"): "theme files (the login page needs them)",
    ("GET", "/login"): "the login form",
    ("POST", "/login"): "the login itself (same-site only, checked below)",
    ("POST", "/logout"): "ends the session of whoever sends it (same-site only, checked below)",
    ("GET", "/manifest.webmanifest"): "the web app manifest, fetched by the browser without the cookie",
    ("GET", "/sw.js"): "the service worker script",
    ("GET", "/join/{token}"): "the invitation link: the token in the address is the credential",
    ("GET", "/cast/{token}/{name:path}"): "a TV fetching a file: the signed token in the address is the credential",
    ("OPTIONS", "/cast/{token}/{name:path}"): "the TV's preflight for the same address",
}
# Their own protocol and their own login (never the browser's cookie), so neither the 401-or-login-page rule nor the Origin
# rule describes them. Each is checked below to refuse a request that carries no credentials of its own kind.
MAIN_OWN_LOGIN = {
    "/rest/{name}": "Subsonic: user and token or password in the query; errors are Subsonic answers",
    "/kosync/{route:path}": "KOReader sync: x-auth-user and x-auth-key headers",
}


def fill(path: str) -> str:
    """A route path with its parameters filled in: the library for {root}, something harmless for the rest."""
    return re.sub(r"\{(\w+)(?::\w+)?\}", lambda m: "books" if m.group(1) == "root" else "x", path)


def table(app):
    """[(method, route path)] of every route; anything that is not a plain Route would hide routes from this file."""
    out = []
    for route in app.routes:
        assert isinstance(route, Route), "a %s in the route table: teach this test to walk it" % type(route).__name__
        assert route.methods, "%s takes every method: give the route its methods, so that each can be checked" % route.path
        for method in sorted(route.methods):
            out.append((method, route.path))
    return out


def listed_public(method: str, path: str, public: dict) -> bool:
    """Is this route public on purpose? (HEAD goes with GET: the same answer without its body.)"""
    return ("GET" if method == "HEAD" else method, path) in public


def is_login_redirect(response) -> bool:
    return response.status_code in (302, 303) and "/login" in response.headers.get("location", "")


@pytest.fixture
def site(cfg, tmp_path):
    """The site with every optional part switched on, so that every route it can have is in the table."""
    full = dataclasses.replace(cfg, data_dir=str(tmp_path / "data"), uploads_dir=str(tmp_path / "up"), webdav=True, subsonic=True,
                               kosync=True, relay=True, cast=True, thumbnails=True)
    return create_app(full)


def test_the_site_has_all_its_optional_routes_in_this_test(site):
    paths = {path for _, path in table(site)}
    for needed in ("/dav/{path:path}", "/rest/{name}", "/kosync/{route:path}", "/api/cast/play", "/relay/{root}/{path:path}",
                   "/upload/{root}", "/thumb/{root}/{path:path}"):
        assert needed in paths, needed
    listed = {path for _, path in MAIN_PUBLIC} | set(MAIN_OWN_LOGIN)
    assert not listed - paths, "listed as public, but no longer a route: %s" % sorted(listed - paths)
    assert len(paths) > 50


def test_every_site_route_refuses_a_request_without_credentials(site):
    client = TestClient(site, follow_redirects=False)
    open_routes = []
    for method, path in table(site):
        if listed_public(method, path, MAIN_PUBLIC) or path in MAIN_OWN_LOGIN:
            continue
        for headers in ({}, {"accept": "text/html"}, {"origin": "http://testserver"}):
            r = client.request(method, fill(path), headers=headers)
            if not (r.status_code == 401 or is_login_redirect(r)):
                open_routes.append("%s %s -> %d" % (method, path, r.status_code))
                break
    assert not open_routes, "answered without a login (guard it, or list it in MAIN_PUBLIC with the reason):\n" + "\n".join(open_routes)


def test_every_unsafe_site_route_refuses_another_website(site):
    me = TestClient(site, follow_redirects=False)
    login = me.post("/login", content="username=alice&password=pw-alice", headers={**FORM, "origin": "http://testserver"})
    assert login.status_code == 303 and me.get("/", headers={"accept": "text/html"}).status_code == 200      # really signed in
    fetched = TestClient(site, follow_redirects=False)                                                     # the same, with Basic credentials
    crossed, seen = [], 0
    for method, path in table(site):
        if method in SAFE or path in MAIN_OWN_LOGIN:
            continue
        seen += 1
        for who, client, extra in (("cookie", me, {}), ("basic", fetched, basic("alice", "pw-alice"))):
            r = client.request(method, fill(path), content="name=x", headers={**FORM, **EVIL, **extra})
            if r.status_code != 403:
                crossed.append("%s %s (%s) -> %d" % (method, path, who, r.status_code))
    assert seen > 25
    assert not crossed, "an unsafe request from another website was not refused with 403:\n" + "\n".join(crossed)
    assert me.get("/", headers={"accept": "text/html"}).status_code == 200                                # and the refused logout did not sign out


def test_the_routes_with_their_own_login_refuse_a_request_without_it(site):
    client = TestClient(site, follow_redirects=False)
    assert set(MAIN_OWN_LOGIN) == {"/rest/{name}", "/kosync/{route:path}"}                                # a new one needs its own check here
    for method in ("GET", "POST"):
        r = client.request(method, "/rest/getMusicFolders.view?f=json&v=1.16.1&c=test")
        assert r.status_code == 200 and r.json()["subsonic-response"]["status"] == "failed", r.text[:200]
        assert r.json()["subsonic-response"]["error"]["code"] in (10, 40, 41, 42, 43, 44)                 # missing parameter or wrong login
    for method, path in (("GET", "/kosync/users/auth"), ("GET", "/kosync/syncs/progress/abc"), ("PUT", "/kosync/syncs/progress")):
        r = client.request(method, path, headers={"accept": "application/vnd.koreader.v1+json"})
        assert r.status_code == 401, (method, path, r.status_code)


# ---- the admin page ---------------------------------------------------------------------------------------------------------------
ADMIN_PUBLIC = {
    ("GET", "/static/{path:path}"): "stylesheets, scripts and icons, the same files as on the site",
    ("GET", "/favicon.ico"): "the icon",
    ("GET", "/theme/{name}/{path:path}"): "theme files",
}


@pytest.fixture
def admin_app(tmp_path):
    path = make_config(tmp_path)
    return create_admin_app(create_app(load_config(str(path)), config_path=str(path), reload_interval=0.0))


def test_every_admin_route_needs_an_administrator(admin_app):
    client = TestClient(admin_app, follow_redirects=False)
    paths = {path for _, path in table(admin_app)}
    listed = {path for _, path in ADMIN_PUBLIC}
    assert not listed - paths, "listed as public, but no longer a route: %s" % sorted(listed - paths)
    assert len(paths) > 20
    open_routes, weak = [], []
    for method, path in table(admin_app):
        if listed_public(method, path, ADMIN_PUBLIC):
            continue
        for headers in ({}, {"origin": "http://testserver"}):
            r = client.request(method, fill(path), content="name=x", headers={**FORM, **headers})
            if not (r.status_code == 401 and "WWW-Authenticate" in r.headers):
                open_routes.append("%s %s -> %d" % (method, path, r.status_code))
                break
        r = client.request(method, fill(path), content="name=x",
                           headers={**FORM, "origin": "http://testserver", **basic("alice", "alice-password-1")})
        if r.status_code != 403:
            weak.append("%s %s -> %d" % (method, path, r.status_code))
    assert not open_routes, "answered without a login (guard it, or list it in ADMIN_PUBLIC with the reason):\n" + "\n".join(open_routes)
    assert not weak, "answered a user who is not an administrator:\n" + "\n".join(weak)


def test_every_unsafe_admin_route_refuses_another_website(admin_app):
    client = TestClient(admin_app, follow_redirects=False)
    crossed, seen = [], 0
    for method, path in table(admin_app):
        if method in SAFE:
            continue
        seen += 1
        for headers in (EVIL, {}):                      # another website, and no Origin at all
            r = client.request(method, fill(path), content="name=x", headers={**FORM, **BOSS, **headers})
            if r.status_code != 403:
                crossed.append("%s %s (%s) -> %d" % (method, path, "foreign origin" if headers else "no origin", r.status_code))
    assert seen >= 17
    assert not crossed, "an unsafe request that did not come from the admin page itself was not refused with 403:\n" + "\n".join(crossed)

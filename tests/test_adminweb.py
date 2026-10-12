"""The admin page: who may use it, what it shows and every action, plus the second server on its own port."""
import base64
import socket
import time
import urllib.error
import urllib.request

import pytest
from conftest import FAST, basic
from starlette.testclient import TestClient

from pluteus import auth as authmod
from pluteus.adminapp import create_admin_app, start_admin_server
from pluteus.app import create_app
from pluteus.config import ConfigError, load_config, parse_config
from pluteus.useredit import add_user_text, set_user_text

ORIGIN = {"origin": "http://testserver"}
FORM = {"content-type": "application/x-www-form-urlencoded", **ORIGIN}


def make_config(tmp_path, admin_users=("boss",)):
    books, comics = tmp_path / "books", tmp_path / "comics"
    books.mkdir()
    comics.mkdir()
    users = ""
    for name, password, extra in (("boss", "boss-password-1", "admin = true\n"), ("alice", "alice-password-1", ""),
                                  ("deputy", "deputy-password-1", "admin = true\n")):
        users += '[[users]]\nname = "%s"\npassword_hash = "%s"\n%s\n' % (name, authmod.hash_password(password, **FAST), extra)
    text = ('[server]\nport = 8080\nadmin_port = 8101\ndata_dir = "%s"\nuploads_dir = "%s"\n\n'
            '[[roots]]\nname = "books"\npath = "%s"\n\n[[roots]]\nname = "comics"\npath = "%s"\n\n%s'
            % (str(tmp_path / "data").replace("\\", "/"), str(tmp_path / "up").replace("\\", "/"),
               str(books).replace("\\", "/"), str(comics).replace("\\", "/"), users))
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8", newline="")
    return path


@pytest.fixture
def admin(tmp_path):
    path = make_config(tmp_path)
    cfg = load_config(str(path))
    app = create_app(cfg, config_path=str(path), reload_interval=0.0)
    return TestClient(create_admin_app(app), follow_redirects=False), app, path


BOSS = basic("boss", "boss-password-1")


# ---- configuration -----------------------------------------------------------------------------------------------------------------
def conf(**server):
    return {"server": server, "roots": [{"name": "r", "path": "."}],
            "users": [{"name": "u", "password_hash": "scrypt$1$2$3$4$5", "admin": True}]}


def test_the_admin_page_is_off_by_default_and_the_admin_flag_defaults_to_false():
    cfg = parse_config(conf(), check_roots=False)
    assert cfg.admin_port == 0 and cfg.admin_host == "127.0.0.1"
    plain = conf()
    plain["users"][0].pop("admin")
    assert parse_config(plain, check_roots=False).users["u"].admin is False
    assert parse_config(conf(admin_port=8101), check_roots=False).admin_port == 8101


def test_admin_port_needs_an_admin_user_and_its_own_port():
    data = conf(admin_port=8101)
    data["users"][0]["admin"] = False
    with pytest.raises(ConfigError, match="no user has admin = true"):
        parse_config(data, check_roots=False)
    with pytest.raises(ConfigError, match="must differ"):
        parse_config(conf(port=8101, admin_port=8101), check_roots=False)
    with pytest.raises(ConfigError, match="admin_port"):
        parse_config(conf(admin_port=70000), check_roots=False)


def test_the_admin_flag_can_be_written_and_changed_in_the_file():
    base = '[server]\n[[roots]]\nname = "r"\npath = "."\n'
    text = add_user_text(base, "root", "scrypt$1$2$3$4$5", admin=True)
    assert "admin = true" in text
    assert "admin" not in add_user_text(base, "plain", "scrypt$1$2$3$4$5")
    off = set_user_text(text, "root", admin=False)
    assert "admin = true" not in off
    assert "admin = true" in set_user_text(off, "root", admin=True)


# ---- who may come in ---------------------------------------------------------------------------------------------------------------
def test_signing_in_is_required_and_only_admins_get_further(admin):
    client, _, _ = admin
    r = client.get("/")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith('Basic realm="pluteus admin"')
    assert client.get("/", headers=basic("boss", "wrong-password")).status_code == 401
    assert client.get("/", headers=basic("alice", "alice-password-1")).status_code == 403    # right password, not an admin
    assert client.get("/", headers=BOSS).status_code == 200
    for path in ("/security", "/users"):
        assert client.get(path).status_code == 401 and client.get(path, headers=BOSS).status_code == 200


def test_wrong_passwords_are_throttled_like_on_the_main_site(admin):
    client, app, _ = admin
    for i in range(5):
        assert client.get("/", headers=basic("boss", "guess%d" % i)).status_code == 401
    assert client.get("/", headers=basic("boss", "guess-more")).status_code == 429
    assert client.get("/", headers=BOSS).status_code == 429                                  # right password, never verified: refused
    app.state.limiter.unlock("boss")
    assert client.get("/", headers=BOSS).status_code == 200                                  # verified once ...
    for i in range(5):
        client.get("/", headers=basic("boss", "again%d" % i))
    app.state.auth._cache.clear()                                                            # (the 5 minute cache has run out)
    assert client.get("/", headers=basic("boss", "again-more")).status_code == 429
    assert client.get("/", headers=BOSS).status_code == 200                                  # ... so it still works during a lock


def test_every_change_needs_a_same_site_post(admin):
    client, _, _ = admin
    for path in ("/security/unlock", "/security/forget", "/security/signout", "/users/add", "/users/password",
                 "/users/access", "/users/remove"):
        assert client.post(path, content="name=x", headers={**BOSS, "content-type": FORM["content-type"]}).status_code == 403, path
        assert client.post(path, content="name=x", headers={**BOSS, "content-type": FORM["content-type"],
                                                           "origin": "http://evil.example"}).status_code == 403, path
        assert client.get(path, headers=BOSS).status_code == 405, path
    assert client.post("/users/add", content="name=x", headers={"content-type": FORM["content-type"], **ORIGIN}).status_code == 401


def test_stylesheets_and_themes_are_public_and_nothing_admin_is_on_the_main_site(admin):
    client, app, _ = admin
    assert client.get("/static/base.css").status_code == 200 and client.get("/theme/default/theme.css").status_code == 200
    main = TestClient(app, follow_redirects=False)
    for path in ("/security", "/users", "/security/unlock"):
        assert main.get(path, headers={"accept": "text/html"}).status_code in (404, 405)


# ---- status ------------------------------------------------------------------------------------------------------------------------
def test_status_shows_libraries_uploads_and_state(admin, tmp_path):
    client, app, _ = admin
    (tmp_path / "up" / "alice").mkdir(parents=True)
    (tmp_path / "up" / "alice" / "f.bin").write_bytes(b"x" * 3000)
    html = client.get("/", headers=BOSS).text
    from pluteus import __version__
    assert __version__ in html and "<h1>Status</h1>" in html and "pl-dot-ok'></span>ok" in html and "NOT available" not in html
    assert "books" in html and "comics" in html and "alice" in html and "2.9 KB" in html or "3000 B" in html
    assert "do not route or forward" in html                                                  # the standing warning
    (tmp_path / "comics").rmdir()
    assert "NOT available" in client.get("/", headers=BOSS).text


# ---- security ----------------------------------------------------------------------------------------------------------------------
def test_locks_are_listed_and_can_be_cleared(admin):
    client, app, _ = admin
    for i in range(5):
        app.state.limiter.failure("mallory", "", now=time.time())
    html = client.get("/security", headers=BOSS).text
    assert "mallory" in html and "locked for" in html
    r = client.post("/security/unlock", content="name=mallory", headers={**BOSS, **FORM})
    assert r.status_code == 303 and r.headers["location"] == "/security?ok=unlocked"
    assert app.state.limiter.wait_seconds("mallory", "") == 0
    assert "The lock is cleared." in client.get("/security?ok=unlocked", headers=BOSS).text
    assert "There was nothing to clear." in client.get("/security?ok=nothing", headers=BOSS).text
    assert client.post("/security/unlock", content="name=mallory", headers={**BOSS, **FORM}).headers["location"].endswith("nothing")


def test_known_devices_can_be_forgotten_and_a_user_signed_out_everywhere(admin):
    client, app, _ = admin
    sessions, st = app.state.sessions, app.state.state
    alice = app.state.auth.users["alice"]
    token = sessions.start(alice)
    device_token, device_id = sessions.remember_device(None, alice, "203.0.113.9")
    html = client.get("/security", headers=BOSS).text
    assert "alice" in html and "203.0.113.9" in html and device_id in html
    r = client.post("/security/forget", content="id=" + device_id, headers={**BOSS, **FORM})
    assert r.status_code == 303 and st.get_device(device_id) is None
    sessions.remember_device(None, alice, "203.0.113.9")
    assert app.state.auth.authenticate(basic("alice", "alice-password-1")["Authorization"])         # a verified reader-app login
    assert sessions.user_for(token, app.state.auth.users) is not None
    r = client.post("/security/signout", content="name=alice", headers={**BOSS, **FORM})
    assert r.headers["location"] == "/security?ok=signedout"
    assert sessions.user_for(token, app.state.auth.users) is None and st.list_devices() == []
    assert app.state.auth.cached_user(basic("alice", "alice-password-1")["Authorization"]) is None


# ---- users -------------------------------------------------------------------------------------------------------------------------
def read_users(path):
    return load_config(str(path)).users


def test_users_are_listed_with_their_rights(admin):
    client, _, _ = admin
    html = client.get("/users", headers=BOSS).text
    assert "boss" in html and "(you)" in html and "alice" in html and "admin" in html
    assert html.count("/users/remove") == 2                                                    # not for yourself
    assert "type='password'" in html and "name='root' value='books'" in html


def test_adding_a_user_writes_the_config_with_the_chosen_rights(admin):
    client, _, path = admin
    body = "name=carol&password=carol-password-1&again=carol-password-1&root=books&adult=on&admin=on"
    r = client.post("/users/add", content=body, headers={**BOSS, **FORM})
    assert r.status_code == 303 and r.headers["location"] == "/users?ok=added"
    carol = read_users(path)["carol"]
    assert carol.roots == ("books",) and carol.adult and not carol.upload and carol.admin       # upload box not ticked: off
    assert authmod.verify_password("carol-password-1", carol.password_hash)
    assert "User added." in client.get("/users?ok=added", headers=BOSS).text


@pytest.mark.parametrize("body,message", [
    ("name=dave&password=short&again=short", "at least 8"), ("name=dave&password=long-enough-1&again=different-12", "differ"),
    ("name=alice&password=long-enough-1&again=long-enough-1", "already exists"),
    ("name=dave&password=long-enough-1&again=long-enough-1&root=nowhere", "unknown root")])
def test_a_refused_change_is_shown_on_the_page_and_nothing_is_written(admin, body, message):
    client, _, path = admin
    before = path.read_text(encoding="utf-8")
    r = client.post("/users/add", content=body, headers={**BOSS, **FORM})
    assert r.status_code == 400 and message in r.text and "role='alert'" in r.text
    assert path.read_text(encoding="utf-8") == before


def test_password_and_access_changes(admin):
    client, _, path = admin
    r = client.post("/users/password", content="name=alice&password=brand-new-pass-1&again=brand-new-pass-1", headers={**BOSS, **FORM})
    assert r.headers["location"] == "/users?ok=password"
    assert authmod.verify_password("brand-new-pass-1", read_users(path)["alice"].password_hash)
    assert client.post("/users/password", content="name=alice&password=x&again=x", headers={**BOSS, **FORM}).status_code == 400
    r = client.post("/users/access", content="name=alice&root=comics&upload=on", headers={**BOSS, **FORM})
    alice = read_users(path)["alice"]
    assert r.headers["location"] == "/users?ok=access" and alice.roots == ("comics",) and alice.upload and not alice.adult


def test_the_last_admin_cannot_be_removed_or_demoted_and_you_cannot_remove_yourself(admin):
    client, _, path = admin
    assert client.post("/users/remove", content="name=boss", headers={**BOSS, **FORM}).status_code == 400          # yourself
    assert "cannot remove yourself" in client.post("/users/remove", content="name=boss", headers={**BOSS, **FORM}).text
    assert client.post("/users/remove", content="name=deputy", headers={**BOSS, **FORM}).headers["location"] == "/users?ok=removed"
    assert "deputy" not in read_users(path)
    r = client.post("/users/access", content="name=boss&upload=on", headers={**BOSS, **FORM})                      # un-admin the last
    assert r.status_code == 400 and "at least one admin" in r.text and read_users(path)["boss"].admin
    r = client.post("/users/remove", content="name=alice", headers={**BOSS, **FORM})
    assert r.headers["location"] == "/users?ok=removed" and "alice" not in read_users(path)


def test_everything_from_outside_is_escaped_on_the_pages(admin):
    client, app, path = admin
    body = "name=%3Cb%3Ex%3C%2Fb%3E&password=long-enough-1&again=long-enough-1"
    assert client.post("/users/add", content=body, headers={**BOSS, **FORM}).status_code == 303
    html = client.get("/users", headers=BOSS).text
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html
    app.state.limiter.failure("<script>alert(1)</script>", "", now=time.time())
    sec = client.get("/security", headers=BOSS).text
    assert "<script>alert(1)</script>" not in sec and "&lt;script&gt;" in sec
    assert "<script>alert" not in client.get("/users?ok=%3Cscript%3E", headers=BOSS).text                          # nothing is echoed


def test_a_server_started_without_a_config_file_cannot_edit_users(tmp_path):
    path = make_config(tmp_path)
    cfg = load_config(str(path))
    client = TestClient(create_admin_app(create_app(cfg)), follow_redirects=False)
    assert "cannot be changed here" in client.get("/users", headers=BOSS).text
    assert client.post("/users/add", content="name=x", headers={**BOSS, **FORM}).status_code == 400


# ---- the second server -------------------------------------------------------------------------------------------------------------
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_admin_page_runs_on_its_own_port_next_to_the_main_server(tmp_path):
    import dataclasses
    path = make_config(tmp_path)
    port = free_port()
    cfg = dataclasses.replace(load_config(str(path)), admin_port=port, admin_host="127.0.0.1")
    app = create_app(cfg, config_path=str(path))
    server = start_admin_server(app)
    try:
        url = "http://127.0.0.1:%d/" % port
        for _ in range(100):
            try:
                urllib.request.urlopen(url, timeout=1)
            except urllib.error.HTTPError as e:
                assert e.code == 401 and "pluteus admin" in e.headers["WWW-Authenticate"]
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("the admin server did not start")
        token = base64.b64encode(b"boss:boss-password-1").decode()
        page = urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Basic " + token}), timeout=5)
        assert page.status == 200 and b"<h1>Status</h1>" in page.read()
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Basic " + base64.b64encode(b"alice:alice-password-1").decode()}), timeout=5)
        assert err.value.code == 403
    finally:
        server.stop()
    assert not server.thread.is_alive()


def test_library_lights_are_green_yellow_or_red():
    from pluteus.adminweb import _root_light
    good = {"ok": True, "free": 500, "total": 1000, "seconds": 0.01}
    assert "pl-dot-ok" in _root_light(good)
    assert "pl-dot-warn" in _root_light(dict(good, seconds=5.0)) and "slow" in _root_light(dict(good, seconds=5.0))
    assert "pl-dot-warn" in _root_light(dict(good, free=20)) and "low disk" in _root_light(dict(good, free=20))
    assert "pl-dot-bad" in _root_light(dict(good, ok=False))
    assert "pl-dot-ok" in _root_light({"ok": True, "free": None, "total": None})      # unknown disk size is not a warning


def test_status_names_the_users_sessions_devices_and_locked_names(admin):
    client, app, _ = admin
    for i in range(5):
        app.state.limiter.failure("mallory", "", now=time.time())
    html = client.get("/", headers=BOSS).text
    row = html[html.index("Locked names"):]
    assert "mallory" in row[:row.index("</tr>")]
    users = html[html.index("<td>Users</td>"):]
    assert "alice" in users[:users.index("</tr>")]


def test_users_table_has_one_column_per_right(admin):
    client, _, _ = admin
    html = client.get("/users", headers=BOSS).text
    head = html[html.index("<thead>"):html.index("</thead>")]
    for label in ("User", "Folders", "Adult", "Uploads", "Admin"):
        assert "<th>%s</th>" % label in head
    assert "Rights" not in head and "pl-user-icon" in html


def test_sign_out_forgets_the_login_and_asks_the_browser_to_drop_it(admin):
    client, app, _ = admin
    assert client.get("/", headers=BOSS).status_code == 200
    assert app.state.auth.cached_user(BOSS["Authorization"]) is not None
    r = client.post("/logout", headers=dict(BOSS, Origin="http://testserver"))
    assert r.status_code == 401 and "Basic" in r.headers["www-authenticate"] and "Signed out" in r.text
    assert app.state.auth.cached_user(BOSS["Authorization"]) is None
    assert client.post("/logout", headers=BOSS).status_code == 403                  # still needs a same-site request
    assert "Sign out" in client.get("/", headers=BOSS).text

"""The app key as the password of reader apps (OPDS), WebDAV and downloads: read only, never an admin or an uploader."""
import pytest
from conftest import basic
from starlette.testclient import TestClient

from pluteus.adminapp import create_admin_app
from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import load_config

KEY = "alice-reader-key-123"


@pytest.fixture
def setup(tmp_path):
    books = tmp_path / "books"
    books.mkdir()
    (books / "a.epub").write_bytes(b"EPUB" * 20)
    text = ('[server]\nport = 8080\nadmin_port = 8101\nwebdav = true\ndata_dir = "%s"\nuploads_dir = "%s"\n\n[[roots]]\nname = "books"\npath = "%s"\n\n'
            '[[users]]\nname = "alice"\npassword_hash = "%s"\nroots = ["*"]\nadmin = true\n'
            % (str(tmp_path / "data").replace("\\", "/"), str(tmp_path / "up").replace("\\", "/"), str(books).replace("\\", "/"),
               hash_password("pw-alice", n=2 ** 4)))
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8", newline="")
    app = create_app(load_config(str(path)), config_path=str(path), reload_interval=0.0)
    app.state.state.set_appkey("alice", KEY)
    return TestClient(app), app, TestClient(create_admin_app(app), follow_redirects=False)


def test_the_key_works_as_a_basic_password_for_reading(setup):
    c, _, _ = setup
    key = basic("alice", KEY)
    assert c.get("/opds/books", headers=key).status_code == 200
    assert c.get("/dl/books/a.epub", headers=key).content.startswith(b"EPUB")
    assert c.request("PROPFIND", "/dav/books/", headers=key).status_code == 207
    assert c.get("/opds/books", headers=basic("alice", "pw-alice")).status_code == 200            # the real password still works
    assert c.get("/opds/books", headers=basic("alice", "wrong")).status_code == 401


def test_a_key_login_cannot_write_or_become_a_session(setup):
    c, _, _ = setup
    key = basic("alice", KEY)
    r = c.post("/upload/uploads", headers={**key, "Origin": "http://testserver"}, content=b"x")
    assert r.status_code in (403, 404)
    assert c.post("/settings", headers={**key, "Origin": "http://testserver"}, content="theme=gold").status_code == 403
    login = c.post("/login", data={"username": "alice", "password": KEY}, headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert login.status_code != 303 or "pluteus_session" not in login.headers.get("set-cookie", "")      # the form wants the password
    assert c.get("/", headers={"Accept": "text/html"}, follow_redirects=False).status_code in (303, 401)  # no cookie was given


def test_a_key_login_is_never_an_admin(setup):
    _, _, admin = setup
    assert admin.get("/", headers=basic("alice", KEY)).status_code in (401, 403)
    assert admin.get("/", headers=basic("alice", "pw-alice")).status_code == 200


def test_making_or_revoking_a_key_stops_old_logins_at_once(setup):
    c, app, admin = setup
    key = basic("alice", KEY)
    assert c.get("/opds/books", headers=key).status_code == 200                                    # now cached
    r = admin.post("/users/appkey/revoke", content="name=alice", headers={**basic("alice", "pw-alice"), "origin": "http://testserver",
                                                                         "content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 303
    assert c.get("/opds/books", headers=key).status_code == 401

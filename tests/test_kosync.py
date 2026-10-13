"""KOReader progress sync (kosync): login with the app key, one position per user and book."""
import hashlib

import pytest
from starlette.testclient import TestClient

from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import parse_config

KEY = "reading-key-for-alice"
HASH = hashlib.md5(KEY.encode()).hexdigest()
DOC = "0123456789abcdef0123456789abcdef"
AUTH = {"x-auth-user": "alice", "x-auth-key": HASH, "Accept": "application/vnd.koreader.v1+json"}


@pytest.fixture
def client(tmp_path):
    (tmp_path / "b").mkdir()
    cfg = parse_config({"server": {"kosync": True}, "roots": [{"name": "b", "path": str(tmp_path / "b")}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]},
                                  {"name": "bob", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]}]})
    app = create_app(cfg)
    app.state.state.set_appkey("alice", KEY)
    return TestClient(app)


def put(client, headers=AUTH, **over):
    body = {"document": DOC, "progress": "12", "percentage": 0.25, "device": "PocketBook", "device_id": "abc", **over}
    return client.put("/kosync/syncs/progress", json=body, headers=headers)


def test_it_is_off_by_default(tmp_path):
    cfg = parse_config({"roots": [{"name": "b", "path": str(tmp_path)}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]}]})
    assert TestClient(create_app(cfg)).get("/kosync/users/auth", headers=AUTH).status_code == 404


def test_login_uses_the_md5_of_the_key(client):
    assert client.get("/kosync/users/auth", headers=AUTH).json() == {"authorized": "OK"}
    assert client.get("/kosync/users/auth", headers={**AUTH, "x-auth-key": hashlib.md5(b"wrong").hexdigest()}).status_code == 401
    assert client.get("/kosync/users/auth", headers={**AUTH, "x-auth-user": "bob"}).status_code == 401       # bob has no key
    assert client.get("/kosync/users/auth").status_code == 401


def test_registering_from_the_device_is_refused_with_an_explanation(client):
    r = client.post("/kosync/users/create", json={"username": "x", "password": HASH})
    assert r.status_code == 403 and "admin page" in r.json()["message"]


def test_a_position_is_stored_and_read_back(client):
    assert client.get("/kosync/syncs/progress/" + DOC, headers=AUTH).json() == {"document": DOC}      # nothing yet
    r = put(client)
    assert r.status_code == 200 and r.json()["document"] == DOC and r.json()["timestamp"] > 0
    got = client.get("/kosync/syncs/progress/" + DOC, headers=AUTH).json()
    assert (got["progress"], got["percentage"], got["device"], got["device_id"]) == ("12", 0.25, "PocketBook", "abc")
    put(client, progress="40", percentage=0.5, device="Phone")
    assert client.get("/kosync/syncs/progress/" + DOC, headers=AUTH).json()["percentage"] == 0.5          # the newest wins


def test_positions_are_per_user(client):
    put(client)
    client.app.state.state.set_appkey("bob", "bobs-key")
    other = {"x-auth-user": "bob", "x-auth-key": hashlib.md5(b"bobs-key").hexdigest()}
    assert client.get("/kosync/syncs/progress/" + DOC, headers=other).json() == {"document": DOC}


@pytest.mark.parametrize("over", [{"document": "../x"}, {"document": ""}, {"percentage": 2}, {"percentage": "x"},
                                  {"progress": "p" * 600}])
def test_bad_bodies_are_refused(client, over):
    assert put(client, **over).status_code == 400


def test_wrong_logins_are_throttled(client):
    for _ in range(10):
        client.get("/kosync/users/auth", headers={**AUTH, "x-auth-key": "0" * 32})
    assert client.get("/kosync/users/auth", headers=AUTH).status_code == 401                              # locked, even with the right key

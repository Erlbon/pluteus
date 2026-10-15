"""HTTP Basic logins from reader apps: UTF-8 and the Latin-1 that older apps send, and a log line for every refusal (never the password)."""
import base64
import logging

from starlette.testclient import TestClient

from pluteus.app import create_app
from pluteus.auth import Authenticator, hash_password
from pluteus.config import parse_config

PASSWORD = "bjørn-påss-1"


def client(tmp_path):
    (tmp_path / "books").mkdir()
    cfg = parse_config({"roots": [{"name": "books", "path": str(tmp_path / "books")}],
                        "users": [{"name": "erlend", "password_hash": hash_password(PASSWORD, n=2 ** 4), "roots": ["*"]}]})
    return TestClient(create_app(cfg))


def header(name: str, password: str, encoding: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(("%s:%s" % (name, password)).encode(encoding)).decode("ascii")}


def test_utf8_and_latin1_logins_both_work(tmp_path):
    c = client(tmp_path)
    assert c.get("/opds", headers=header("erlend", PASSWORD, "utf-8")).status_code == 200
    assert c.get("/opds", headers=header("erlend", PASSWORD, "latin-1")).status_code == 200            # an older reader app


def test_a_wrong_password_is_refused_and_logged_without_the_password(tmp_path, caplog):
    c = client(tmp_path)
    with caplog.at_level(logging.WARNING):
        assert c.get("/opds", headers=header("erlend", "not-the-password", "utf-8")).status_code == 401
        assert c.get("/opds", headers=header("nobody", "x", "utf-8")).status_code == 401
        assert c.get("/opds", headers={"Authorization": "Basic !!!not-base64"}).status_code == 401
    text = caplog.text
    assert "login refused for 'erlend'" in text and "wrong password" in text and "no such user" in text and "could not be read" in text
    assert "not-the-password" not in text


def test_parse_basic_reads_both_encodings():
    for encoding in ("utf-8", "latin-1"):
        assert Authenticator.parse_basic(header("erlend", PASSWORD, encoding)["Authorization"]) == ("erlend", PASSWORD)
    assert Authenticator.parse_basic("Basic " + base64.b64encode(b"no-colon").decode()) is None

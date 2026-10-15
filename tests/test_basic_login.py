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


def test_a_name_with_the_wrong_letter_case_or_a_space_still_means_the_user(tmp_path):
    c = client(tmp_path)
    for typed in ("Erlend", "ERLEND", " erlend", "erlend "):
        assert c.get("/opds", headers=header(typed, PASSWORD, "utf-8")).status_code == 200, typed
    assert c.get("/opds", headers=header("Erlend", PASSWORD.upper(), "utf-8")).status_code == 401           # the password is never loosened
    assert c.get("/opds", headers=header("Erlend", PASSWORD + " ", "utf-8")).status_code == 401


def test_loosened_names_still_count_against_the_same_account(tmp_path):
    c = client(tmp_path)
    for number, typed in enumerate(("erlend", "Erlend", " erlend", "ERLEND ", "erlend  ")):
        c.get("/opds", headers=header(typed, "wrong-%d" % number, "utf-8"))
    locked = c.get("/opds", headers=header("erlend  ", "wrong-again", "utf-8"))
    assert locked.status_code == 429                                                                          # one lock for every spelling


def test_ambiguous_names_are_not_guessed(tmp_path):
    (tmp_path / "books").mkdir()
    cfg = parse_config({"roots": [{"name": "books", "path": str(tmp_path / "books")}],
                        "users": [{"name": "Sam", "password_hash": hash_password("pw-one-1", n=2 ** 4), "roots": ["*"]},
                                  {"name": "sam", "password_hash": hash_password("pw-two-2", n=2 ** 4), "roots": ["*"]}]})
    c = TestClient(create_app(cfg))
    assert c.get("/opds", headers=header("sam", "pw-two-2", "utf-8")).status_code == 200                    # an exact name is exact
    assert c.get("/opds", headers=header("Sam", "pw-one-1", "utf-8")).status_code == 200
    assert c.get("/opds", headers=header("SAM", "pw-one-1", "utf-8")).status_code == 401                    # two users match: nothing is guessed


def test_the_log_hints_at_the_usual_reader_app_slips(tmp_path, caplog):
    c = client(tmp_path)
    with caplog.at_level(logging.WARNING):
        c.get("/opds", headers=header("erlend", PASSWORD + " ", "utf-8"))
        c.get("/opds", headers=header("Erlend ", "", "utf-8"))
    assert "wrong password (%d characters; the password has a space" % (len(PASSWORD) + 1) in caplog.text
    assert "the password is empty" in caplog.text and PASSWORD not in caplog.text


def test_an_opds_401_carries_an_authentication_document_and_the_basic_challenge(tmp_path):
    import json
    c = client(tmp_path)
    for path in ("/opds", "/opds/", "/opds/books"):
        r = c.get(path)
        assert r.status_code == 401 and r.headers["www-authenticate"] == 'Basic realm="pluteus"', path
        assert r.headers["content-type"].startswith("application/opds-authentication+json")
        doc = json.loads(r.text)
        assert doc["authentication"][0]["type"] == "http://opds-spec.org/auth/basic" and doc["title"] == "pluteus" and doc["id"]
    assert c.get("/healthz").status_code == 200
    other = c.get("/download-nothing")                                                  # everything else keeps the plain answer
    assert other.status_code in (401, 404) and "opds-authentication" not in other.headers.get("content-type", "")
    assert c.get("/opds", headers=header("erlend", PASSWORD, "utf-8")).status_code == 200

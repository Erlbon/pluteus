"""Subsonic radio list: stations that need the relay (plain http, stored login) are offered through the server with the app's own login."""
from urllib.parse import parse_qs, urlsplit

from starlette.testclient import TestClient
from test_login_web import make
from test_relay import AUDIO, fresh_cache, station  # noqa: F401

KEY = "app-key-for-alice-123"


def build(cfg, tmp_path, library, **server):
    client, app = make(cfg, tmp_path, subsonic=True, uploads_dir=str(tmp_path / "up"), **server)
    app.state.state.set_appkey("alice", KEY)
    return TestClient(app, headers={"host": "pluteus.example"}), library / "books" / "Radio"


def listing(c, **auth):
    r = c.get("/rest/getInternetRadioStations.view", params={"f": "json", **auth})
    return r.json()["subsonic-response"]["internetRadioStations"].get("internetRadioStation", [])


def test_http_stations_go_through_the_server_and_play(cfg, tmp_path, library, station):
    c, radio = build(cfg, tmp_path, library, relay=True)
    radio.mkdir(parents=True)
    (radio / "Plain.m3u").write_text(station + "/stream\n", encoding="utf-8")
    (radio / "Secure.m3u").write_text("https://example.org/s\n", encoding="utf-8")
    found = {s["name"]: s for s in listing(c, apiKey=KEY)}
    assert found["Secure"]["streamUrl"] == "https://example.org/s"                       # https: direct, as before
    url = urlsplit(found["Plain"]["streamUrl"])
    assert (url.scheme, url.netloc, url.path) == ("http", "pluteus.example", "/rest/stream")
    assert parse_qs(url.query)["apiKey"] == [KEY] and parse_qs(url.query)["id"] == [found["Plain"]["id"]]
    played = c.get(url.path + "?" + url.query)
    assert played.status_code == 200 and played.content == AUDIO and played.headers["content-type"] == "audio/mpeg"


def test_a_plain_password_login_is_never_copied_into_an_address(cfg, tmp_path, library, station):
    c, radio = build(cfg, tmp_path, library, relay=True)
    radio.mkdir(parents=True)
    (radio / "Plain.m3u").write_text(station + "/stream\n", encoding="utf-8")
    assert listing(c, u="alice", p=KEY)[0]["streamUrl"] == station + "/stream"
    assert "_relay" not in listing(c, apiKey=KEY)[0]


def test_relay_off_leaves_the_list_alone_and_stream_refuses_a_station(cfg, tmp_path, library, station):
    c, radio = build(cfg, tmp_path, library)
    radio.mkdir(parents=True)
    (radio / "Plain.m3u").write_text(station + "/stream\n", encoding="utf-8")
    first = listing(c, apiKey=KEY)[0]
    assert first["streamUrl"] == station + "/stream"
    r = c.get("/rest/stream.view", params={"id": first["id"], "apiKey": KEY, "f": "json"})
    assert r.status_code == 200 and r.json()["subsonic-response"]["status"] == "failed"

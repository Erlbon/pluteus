"""The admin page's relay box: open streams, Stop, totals, key fingerprint and stored-stream counts (never a stored value)."""
from conftest import basic
from test_adminweb import BOSS, FORM, make_config
from starlette.testclient import TestClient

from pluteus import relay
from pluteus.adminapp import create_admin_app
from pluteus.app import create_app
from pluteus.config import load_config


def test_limiter_registry_counts_and_stops():
    lim = relay.Limiter(2, 4)
    a = lim.register("alice", "radio/one.m3u#0", "radio")
    b = lim.register("bob", "radio/two.m3u#1", "radio")
    a["bytes"] += 1000
    assert {s["user"] for s in lim.streams()} == {"alice", "bob"}
    assert lim.totals()["alice"] == 1000
    assert lim.stop(a["id"]) and a["stop"]
    lim.unregister(a)
    assert not lim.stop(a["id"])                      # already gone
    assert [s["user"] for s in lim.streams()] == ["bob"]
    assert lim.totals()["alice"] == 1000        # kept after the stream ended
    lim.unregister(b)
    assert lim.streams() == []


def make(tmp_path, relay_on):
    path = make_config(tmp_path)
    if relay_on:
        path.write_text(path.read_text(encoding="utf-8").replace("[server]\n", "[server]\nrelay = true\n", 1), encoding="utf-8", newline="")
    cfg = load_config(str(path))
    app = create_app(cfg, config_path=str(path), reload_interval=0.0)
    return TestClient(create_admin_app(app), follow_redirects=False), app


def test_no_relay_box_when_off(tmp_path):
    client, _ = make(tmp_path, False)
    assert "Stream relay</" not in client.get("/", headers=BOSS).text.replace("Stream relay (radio", "")
    assert "Carried since the start" not in client.get("/", headers=BOSS).text


def test_relay_box_shows_streams_and_counts_only(tmp_path):
    client, app = make(tmp_path, True)
    app.state.state.stream_put("alice", "secret-name", b"sealed-blob", "x")
    handle = app.state.relay_limits.register("alice", "radio/a.m3u#0", "radio")
    handle["bytes"] = 2048
    text = client.get("/", headers=BOSS).text
    assert "Carried since the start" in text and "radio/a.m3u#0" in text
    assert "alice (1)" in text                         # a count, not a name or value
    assert "secret-name" not in text and "sealed" not in text
    assert "fingerprint" in text
    r = client.post("/relay/stop", headers={**BOSS, **FORM}, content="id=%d" % handle["id"])
    assert r.status_code == 303 and handle["stop"]
    r = client.post("/relay/stop", headers={**BOSS, **FORM}, content="id=999")
    assert r.status_code == 303 and "ok=nothing" in r.headers["location"]


def test_stop_needs_an_admin(tmp_path):
    client, _ = make(tmp_path, True)
    assert client.post("/relay/stop", headers={**basic("alice", "alice-password-1"), **FORM}, content="id=1").status_code in (401, 403)
    assert client.post("/relay/stop", headers=FORM, content="id=1").status_code == 401

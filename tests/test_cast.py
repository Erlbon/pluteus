"""Casting on the server: config, signed media addresses, the Hub, the agent endpoints, the play and control API, the media route."""
import dataclasses
import time

import pytest
from conftest import basic
from starlette.testclient import TestClient
from test_login_web import make
from test_uploads import ORIGIN

from pluteus import cast, keypath
from pluteus.config import CastSite, ConfigError, parse_config

TOKEN = "agent-token-for-tests-0123456789"
SONG = bytes(range(256)) * 400                      # 102,400 bytes of "music"
ALICE = basic("alice", "pw-alice")
AGENT = {"Authorization": "Bearer " + TOKEN}
KINDS = [{"id": "tv-1", "name": "Living room", "state": "idle"}, {"id": "kitchen", "name": "Kitchen", "state": "idle"}]


def site(name="Cabin", users=()):
    return CastSite(name, cast.hash_token(TOKEN), tuple(users))


@pytest.fixture
def library_song(library):
    path = library / "books" / "Good Author" / "song.mp3"
    path.write_bytes(SONG)
    (library / "books" / "Good Author" / "film.mkv").write_bytes(b"mkv")
    return path


@pytest.fixture
def served(cfg, tmp_path, library_song):
    client, app = make(cfg, tmp_path, cast=True, cast_sites=(site(),))
    return client, app


def online(client, devices=KINDS):
    r = client.post("/cast-agent/status", json={"devices": devices}, headers=AGENT)
    assert r.status_code == 200
    return r


def play(client, who=ALICE, **over):
    body = {"site": "Cabin", "device": "tv-1", "root": "books", "path": "Good Author/song.mp3", **over}
    return client.post("/api/cast/play", json=body, headers={**who, **ORIGIN})


# ---- config -------------------------------------------------------------------------------------------------------
def base_config(**extra):
    from conftest import FAST
    from pluteus.auth import hash_password
    return {"server": {"cast": True}, "roots": [{"name": "a", "path": "."}],
            "users": [{"name": "alice", "password_hash": hash_password("pw-alice", **FAST), "roots": ["*"]}], **extra}


def test_config_reads_sites():
    cfg = parse_config(base_config(cast_sites=[{"name": "Cabin", "token_hash": cast.hash_token(TOKEN), "users": ["alice"]}]), check_roots=False)
    assert cfg.cast and cfg.cast_sites == (CastSite("Cabin", cast.hash_token(TOKEN), ("alice",)),)
    assert parse_config(base_config(), check_roots=False).cast_sites == ()


@pytest.mark.parametrize("entry,message", [
    ({"name": "Cabin"}, "token_hash"),
    ({"name": "Cabin", "token_hash": "plain-secret"}, "token_hash"),
    ({"name": "bad name!", "token_hash": cast.hash_token(TOKEN)}, "name"),
    ({"name": "Cabin", "token_hash": cast.hash_token(TOKEN), "users": ["nobody"]}, "unknown user"),
    ({"name": "Cabin", "token_hash": cast.hash_token(TOKEN), "extra": 1}, "unknown key"),
])
def test_config_refuses_bad_sites(entry, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(base_config(cast_sites=[entry]), check_roots=False)


def test_config_refuses_duplicate_sites():
    entry = {"name": "Cabin", "token_hash": cast.hash_token(TOKEN)}
    with pytest.raises(ConfigError, match="duplicate"):
        parse_config(base_config(cast_sites=[entry, entry]), check_roots=False)


# ---- tokens and the key ---------------------------------------------------------------------------------------------
def test_media_token_round_trip_and_tamper():
    key = b"k" * 32
    token = cast.sign(key, "books", ("Good Author", "song.mp3"), "alice")
    assert cast.verify(key, token) == ("books", ("Good Author", "song.mp3"), "alice")
    body, _, signature = token.partition(".")
    assert cast.verify(key, body + "." + signature[:-2] + "AA") is None
    assert cast.verify(b"x" * 32, token) is None                       # another key
    assert cast.verify(key, "garbage") is None and cast.verify(key, "") is None
    assert cast.verify(key, token, now=time.time() + cast.TOKEN_SECONDS + 5) is None      # lapsed


def test_key_is_kept_in_the_data_folder(tmp_path):
    first = cast.load_key(str(tmp_path / "data"))
    assert cast.load_key(str(tmp_path / "data")) == first and len(first) == 32
    assert cast.load_key("") != cast.load_key("")                       # no data folder: a key for this run only
    (tmp_path / "data" / "cast.key").write_text("not a key", encoding="ascii")
    with pytest.raises(cast.CastError):
        cast.load_key(str(tmp_path / "data"))                            # a damaged key is never replaced


def test_logs_never_hold_a_media_token():
    assert keypath.masked("/cast/abc.def/song.mp3") == "/cast/***/song.mp3"
    assert keypath.masked("/k/secretkey1/opds") == "/k/***/opds"
    assert cast.hash_token("a") != cast.hash_token("b") and cast.hash_token("a").startswith("sha256$")


# ---- the hub ------------------------------------------------------------------------------------------------------
def test_hub_cleans_what_an_agent_reports():
    hub = cast.Hub([site()])
    hub.report("Cabin", [{"id": "ok", "name": "A\x00B", "state": "playing", "position": 5, "volume": 55.4},
                         {"id": "bad id!"}, "junk", {"id": "x", "state": "exploding", "position": -3, "title": "t" * 999}])
    devices = hub.snapshot("alice")[0]["devices"]
    assert [d["id"] for d in devices] == ["ok", "x"]
    assert devices[0]["name"] == "AB" and devices[0]["volume"] == 55 and devices[0]["state"] == "playing"
    assert devices[1]["state"] == "idle" and devices[1]["position"] is None and len(devices[1]["title"]) == 200


def test_hub_refuses_commands_it_should():
    hub = cast.Hub([site("Cabin", ["alice"])])
    with pytest.raises(cast.CastError) as e:
        hub.enqueue("Cabin", "alice", "tv-1", "pause")
    assert e.value.status == 503                                          # nobody has reported yet: offline
    hub.report("Cabin", KINDS)
    with pytest.raises(cast.CastError) as e:
        hub.enqueue("Cabin", "alice", "nope", "pause")
    assert e.value.status == 404
    with pytest.raises(cast.CastError) as e:
        hub.enqueue("Cabin", "alice", "tv-1", "reboot")
    assert e.value.status == 400
    with pytest.raises(cast.CastError) as e:
        hub.enqueue("Cabin", "bob", "tv-1", "pause")                      # not on the site's list
    assert e.value.status == 404
    assert hub.snapshot("bob") == [] and hub.snapshot("alice")[0]["online"] is True


def test_hub_queue_is_taken_once_and_late_commands_are_dropped(monkeypatch):
    hub = cast.Hub([site()])
    hub.report("Cabin", KINDS)
    hub.enqueue("Cabin", "alice", "tv-1", "pause")
    hub.enqueue("Cabin", "alice", "tv-1", "volume", value=30)
    assert [c["op"] for c in hub.take("Cabin")] == ["pause", "volume"] and hub.take("Cabin") == []
    hub.enqueue("Cabin", "alice", "tv-1", "pause")
    monkeypatch.setattr(cast, "COMMAND_SECONDS", -1)
    assert hub.take("Cabin") == []


def test_hub_queue_has_a_limit():
    hub = cast.Hub([site()])
    hub.report("Cabin", KINDS)
    for _ in range(cast.MAX_QUEUE):
        hub.enqueue("Cabin", "alice", "tv-1", "pause")
    with pytest.raises(cast.CastError) as e:
        hub.enqueue("Cabin", "alice", "tv-1", "pause")
    assert e.value.status == 429


def test_agent_goes_offline_after_a_while(monkeypatch):
    hub = cast.Hub([site()])
    assert not hub.online("Cabin")
    hub.report("Cabin", KINDS)
    assert hub.online("Cabin")
    monkeypatch.setattr(cast, "ONLINE_SECONDS", -1)
    assert not hub.online("Cabin") and hub.snapshot("alice")[0]["devices"] == []


# ---- the app: off by default ---------------------------------------------------------------------------------------------
def test_casting_is_off_by_default(cfg, tmp_path):
    client, _ = make(cfg, tmp_path)
    assert client.get("/api/cast", headers=ALICE).status_code == 404
    assert client.get("/cast-agent/poll", headers=AGENT).status_code == 404
    assert client.get("/cast/x.y/song.mp3").status_code == 404


# ---- the agent endpoints ----------------------------------------------------------------------------------------------
def test_agent_needs_its_token(served):
    client, _ = served
    assert client.get("/cast-agent/poll?wait=0").status_code == 401
    assert client.get("/cast-agent/poll?wait=0", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/cast-agent/poll?wait=0", headers=ALICE).status_code == 401           # a user's login is not an agent token
    assert client.post("/cast-agent/status", json={"devices": []}).status_code == 401
    assert client.get("/cast-agent/poll?wait=0", headers=AGENT).json() == {"commands": [], "protocol": 1}


def test_wrong_tokens_are_counted_like_wrong_passwords(served):
    client, _ = served
    codes = [client.get("/cast-agent/poll?wait=0", headers={"Authorization": "Bearer wrong"}).status_code for _ in range(40)]
    assert 429 in codes
    assert client.get("/cast-agent/poll?wait=0", headers=AGENT).status_code == 429          # the address is locked for now, even with the real token


def test_agent_status_must_be_json(served):
    client, _ = served
    assert client.post("/cast-agent/status", content=b"nonsense", headers=AGENT).status_code == 400
    assert client.post("/cast-agent/status", json=[1], headers=AGENT).status_code == 400


# ---- the user API ---------------------------------------------------------------------------------------------------
def test_state_lists_sites_and_devices(served):
    client, _ = served
    assert client.get("/api/cast").status_code == 401
    before = client.get("/api/cast", headers=ALICE).json()
    assert before == {"sites": [{"name": "Cabin", "online": False, "devices": []}]}
    online(client, [{"id": "tv-1", "name": "Living room", "state": "playing", "position": 12.5, "duration": 200, "volume": 40, "title": "Song"}])
    after = client.get("/api/cast", headers=ALICE).json()["sites"][0]
    assert after["online"] and after["devices"][0]["state"] == "playing" and after["devices"][0]["position"] == 12.5


def test_a_site_can_be_limited_to_some_users(cfg, tmp_path, library_song):
    client, _ = make(cfg, tmp_path, cast=True, cast_sites=(site("Cabin", ["bob"]),))
    online(client)
    assert client.get("/api/cast", headers=ALICE).json() == {"sites": []}
    assert client.get("/api/cast", headers=basic("bob", "pw-bob")).json()["sites"][0]["name"] == "Cabin"
    r = play(client)
    assert r.status_code == 404 and "no such place" in r.json()["error"]


def test_play_queues_a_command_with_a_signed_address(served):
    client, _ = served
    online(client)
    r = play(client, start=30)
    assert r.status_code == 200 and r.json()["ok"] and r.json()["title"] == "song"
    [command] = client.get("/cast-agent/poll?wait=0", headers=AGENT).json()["commands"]
    assert command["op"] == "play" and command["device"] == "tv-1" and command["mime"] == "audio/mpeg" and command["start"] == 30
    assert command["url"].startswith("/cast/") and command["url"].endswith("/song.mp3")
    assert client.get("/cast-agent/poll?wait=0", headers=AGENT).json()["commands"] == []     # taken once


def test_play_refuses_what_it_should(served):
    client, _ = served
    assert play(client).status_code == 503                                             # no agent connected yet
    online(client)
    assert play(client, device="nope").status_code == 404
    assert play(client, site="Elsewhere").status_code == 404
    assert play(client, path="Good Author/film.mkv").status_code == 415                # not playable without converting
    assert play(client, path="Good Author/Real Book.epub").status_code == 415          # not music or video
    assert play(client, path="Good Author/missing.mp3").status_code == 404
    assert play(client, path="../secret.txt").status_code in (400, 404)
    assert play(client, start="soon").status_code == 400
    assert play(client, who=basic("carol", "pw-carol")).status_code == 404             # carol may not open the books root
    assert play(client, who={}).status_code == 401
    assert client.post("/api/cast/play", json={}, headers=ALICE).status_code == 403    # no Origin: refused like every other write


def test_control_commands(served):
    client, _ = served
    online(client)

    def control(**body):
        return client.post("/api/cast/control", json={"site": "Cabin", "device": "tv-1", **body}, headers={**ALICE, **ORIGIN})
    assert control(op="pause").status_code == 200
    assert control(op="seek", value=95).status_code == 200
    assert control(op="volume", value=33.6).status_code == 200
    assert control(op="volume", value=101).status_code == 400
    assert control(op="seek", value=-1).status_code == 400
    assert control(op="seek").status_code == 400
    assert control(op="play").status_code == 400
    assert control(op="explode").status_code == 400
    ops = [(c["op"], c.get("value")) for c in client.get("/cast-agent/poll?wait=0", headers=AGENT).json()["commands"]]
    assert ops == [("pause", None), ("seek", 95.0), ("volume", 34)]


# ---- the media route ------------------------------------------------------------------------------------------------
def media_url(client):
    online(client)
    play(client)
    return client.get("/cast-agent/poll?wait=0", headers=AGENT).json()["commands"][0]["url"]


def test_media_is_served_with_the_token_alone(served):
    client, _ = served
    url = media_url(client)
    fresh = TestClient(client.app)                                                      # no login, no cookie: what a Chromecast has
    r = fresh.get(url)
    assert r.status_code == 200 and r.content == SONG
    assert r.headers["content-type"] == "audio/mpeg" and r.headers["access-control-allow-origin"] == "*"
    assert "private" in r.headers["cache-control"] and r.headers["accept-ranges"] == "bytes"
    head = fresh.head(url)
    assert head.status_code == 200 and head.headers["content-length"] == str(len(SONG))
    part = fresh.get(url, headers={"Range": "bytes=100-199"})
    assert part.status_code == 206 and part.content == SONG[100:200] and part.headers["content-range"] == "bytes 100-199/%d" % len(SONG)
    pre = fresh.options(url, headers={"Origin": "http://cast.example", "Access-Control-Request-Method": "GET"})
    assert pre.status_code == 204 and "GET" in pre.headers["access-control-allow-methods"]


def test_media_refuses_bad_tokens_and_vanished_files(served, library_song):
    client, _ = served
    url = media_url(client)
    fresh = TestClient(client.app)
    token = url.split("/")[2]
    assert fresh.get("/cast/" + token[:-3] + "AAA/song.mp3").status_code == 404
    assert fresh.get("/cast/nonsense/song.mp3").status_code == 404
    library_song.unlink()
    assert fresh.get(url).status_code == 404


def test_media_follows_the_users_current_rights(cfg, tmp_path, library_song):
    """The token names a file for a user, but the file is looked up again with that user's rights every time."""
    changed = dataclasses.replace(cfg, users={**cfg.users, "alice": dataclasses.replace(cfg.users["alice"], roots=("books",))})
    client, app = make(changed, tmp_path, cast=True, cast_sites=(site(),))
    url = media_url(client)
    assert TestClient(app).get(url).status_code == 200
    app.state.auth.users["alice"] = dataclasses.replace(app.state.auth.users["alice"], roots=("comics",))
    assert TestClient(app).get(url).status_code == 404
    del app.state.auth.users["alice"]
    assert TestClient(app).get(url).status_code == 404


def test_media_survives_a_restart_with_a_data_folder(cfg, tmp_path, library_song):
    first, app = make(cfg, tmp_path, cast=True, cast_sites=(site(),))
    url = media_url(first)
    second, _ = make(cfg, tmp_path, cast=True, cast_sites=(site(),))                    # same data folder: same cast.key
    assert TestClient(second.app).get(url).content == SONG


def test_the_shared_anonymous_user_cannot_cast(cfg, tmp_path, library_song):
    client, _ = make(cfg, tmp_path, cast=True, cast_sites=(site(),), anonymous_user="alice")
    online(client)
    assert client.get("/api/cast").status_code == 404                                   # no login: acts as alice, who is the shared user
    assert client.get("/api/cast", headers=basic("bob", "pw-bob")).status_code == 200
    body = {"site": "Cabin", "device": "tv-1", "root": "books", "path": "Good Author/song.mp3"}
    assert client.post("/api/cast/play", json=body, headers=ORIGIN).status_code == 404
    assert client.post("/api/cast/control", json={**body, "op": "pause"}, headers=ORIGIN).status_code == 404

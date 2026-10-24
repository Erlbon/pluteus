"""Casting: config, signed media addresses, the device Manager, the play and control API, the media route."""
import dataclasses
import time

import pytest
from conftest import basic
from starlette.testclient import TestClient
from test_login_web import make
from test_uploads import ORIGIN

from pluteus import cast, castdevices, keypath
from pluteus.config import CastDevice, ConfigError, parse_config

SONG = bytes(range(256)) * 400                      # 102,400 bytes of "music"
ALICE = basic("alice", "pw-alice")
DEVICES = (CastDevice("Living room", "192.168.1.50"), CastDevice("Kitchen", "192.168.1.51"))


class FakeBackend(castdevices.Backend):
    """Stands in for a kind of device: records what it was told, connects at once unless told otherwise."""
    kind = "cast"
    types = castdevices.CastBackend.types

    def __init__(self):
        self.calls, self.up, self.fail, self.refuse_connect = [], set(), None, None
        self.state = {}

    def connect(self, device):
        if self.refuse_connect:
            raise ConnectionError(self.refuse_connect)
        self.up.add(device.name)

    def connected(self, name):
        return name in self.up

    def status(self, name):
        return {"state": "idle", "position": None, "duration": None, "volume": 20, "title": "", **self.state.get(name, {})}

    def _do(self, *call):
        if self.fail:
            raise RuntimeError(self.fail)
        self.calls.append(call)

    def play(self, name, url, mime, title, start):
        self._do("play", name, url, mime, title, start)
        self.state[name] = {"state": "playing", "title": title, "position": start, "duration": 200.0}

    def pause(self, name):
        self._do("pause", name)

    def resume(self, name):
        self._do("resume", name)

    def stop(self, name):
        self._do("stop", name)

    def seek(self, name, seconds):
        self._do("seek", name, seconds)

    def set_volume(self, name, percent):
        self._do("volume", name, percent)


@pytest.fixture
def library_song(library):
    path = library / "books" / "Good Author" / "song.mp3"
    path.write_bytes(SONG)
    (library / "books" / "Good Author" / "film.mkv").write_bytes(b"mkv")
    return path


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setitem(castdevices.BACKENDS, "cast", FakeBackend)


def served(cfg, tmp_path, devices=DEVICES, **changes):
    """(client, app, the fake backend) with casting on and every device connected."""
    client, app = make(cfg, tmp_path, cast=True, cast_devices=devices, **changes)
    manager = app.state.cast_manager
    manager.start()
    for _ in range(100):
        if manager.online_count() == len(devices):
            break
        time.sleep(0.02)
    return client, app, manager._backends["cast"]


@pytest.fixture
def cast_app(cfg, tmp_path, library_song, fake):
    return served(cfg, tmp_path)


def play(client, who=ALICE, **over):
    body = {"device": "Living room", "root": "books", "path": "Good Author/song.mp3", **over}
    return client.post("/api/cast/play", json=body, headers={**who, **ORIGIN})


def control(client, **body):
    return client.post("/api/cast/control", json={"device": "Living room", **body}, headers={**ALICE, **ORIGIN})


# ---- config -------------------------------------------------------------------------------------------------------
def base_config(**extra):
    from conftest import FAST
    from pluteus.auth import hash_password
    return {"server": {"cast": True}, "roots": [{"name": "a", "path": "."}],
            "users": [{"name": "alice", "password_hash": hash_password("pw-alice", **FAST), "roots": ["*"]}], **extra}


def test_config_reads_devices():
    cfg = parse_config(base_config(cast_devices=[{"name": "TV", "address": "192.168.1.50", "users": ["alice"]}, {"name": "Den", "address": "den.lan", "kind": "cast"}]),
                       check_roots=False)
    assert cfg.cast and cfg.cast_devices == (CastDevice("TV", "192.168.1.50", "cast", ("alice",)), CastDevice("Den", "den.lan", "cast", ()))
    assert parse_config(base_config(), check_roots=False).cast_devices == () and not parse_config(base_config(), check_roots=False).cast_media_base


@pytest.mark.parametrize("entry,message", [
    ({"name": "TV"}, "address"),
    ({"name": "TV", "address": "not an address!"}, "address"),
    ({"name": "", "address": "1.2.3.4"}, "name"),
    ({"name": "TV", "address": "1.2.3.4", "kind": "airplay"}, "kind"),
    ({"name": "TV", "address": "1.2.3.4", "users": ["nobody"]}, "unknown user"),
    ({"name": "TV", "address": "1.2.3.4", "extra": 1}, "unknown key"),
])
def test_config_refuses_bad_devices(entry, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(base_config(cast_devices=[entry]), check_roots=False)


def test_config_refuses_duplicate_devices_and_bad_media_base():
    entry = {"name": "TV", "address": "1.2.3.4"}
    with pytest.raises(ConfigError, match="duplicate"):
        parse_config(base_config(cast_devices=[entry, entry]), check_roots=False)
    for bad in ("192.168.1.10:8100", "http://192.168.1.10:8100/pluteus", "ftp://x", "http://a b"):
        config = base_config()
        config["server"]["cast_media_base"] = bad
        with pytest.raises(ConfigError, match="cast_media_base"):
            parse_config(config, check_roots=False)
    config = base_config()
    config["server"]["cast_media_base"] = "http://192.168.1.10:8100/"
    assert parse_config(config, check_roots=False).cast_media_base == "http://192.168.1.10:8100"


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


# ---- the manager -----------------------------------------------------------------------------------------------------
def manager_with(devices=DEVICES, backend=None):
    backend = backend or FakeBackend()
    return castdevices.Manager(devices, {"cast": backend}), backend


def test_manager_checks_who_and_what_before_it_commands():
    manager, backend = manager_with((CastDevice("TV", "1.2.3.4", users=("alice",)),))
    with pytest.raises(cast.CastError) as e:
        manager.command("alice", "TV", "pause")
    assert e.value.status == 503                                          # not connected yet
    backend.up.add("TV")
    with pytest.raises(cast.CastError) as e:
        manager.command("bob", "TV", "pause")                              # not on the device's list
    assert e.value.status == 404
    with pytest.raises(cast.CastError) as e:
        manager.command("alice", "Nope", "pause")
    assert e.value.status == 404
    with pytest.raises(cast.CastError) as e:
        manager.command("alice", "TV", "reboot")
    assert e.value.status == 400
    manager.command("alice", "TV", "volume", value=30)
    manager.command("alice", "TV", "seek", value=61)
    assert backend.calls == [("volume", "TV", 30), ("seek", "TV", 61.0)]
    assert manager.snapshot("bob") == [] and manager.snapshot("alice")[0]["name"] == "TV"


def test_manager_turns_a_backend_failure_into_a_sentence():
    manager, backend = manager_with((CastDevice("TV", "1.2.3.4"),))
    backend.up.add("TV")
    backend.fail = "the TV said no"
    with pytest.raises(cast.CastError) as e:
        manager.command("alice", "TV", "pause")
    assert e.value.status == 502 and "TV did not accept that (the TV said no)" in e.value.message


def test_manager_keeps_trying_to_reach_a_device_and_says_why_not(monkeypatch):
    monkeypatch.setattr(castdevices, "CONNECT_RETRY", 0.05)
    manager, backend = manager_with((CastDevice("TV", "1.2.3.4"),))
    backend.refuse_connect = "no Chromecast answered at 1.2.3.4"
    manager.start()
    manager.start()                                                        # a second start does nothing
    for _ in range(100):
        if manager.snapshot("alice")[0]["error"]:
            break
        time.sleep(0.02)
    shown = manager.snapshot("alice")[0]
    assert shown["online"] is False and "no Chromecast answered" in shown["error"]
    with pytest.raises(cast.CastError) as e:
        manager.command("alice", "TV", "pause")
    assert "no Chromecast answered" in e.value.message
    backend.refuse_connect = None                                          # the TV is switched on
    for _ in range(200):
        if manager.online_count():
            break
        time.sleep(0.02)
    assert manager.online_count() == 1 and manager.snapshot("alice")[0]["error"] == ""
    manager.close()


def test_a_device_that_stops_answering_is_reported_not_fatal():
    manager, backend = manager_with((CastDevice("TV", "1.2.3.4"),))
    backend.up.add("TV")
    backend.status = lambda name: (_ for _ in ()).throw(TimeoutError())
    assert "TimeoutError" in manager.snapshot("alice")[0]["error"]


# ---- the app: off by default ---------------------------------------------------------------------------------------------
def test_casting_is_off_by_default(cfg, tmp_path):
    client, _ = make(cfg, tmp_path)
    assert client.get("/api/cast", headers=ALICE).status_code == 404
    assert client.get("/cast/x.y/song.mp3").status_code == 404


# ---- the user API ---------------------------------------------------------------------------------------------------
def test_state_lists_devices_and_what_they_do(cast_app):
    client, _, backend = cast_app
    assert client.get("/api/cast").status_code == 401
    shown = client.get("/api/cast", headers=ALICE).json()["devices"]
    assert [d["name"] for d in shown] == ["Living room", "Kitchen"] and all(d["online"] and d["state"] == "idle" for d in shown)
    assert "audio/mpeg" in shown[0]["types"] and shown[0]["kind"] == "cast"
    backend.state["Kitchen"] = {"state": "playing", "title": "Song", "position": 12.5}
    assert client.get("/api/cast", headers=ALICE).json()["devices"][1]["position"] == 12.5


def test_a_device_can_be_limited_to_some_users(cfg, tmp_path, library_song, fake):
    client, _, backend = served(cfg, tmp_path, (CastDevice("Den", "10.0.0.2", users=("bob",)),))
    assert client.get("/api/cast", headers=ALICE).json() == {"devices": []}
    assert client.get("/api/cast", headers=basic("bob", "pw-bob")).json()["devices"][0]["name"] == "Den"
    r = play(client, device="Den")
    assert r.status_code == 404 and "no such device" in r.json()["error"]
    assert backend.calls == []


def test_play_sends_the_device_a_signed_address_on_this_server(cast_app):
    client, _, backend = cast_app
    r = play(client, start=30)
    assert r.status_code == 200 and r.json() == {"ok": True, "title": "song"}
    [(op, name, url, mime, title, start)] = backend.calls
    assert (op, name, mime, title, start) == ("play", "Living room", "audio/mpeg", "song", 30.0)
    assert url.startswith("http://testserver/cast/") and url.endswith("/song.mp3")             # the address the page was opened with
    assert TestClient(client.app).get(url.replace("http://testserver", "")).content == SONG       # and it works with no login


def test_the_media_address_follows_the_config_and_the_proxy(cfg, tmp_path, library_song, fake):
    client, _, backend = served(cfg, tmp_path, cast_media_base="http://192.168.1.10:8100")
    play(client)
    assert backend.calls[-1][2].startswith("http://192.168.1.10:8100/cast/")
    client, _, backend = served(cfg, tmp_path / "other", trust_proxy=True)
    client.post("/api/cast/play", json={"device": "Kitchen", "root": "books", "path": "Good Author/song.mp3"},
                headers={**ALICE, **ORIGIN, "x-forwarded-proto": "https", "x-forwarded-host": "pluteus.lan"})
    assert backend.calls[-1][2].startswith("https://pluteus.lan/cast/")
    r = client.post("/api/cast/play", json={"device": "Kitchen", "root": "books", "path": "Good Author/song.mp3"},
                    headers={**ALICE, **ORIGIN, "x-forwarded-host": "evil.example/x y"})
    assert r.status_code == 500 and "cast_media_base" in r.json()["error"]


def test_a_page_opened_as_localhost_cannot_tell_a_tv_where_to_go(cast_app):
    client, _, backend = cast_app
    body = {"device": "Living room", "root": "books", "path": "Good Author/song.mp3"}
    for host in ("localhost:8100", "127.0.0.1:8100", "[::1]:8100"):
        r = client.post("/api/cast/play", json=body, headers={**ALICE, **ORIGIN, "host": host, "origin": "http://" + host})
        assert r.status_code == 409 and "cast_media_base" in r.json()["error"]
    assert backend.calls == []


def test_play_refuses_what_it_should(cast_app):
    client, _, backend = cast_app
    assert play(client, device="Nope").status_code == 404
    assert play(client, path="Good Author/film.mkv").status_code == 415                # not playable without converting
    assert play(client, path="Good Author/Real Book.epub").status_code == 415          # not music or video
    assert play(client, path="Good Author/missing.mp3").status_code == 404
    assert play(client, path="../secret.txt").status_code in (400, 404)
    assert play(client, start="soon").status_code == 400
    assert play(client, who=basic("carol", "pw-carol")).status_code == 404             # carol may not open the books root
    assert play(client, who={}).status_code == 401
    assert client.post("/api/cast/play", json={}, headers=ALICE).status_code == 403    # no Origin: refused like every other write
    assert backend.calls == []


def test_play_says_so_when_a_device_is_not_connected(cfg, tmp_path, library_song, monkeypatch):
    class Unreachable(FakeBackend):
        def __init__(self):
            super().__init__()
            self.refuse_connect = "no Chromecast answered at 192.168.1.50"
    monkeypatch.setitem(castdevices.BACKENDS, "cast", Unreachable)
    client, app = make(cfg, tmp_path, cast=True, cast_devices=DEVICES)
    r = play(client)
    assert r.status_code == 503 and "not connected" in r.json()["error"]
    for _ in range(100):                                                               # the reason shows up once the first attempt has failed
        if "no Chromecast answered" in str(client.get("/api/cast", headers=ALICE).json()["devices"][0]["error"]):
            break
        time.sleep(0.02)
    assert "no Chromecast answered" in client.get("/api/cast", headers=ALICE).json()["devices"][0]["error"]


def test_a_device_that_refuses_gives_a_clear_answer(cast_app):
    client, _, backend = cast_app
    backend.fail = "the receiver is busy"
    r = play(client)
    assert r.status_code == 502 and "Living room did not accept that" in r.json()["error"]


def test_control_commands(cast_app):
    client, _, backend = cast_app
    assert control(client, op="pause").status_code == 200
    assert control(client, op="resume").status_code == 200
    assert control(client, op="seek", value=95).status_code == 200
    assert control(client, op="volume", value=33.6).status_code == 200
    assert control(client, op="stop").status_code == 200
    assert control(client, op="volume", value=101).status_code == 400
    assert control(client, op="seek", value=-1).status_code == 400
    assert control(client, op="seek").status_code == 400
    assert control(client, op="play").status_code == 400
    assert control(client, op="explode").status_code == 400
    assert backend.calls == [("pause", "Living room"), ("resume", "Living room"), ("seek", "Living room", 95.0), ("volume", "Living room", 34),
                             ("stop", "Living room")]


def test_the_shared_anonymous_user_cannot_cast(cfg, tmp_path, library_song, fake):
    client, _, backend = served(cfg, tmp_path, anonymous_user="alice")
    assert client.get("/api/cast").status_code == 404                                   # no login: acts as alice, who is the shared user
    assert client.get("/api/cast", headers=basic("bob", "pw-bob")).status_code == 200
    body = {"device": "Living room", "root": "books", "path": "Good Author/song.mp3"}
    assert client.post("/api/cast/play", json=body, headers=ORIGIN).status_code == 404
    assert client.post("/api/cast/control", json={**body, "op": "pause"}, headers=ORIGIN).status_code == 404
    assert backend.calls == []


# ---- the media route ------------------------------------------------------------------------------------------------
def media_url(client):
    play(client)
    return client.app.state.cast_manager._backends["cast"].calls[-1][2].replace("http://testserver", "")


def test_media_is_served_with_the_token_alone(cast_app):
    client, _, _ = cast_app
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


def test_media_refuses_bad_tokens_and_vanished_files(cast_app, library_song):
    client, _, _ = cast_app
    url = media_url(client)
    fresh = TestClient(client.app)
    token = url.split("/")[2]
    assert fresh.get("/cast/" + token[:-3] + "AAA/song.mp3").status_code == 404
    assert fresh.get("/cast/nonsense/song.mp3").status_code == 404
    library_song.unlink()
    assert fresh.get(url).status_code == 404


def test_media_follows_the_users_current_rights(cfg, tmp_path, library_song, fake):
    """The token names a file for a user, but the file is looked up again with that user's rights every time."""
    changed = dataclasses.replace(cfg, users={**cfg.users, "alice": dataclasses.replace(cfg.users["alice"], roots=("books",))})
    client, app, _ = served(changed, tmp_path)
    url = media_url(client)
    assert TestClient(app).get(url).status_code == 200
    app.state.auth.users["alice"] = dataclasses.replace(app.state.auth.users["alice"], roots=("comics",))
    assert TestClient(app).get(url).status_code == 404
    del app.state.auth.users["alice"]
    assert TestClient(app).get(url).status_code == 404


def test_media_survives_a_restart_with_a_data_folder(cfg, tmp_path, library_song, fake):
    first, _, _ = served(cfg, tmp_path)
    url = media_url(first)
    second, _, _ = served(cfg, tmp_path)                                                # same data folder: same cast.key
    assert TestClient(second.app).get(url).content == SONG

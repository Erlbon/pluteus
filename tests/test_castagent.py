"""The cast agent (pluteus/castagent.py): its loop against the real app, its media proxy, and one run over real HTTP."""
import http.server
import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest
import uvicorn
from conftest import basic
from test_cast import ALICE, SONG, TOKEN, library_song, online, play, site  # noqa: F401 - library_song is a fixture
from test_login_web import make
from test_uploads import ORIGIN

from pluteus import castagent


class FakeBackend(castagent.Backend):
    def __init__(self):
        self.calls, self.fail, self.broken_status = [], None, False
        self.state = {"tv-1": {"state": "idle", "volume": 20}, "kitchen": {"state": "idle", "volume": 50}}

    def devices(self):
        return [{"id": "tv-1", "name": "Living room"}, {"id": "kitchen", "name": "Kitchen"}]

    def status(self, device):
        if self.broken_status:
            raise TimeoutError("no answer")
        return dict(self.state[device])

    def _do(self, *call):
        if self.fail:
            raise RuntimeError(self.fail)
        self.calls.append(call)

    def play(self, device, url, mime, title, start):
        self._do("play", device, url, mime, title, start)
        self.state[device].update(state="playing", title=title, position=start)

    def pause(self, device):
        self._do("pause", device)

    def resume(self, device):
        self._do("resume", device)

    def stop(self, device):
        self._do("stop", device)

    def seek(self, device, seconds):
        self._do("seek", device, seconds)

    def set_volume(self, device, percent):
        self._do("volume", device, percent)


class ClientTransport:
    """The agent's transport, pointed straight at the app in a test client."""

    def __init__(self, client, token=TOKEN):
        self.client, self.token, self.server = client, token, "http://testserver"

    def origin(self):
        return self.server

    def request(self, method, path, body=None, timeout=0):
        r = self.client.request(method, path, json=body, headers={"Authorization": "Bearer " + self.token})
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, None


@pytest.fixture
def agent(cfg, tmp_path, library_song):
    client, app = make(cfg, tmp_path, cast=True, cast_sites=(site(),))
    backend = FakeBackend()
    proxy = castagent.MediaProxy("http://testserver", "10.0.0.5", 8765)
    return castagent.Agent(ClientTransport(client), backend, proxy, log=lambda text: None), client, backend


def cast_status(client):
    return client.get("/api/cast", headers=ALICE).json()["sites"][0]["devices"]


# ---- the loop against the app -----------------------------------------------------------------------------------------
def test_status_makes_the_site_and_its_devices_known(agent):
    ag, client, _ = agent
    ag.send_status()
    assert [d["name"] for d in cast_status(client)] == ["Living room", "Kitchen"]


def test_play_reaches_the_chromecast_through_the_agents_own_address(agent):
    ag, client, backend = agent
    ag.send_status()
    assert play(client, start=12).status_code == 200
    assert ag.poll_once(wait=0) == 1
    [(op, device, url, mime, title, start)] = backend.calls
    assert (op, device, mime, title, start) == ("play", "tv-1", "audio/mpeg", "song", 12.0)
    assert url.startswith("http://10.0.0.5:8765/p/") and url.endswith("/media")              # the Chromecast is sent to the agent, not to pluteus
    ag.send_status()
    assert cast_status(client)[0]["state"] == "playing" and cast_status(client)[0]["title"] == "song"


def test_control_commands_reach_the_backend(agent):
    ag, client, backend = agent
    ag.send_status()
    for body in ({"op": "pause"}, {"op": "resume"}, {"op": "seek", "value": 61}, {"op": "volume", "value": 35}, {"op": "stop"}):
        r = client.post("/api/cast/control", json={"site": "Cabin", "device": "kitchen", **body}, headers={**ALICE, **ORIGIN})
        assert r.status_code == 200
    assert ag.poll_once(wait=0) == 5
    assert backend.calls == [("pause", "kitchen"), ("resume", "kitchen"), ("seek", "kitchen", 61.0), ("volume", "kitchen", 35), ("stop", "kitchen")]


def test_a_failed_command_is_shown_until_the_next_one_works(agent):
    ag, client, backend = agent
    ag.send_status()
    backend.fail = "the Chromecast said no"
    client.post("/api/cast/control", json={"site": "Cabin", "device": "tv-1", "op": "pause"}, headers={**ALICE, **ORIGIN})
    ag.poll_once(wait=0)
    ag.send_status()
    assert "pause failed: the Chromecast said no" in cast_status(client)[0]["error"]
    assert cast_status(client)[1]["error"] == ""
    backend.fail = None
    client.post("/api/cast/control", json={"site": "Cabin", "device": "tv-1", "op": "resume"}, headers={**ALICE, **ORIGIN})
    ag.poll_once(wait=0)
    ag.send_status()
    assert cast_status(client)[0]["error"] == ""


def test_a_chromecast_that_does_not_answer_is_reported_not_fatal(agent):
    ag, client, backend = agent
    backend.broken_status = True
    ag.send_status()
    assert "TimeoutError" in cast_status(client)[0]["error"]


def test_an_address_outside_pluteus_is_never_played(agent):
    ag, client, backend = agent
    for address in ("//evil.example/x", "http://evil.example/x", "evil.example/x", "file:///etc/passwd"):
        ag.handle({"op": "play", "device": "tv-1", "url": address, "mime": "audio/mpeg"})
        assert backend.calls == [] and "play failed" in ag.errors["tv-1"]
    ag.handle({"op": "fly", "device": "tv-1"})
    assert backend.calls == [] and "unknown command" in ag.errors["tv-1"]


def test_a_refused_token_stops_the_loop_politely(cfg, tmp_path, library_song):
    client, _ = make(cfg, tmp_path, cast=True, cast_sites=(site(),))
    ag = castagent.Agent(ClientTransport(client, "wrong"), FakeBackend(), castagent.MediaProxy("http://testserver", "h"), log=lambda t: None)
    with pytest.raises(castagent.Refused):
        ag.send_status()
    with pytest.raises(castagent.Refused):
        ag.poll_once(wait=0)


# ---- the proxy ---------------------------------------------------------------------------------------------------------
class Upstream(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.reply(True)

    def do_HEAD(self):
        self.reply(False)

    def reply(self, body):
        if self.path != "/cast/token/song.mp3":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data, status, extra = SONG, 200, {}
        if self.headers.get("Range", "").startswith("bytes="):
            first, _, last = self.headers["Range"][6:].partition("-")
            first, last = int(first), int(last) if last else len(SONG) - 1
            data, status = SONG[first:last + 1], 206
            extra["Content-Range"] = "bytes %d-%d/%d" % (first, last, len(SONG))
        self.send_response(status)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(data)


@pytest.fixture
def upstream():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % server.server_address[1]
    server.shutdown()
    server.server_close()


def fetch(url, **headers):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=10) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def test_proxy_passes_the_file_and_ranges_on(upstream):
    proxy = castagent.MediaProxy(upstream, "127.0.0.1", 0, bind="127.0.0.1")
    proxy.start()
    try:
        local = proxy.register(upstream + "/cast/token/song.mp3")
        status, body, headers = fetch(local)
        assert status == 200 and body == SONG and headers["Access-Control-Allow-Origin"] == "*"
        status, body, headers = fetch(local, Range="bytes=1000-1999")
        assert status == 206 and body == SONG[1000:2000] and headers["Content-Range"] == "bytes 1000-1999/%d" % len(SONG)
        request = urllib.request.Request(local, method="HEAD")
        with urllib.request.urlopen(request, timeout=10) as r:
            assert r.status == 200 and r.headers["Content-Length"] == str(len(SONG)) and r.read() == b""
        assert fetch(local.rsplit("/p/", 1)[0] + "/p/unknownunknown/media")[0] == 404
        assert fetch(local.rsplit("/", 1)[0] + "/other")[0] == 404
        assert fetch(local.replace("/media", "/x"))[0] == 404
    finally:
        proxy.stop()


def test_proxy_serves_only_pluteus_addresses_and_remembers_few(upstream):
    proxy = castagent.MediaProxy(upstream, "127.0.0.1", 0, bind="127.0.0.1")
    for foreign in ("http://evil.example/x", "https://127.0.0.1:1/x", "file:///etc/passwd", "/relative"):
        with pytest.raises(ValueError):
            proxy.register(foreign)
    ids = [proxy.register(upstream + "/a%d" % i).split("/")[4] for i in range(castagent.MAX_PROXY + 5)]
    assert proxy.lookup(ids[0]) is None and proxy.lookup(ids[-1]) is not None


def test_proxy_reports_an_unreachable_pluteus():
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    dead = "http://127.0.0.1:%d" % free.getsockname()[1]
    free.close()
    proxy = castagent.MediaProxy(dead, "127.0.0.1", 0, bind="127.0.0.1")
    proxy.start()
    try:
        assert fetch(proxy.register(dead + "/cast/t/x.mp3"))[0] == 502
    finally:
        proxy.stop()


# ---- everything together over real HTTP -----------------------------------------------------------------------------------
@pytest.fixture
def live(cfg, tmp_path, library_song):
    """The real app on a real port (uvicorn), as the agent will meet it."""
    _, app = make(cfg, tmp_path, cast=True, cast_sites=(site(),))
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    yield "http://127.0.0.1:%d" % port
    server.should_exit = True
    thread.join(10)


def post_json(url, body, **headers):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as r:
        return r.status, json.loads(r.read())


def test_the_whole_chain_over_real_http(live):
    transport = castagent.HttpTransport(live, TOKEN)
    proxy = castagent.MediaProxy(transport.origin(), "127.0.0.1", 0, bind="127.0.0.1")
    proxy.start()
    backend = FakeBackend()
    agent = castagent.Agent(transport, backend, proxy, log=lambda text: None)
    try:
        agent.send_status()
        asking = {"site": "Cabin", "device": "tv-1", "root": "books", "path": "Good Author/song.mp3"}
        user = {**basic("alice", "pw-alice"), **ORIGIN, "Origin": live}

        # a long poll that is waiting is woken by the command, not by its timeout
        got = []
        poller = threading.Thread(target=lambda: got.append(agent.poll_once(wait=10)))
        began = time.monotonic()
        poller.start()
        time.sleep(0.6)
        assert post_json(live + "/api/cast/play", asking, **user)[0] == 200
        poller.join(8)
        assert got == [1] and time.monotonic() - began < 5

        # what the Chromecast would do: fetch the address it was given, from the agent, and seek in it
        [(_, _, local, mime, title, _)] = backend.calls
        assert mime == "audio/mpeg" and local.startswith("http://127.0.0.1:%d/p/" % proxy.port)
        assert fetch(local)[1] == SONG
        status, body, headers = fetch(local, Range="bytes=5000-5999")
        assert status == 206 and body == SONG[5000:6000]

        agent.send_status()
        request = urllib.request.Request(live + "/api/cast", headers=basic("alice", "pw-alice"))
        with urllib.request.urlopen(request, timeout=10) as r:
            device = json.loads(r.read())["sites"][0]["devices"][0]
        assert device["state"] == "playing" and device["title"] == "song"
    finally:
        proxy.stop()


def test_a_wrong_token_over_real_http_is_refused(live):
    agent = castagent.Agent(castagent.HttpTransport(live, "wrong"), FakeBackend(), castagent.MediaProxy(live, "h"), log=lambda text: None)
    with pytest.raises(castagent.Refused):
        agent.send_status()


# ---- the pychromecast adapter, against a stand-in for the library (no Chromecast here to try it on) ----------------------------
class FakeMedia:
    def __init__(self):
        self.calls = []
        self.status = type("S", (), {"player_state": "PLAYING", "adjusted_current_time": 41.5, "duration": 200.0, "title": "Song"})()

    def play_media(self, url, mime, **kw):
        self.calls.append(("play_media", url, mime, kw))

    def block_until_active(self, timeout=None):
        self.calls.append(("block", timeout))

    def pause(self):
        self.calls.append(("pause",))

    def play(self):
        self.calls.append(("resume",))

    def stop(self):
        self.calls.append(("stop",))

    def seek(self, seconds):
        self.calls.append(("seek", seconds))


class FakeCast:
    name, cast_type, uri, uuid = "Living room", "cast", "192.168.1.9:8009", "11111111-2222-3333-4444-555555555555"

    def __init__(self):
        self.media_controller = FakeMedia()
        self.status = type("S", (), {"volume_level": 0.4})()
        self.volumes = []

    def wait(self, timeout=None):
        pass

    def set_volume(self, level):
        self.volumes.append(level)


@pytest.fixture
def fake_pychromecast(monkeypatch):
    import sys
    import types
    cast = FakeCast()
    module = types.ModuleType("pychromecast")
    module.seen = {}

    class Browser:
        stopped = False

        def stop_discovery(self):
            Browser.stopped = True

    def get_chromecasts(known_hosts=None, timeout=None, **kw):
        module.seen = {"known_hosts": known_hosts, "timeout": timeout}
        return [cast], Browser()
    module.get_chromecasts = get_chromecasts
    monkeypatch.setitem(sys.modules, "pychromecast", module)
    return cast, module, Browser


def test_pychromecast_backend_maps_the_library(fake_pychromecast):
    cast, module, Browser = fake_pychromecast
    backend = castagent.PyChromecastBackend(["192.168.1.9"], log=lambda text: None)
    assert module.seen["known_hosts"] == ["192.168.1.9"]
    assert backend.devices() == [{"id": cast.uuid, "name": "Living room"}]
    assert backend.status(cast.uuid) == {"state": "playing", "position": 41.5, "duration": 200.0, "title": "Song", "volume": 40}
    cast.media_controller.status.player_state = "IDLE"
    assert backend.status(cast.uuid)["state"] == "idle" and backend.status(cast.uuid)["position"] is None
    backend.play(cast.uuid, "http://10.0.0.5:8765/p/x/media", "audio/mpeg", "Song", 0)
    backend.play(cast.uuid, "http://10.0.0.5:8765/p/x/media", "video/mp4", "Film", 90.0)
    first, second = cast.media_controller.calls[0], cast.media_controller.calls[2]
    assert first[3] == {"title": "Song", "current_time": None, "autoplay": True, "stream_type": "BUFFERED"}      # a file, not a live stream
    assert second[3]["current_time"] == 90.0 and second[2] == "video/mp4"
    backend.pause(cast.uuid)
    backend.resume(cast.uuid)
    backend.seek(cast.uuid, 12.0)
    backend.stop(cast.uuid)
    backend.set_volume(cast.uuid, 70)
    backend.set_volume(cast.uuid, 250)
    assert [c[0] for c in cast.media_controller.calls[4:]] == ["pause", "resume", "seek", "stop"]
    assert cast.volumes == [0.7, 1.0]
    with pytest.raises(KeyError):
        backend.pause("not-a-device")
    backend.close()
    assert Browser.stopped


def test_without_the_library_the_backend_says_so(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "pychromecast", None)
    with pytest.raises(ImportError):
        castagent.PyChromecastBackend()

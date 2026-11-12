"""The Google Cast backend against a stand-in for the pychromecast library, the manager with it, and one fetch over real HTTP."""
import socket
import sys
import threading
import time
import types
import urllib.request

import pytest
import uvicorn
from conftest import basic, wait_until
from test_cast import ALICE, DEVICES, SONG, library_song, play  # noqa: F401 - library_song is a fixture
from test_login_web import make

from pluteus import castdevices
from pluteus.config import CastDevice


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
    def __init__(self):
        self.cast_info = type("I", (), {"host": "192.168.1.50", "port": 8009})()         # where it was found: the backend takes it by address
        self.media_controller = FakeMedia()
        self.status = type("S", (), {"volume_level": 0.4})()
        self.volumes, self.waited = [], None

    def wait(self, timeout=None):
        self.waited = timeout

    def set_volume(self, level):
        self.volumes.append(level)


class FakeBrowser:
    def __init__(self):
        self.stopped = False

    def stop_discovery(self):
        self.stopped = True


@pytest.fixture
def library_module(monkeypatch):
    """A stand-in `pychromecast`: one Chromecast answers at 192.168.1.50 and none anywhere else."""
    module = types.ModuleType("pychromecast")
    module.cast, module.browsers, module.asked = FakeCast(), [], []

    def get_chromecasts(known_hosts=None, timeout=None, **kw):
        module.asked.append((known_hosts, timeout))
        browser = FakeBrowser()
        module.browsers.append(browser)
        return ([module.cast] if known_hosts == ["192.168.1.50"] else []), browser
    module.get_chromecasts = get_chromecasts
    monkeypatch.setitem(sys.modules, "pychromecast", module)
    return module


def test_cast_backend_asks_the_device_by_address_and_waits_for_it(library_module):
    backend = castdevices.CastBackend()
    assert not backend.connected("Living room")
    backend.connect(CastDevice("Living room", "192.168.1.50"))
    assert library_module.asked == [(["192.168.1.50"], 10)] and library_module.cast.waited == 15
    assert backend.connected("Living room") and not backend.connected("Kitchen")


def test_cast_backend_says_so_when_nothing_answers(library_module):
    backend = castdevices.CastBackend()
    with pytest.raises(ConnectionError, match="no Chromecast answered at 192.168.1.99"):
        backend.connect(CastDevice("Den", "192.168.1.99"))
    assert library_module.browsers[0].stopped and not backend.connected("Den")


def test_without_the_library_it_says_how_to_install_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "pychromecast", None)
    with pytest.raises(ConnectionError, match="pip install pychromecast"):
        castdevices.CastBackend().connect(CastDevice("Den", "192.168.1.99"))


def test_cast_backend_maps_the_library(library_module):
    backend = castdevices.CastBackend()
    cast = library_module.cast
    backend.connect(CastDevice("TV", "192.168.1.50"))
    assert backend.status("TV") == {"state": "playing", "position": 41.5, "duration": 200.0, "title": "Song", "volume": 40}
    cast.media_controller.status.player_state = "IDLE"
    assert backend.status("TV")["state"] == "idle" and backend.status("TV")["position"] is None
    backend.play("TV", "http://192.168.1.10:8100/cast/t/s.mp3", "audio/mpeg", "Song", 0)
    backend.play("TV", "http://192.168.1.10:8100/cast/t/f.mp4", "video/mp4", "Film", 90.0)
    first, second = cast.media_controller.calls[0], cast.media_controller.calls[2]
    assert first[3] == {"title": "Song", "current_time": None, "autoplay": True, "stream_type": "BUFFERED"}        # a file, not a live stream
    assert second[3]["current_time"] == 90.0 and second[2] == "video/mp4"
    backend.pause("TV")
    backend.resume("TV")
    backend.seek("TV", 12.0)
    backend.stop("TV")
    backend.set_volume("TV", 70)
    backend.set_volume("TV", 250)
    assert [c[0] for c in cast.media_controller.calls[4:]] == ["pause", "resume", "seek", "stop"]
    assert cast.volumes == [0.7, 1.0]
    with pytest.raises(ConnectionError):
        backend.pause("Elsewhere")
    backend.close()
    assert library_module.browsers[0].stopped


def test_the_manager_works_with_the_real_cast_backend(cfg, tmp_path, library_song, library_module):
    """The whole path on the server side with only the library replaced: config, background connect, play, status."""
    client, app = make(cfg, tmp_path, cast=True, cast_devices=(CastDevice("Living room", "192.168.1.50"), CastDevice("Den", "192.168.1.99")))
    shown = client.get("/api/cast", headers=ALICE).json()["devices"]                      # this request starts the connecting
    for _ in range(100):
        if app.state.cast_manager.online_count() == 1:
            break
        time.sleep(0.02)
    shown = client.get("/api/cast", headers=ALICE).json()["devices"]
    assert [(d["name"], d["online"]) for d in shown] == [("Living room", True), ("Den", False)]
    assert "no Chromecast answered at 192.168.1.99" in shown[1]["error"] and shown[0]["state"] == "playing"
    assert play(client, device="Living room").status_code == 200
    [(_, url, mime, kw)] = [c for c in library_module.cast.media_controller.calls if c[0] == "play_media"]
    assert url.startswith("http://testserver/cast/") and mime == "audio/mpeg" and kw["title"] == "song"
    assert play(client, device="Den").status_code == 503
    app.state.cast_manager.close()


# ---- what the Chromecast itself does: fetch the address over real HTTP, and seek in it ------------------------------------------------
@pytest.fixture
def live(cfg, tmp_path, library_song):
    """The real app on a real port (uvicorn)."""
    class Backend(castdevices.CastBackend):
        def connect(self, device):
            self._casts[device.name] = object()
    castdevices.BACKENDS["cast"], saved = Backend, castdevices.BACKENDS["cast"]
    try:
        # The listening socket is bound to port 0 here and handed to the server still open, so the port read back from it
        # cannot be taken by another process in between (the app has to know its own address before it is made).
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        try:
            # opened as 127.0.0.1 a TV could not reach the server, which Pluteus refuses: the config says which address to hand out
            _, app = make(cfg, tmp_path, cast=True, cast_devices=DEVICES[:1], cast_media_base="http://127.0.0.1:%d" % port)
            server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
            thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
            thread.start()
            assert wait_until(lambda: server.started, timeout=20)
            yield "http://127.0.0.1:%d" % port, app
            server.should_exit = True
            thread.join(10)
        finally:
            listener.close()
    finally:
        castdevices.BACKENDS["cast"] = saved


def test_a_device_can_fetch_and_seek_the_file_over_real_http(live):
    base, app = live
    backend = app.state.cast_manager._backends["cast"]
    played = []
    backend.status = lambda name: {"state": "idle"}
    backend.play = lambda name, url, mime, title, start: played.append((url, mime))
    urllib.request.urlopen(urllib.request.Request(base + "/api/cast", headers=basic("alice", "pw-alice")), timeout=10).read()      # starts the connecting
    for _ in range(100):
        if backend.connected("Living room"):
            break
        time.sleep(0.02)
    request = urllib.request.Request(base + "/api/cast/play", method="POST", data=b'{"device":"Living room","root":"books","path":"Good Author/song.mp3"}',
                                     headers={"Content-Type": "application/json", "Origin": base, **basic("alice", "pw-alice")})
    with urllib.request.urlopen(request, timeout=10) as r:
        assert r.status == 200
    [(url, mime)] = played
    assert url.startswith(base + "/cast/") and mime == "audio/mpeg"
    with urllib.request.urlopen(url, timeout=10) as r:
        assert r.status == 200 and r.read() == SONG and r.headers["Access-Control-Allow-Origin"] == "*"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=5000-5999"}), timeout=10) as r:
        assert r.status == 206 and r.read() == SONG[5000:6000]

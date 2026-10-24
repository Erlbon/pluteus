"""The casting devices Pluteus controls itself, on its own network: what each kind of device is told, and the Manager that keeps them.

Pluteus never sends a device the file. It sends the device the *address* of a signed media link (cast.py, `/cast/<token>/<name>`) and the device
fetches the file from Pluteus itself, as a TV or a Chromecast does. So the server must be reachable from the device, and the device is listed in
the config by its address: automatic discovery (mDNS, SSDP) does not pass through Docker Desktop on Windows.

A `Backend` is one kind of device (Google Cast today; the same interface is where DLNA/UPnP goes). The `Manager` connects each configured device
in the background, retrying until it answers, answers the page's questions without waiting on the network, and turns commands into backend calls.
Every call that can block (connecting, playing) runs in a worker thread, never on the event loop."""
from __future__ import annotations

import threading

from .cast import CastError

OPS = ("play", "pause", "resume", "stop", "seek", "volume")
CONNECT_RETRY = 30.0                 # seconds between attempts to reach a device that does not answer


class Backend:
    """One kind of casting device. `connect` may block; the rest answer quickly or raise."""

    kind = ""
    types: tuple = ()                # the media types this kind plays as they are (anything else would need converting)

    def connect(self, device) -> None:
        """Reach the device (a config.CastDevice) and keep the connection; raise when it does not answer."""
        raise NotImplementedError

    def connected(self, name: str) -> bool:
        raise NotImplementedError

    def status(self, name: str) -> dict:
        """{"state": idle|playing|paused|buffering, "position", "duration", "volume" (0 to 100), "title"}."""
        raise NotImplementedError

    def play(self, name: str, url: str, mime: str, title: str, start: float) -> None:
        raise NotImplementedError

    def pause(self, name: str) -> None:
        raise NotImplementedError

    def resume(self, name: str) -> None:
        raise NotImplementedError

    def stop(self, name: str) -> None:
        raise NotImplementedError

    def seek(self, name: str, seconds: float) -> None:
        raise NotImplementedError

    def set_volume(self, name: str, percent: int) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class CastBackend(Backend):
    """Google Cast (Chromecast, Google TV, TVs with Chromecast built in), through the pychromecast library (version 14, an optional install:
    `pip install pychromecast`). A device is asked for by its address, not found by multicast."""

    kind = "cast"
    types = ("audio/mpeg", "audio/mp4", "audio/aac", "audio/ogg", "audio/wav", "audio/flac", "video/mp4", "video/webm")

    def __init__(self):
        self._casts: dict = {}
        self._browsers: list = []

    def connect(self, device) -> None:
        try:
            import pychromecast
        except ImportError:
            raise ConnectionError("the pychromecast library is not installed (pip install pychromecast)")
        casts, browser = pychromecast.get_chromecasts(known_hosts=[device.address], timeout=10)
        if not casts:
            browser.stop_discovery()
            raise ConnectionError("no Chromecast answered at %s" % device.address)
        casts[0].wait(timeout=15)                         # starts its connection and waits for the first status
        self._browsers.append(browser)
        self._casts[device.name] = casts[0]

    def connected(self, name: str) -> bool:
        return name in self._casts

    def _cast(self, name: str):
        cast = self._casts.get(name)
        if cast is None:
            raise ConnectionError("not connected")
        return cast

    def status(self, name: str) -> dict:
        cast = self._cast(name)
        media = cast.media_controller.status
        state = {"PLAYING": "playing", "PAUSED": "paused", "BUFFERING": "buffering"}.get(media.player_state, "idle")
        level = cast.status.volume_level if cast.status else None
        busy = state != "idle"
        return {"state": state, "position": media.adjusted_current_time if busy else None, "duration": media.duration if busy else None,
                "title": (media.title or "") if busy else "", "volume": None if level is None else round(level * 100)}

    def play(self, name: str, url: str, mime: str, title: str, start: float) -> None:
        controller = self._cast(name).media_controller
        controller.play_media(url, mime, title=title or None, current_time=start or None, autoplay=True, stream_type="BUFFERED")
        controller.block_until_active(timeout=15)

    def pause(self, name: str) -> None:
        self._cast(name).media_controller.pause()

    def resume(self, name: str) -> None:
        self._cast(name).media_controller.play()

    def stop(self, name: str) -> None:
        self._cast(name).media_controller.stop()

    def seek(self, name: str, seconds: float) -> None:
        self._cast(name).media_controller.seek(seconds)

    def set_volume(self, name: str, percent: int) -> None:
        self._cast(name).set_volume(max(0, min(100, percent)) / 100)

    def close(self) -> None:
        for browser in self._browsers:
            try:
                browser.stop_discovery()
            except Exception:                           # noqa: BLE001
                pass


BACKENDS = {CastBackend.kind: CastBackend}


def backends_for(devices) -> dict:
    """One backend object for each kind of device the config lists."""
    return {kind: BACKENDS[kind]() for kind in {d.kind for d in devices}}


class Manager:
    """The configured devices: who may use which, whether each is connected, what each is doing, and the commands for them."""

    def __init__(self, devices, backends: dict):
        self._devices = {d.name: d for d in devices}
        self._backends = backends
        self._problems: dict = {}
        self._started = False
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def start(self) -> None:
        """Begin connecting every device in the background (once). Called when the first page asks, so starting the server never waits for a TV."""
        with self._lock:
            if self._started:
                return
            self._started = True
        for device in self._devices.values():
            threading.Thread(target=self._connect_loop, args=(device,), daemon=True).start()

    def _connect_loop(self, device) -> None:
        backend = self._backends[device.kind]
        while not self._stop.is_set():
            try:
                backend.connect(device)
                self._problems.pop(device.name, None)
                return
            except Exception as e:                       # noqa: BLE001 - the reason is shown next to the device
                self._problems[device.name] = str(e)[:200] or e.__class__.__name__
                self._stop.wait(CONNECT_RETRY)

    def may_use(self, name: str, user_name: str) -> bool:
        device = self._devices.get(name)
        return device is not None and (not device.users or user_name in device.users)

    def device(self, name: str, user_name: str):
        if not self.may_use(name, user_name):
            raise CastError("There is no such device to cast to.", 404)
        return self._devices[name]

    def types_for(self, name: str) -> tuple:
        return self._backends[self._devices[name].kind].types

    def online_count(self) -> int:
        return sum(1 for d in self._devices.values() if self._backends[d.kind].connected(d.name))

    def snapshot(self, user_name: str) -> list:
        """The devices this user may cast to, with what each is doing. Never waits on the network."""
        out = []
        for device in self._devices.values():
            if not self.may_use(device.name, user_name):
                continue
            backend = self._backends[device.kind]
            entry = {"id": device.name, "name": device.name, "kind": device.kind, "types": list(backend.types), "online": backend.connected(device.name),
                     "state": "idle", "position": None, "duration": None, "volume": None, "title": "", "error": self._problems.get(device.name, "")}
            if entry["online"]:
                try:
                    entry.update(backend.status(device.name))
                except Exception as e:                   # noqa: BLE001 - a device that does not answer is reported, not fatal
                    entry["error"] = "does not answer (%s)" % e.__class__.__name__
            out.append(entry)
        return out

    def command(self, user_name: str, name: str, op: str, **fields) -> None:
        """Tell a device what to do (this can block for seconds: call it from a worker thread). Raises CastError with a sentence for the user."""
        device = self.device(name, user_name)
        if op not in OPS:
            raise CastError("That is not something a device can be told to do.", 400)
        backend = self._backends[device.kind]
        if not backend.connected(device.name):
            problem = self._problems.get(device.name)
            raise CastError("%s is not connected right now%s." % (device.name, " (%s)" % problem if problem else ""), 503)
        try:
            if op == "play":
                backend.play(device.name, fields["url"], fields["mime"], fields.get("title", ""), float(fields.get("start") or 0))
            elif op == "seek":
                backend.seek(device.name, float(fields["value"]))
            elif op == "volume":
                backend.set_volume(device.name, int(fields["value"]))
            else:
                getattr(backend, {"pause": "pause", "resume": "resume", "stop": "stop"}[op])(device.name)
        except CastError:
            raise
        except Exception as e:                           # noqa: BLE001 - whatever the library raised, the user gets a sentence
            raise CastError("%s did not accept that (%s)." % (device.name, (str(e) or e.__class__.__name__)[:120]), 502)

    def close(self) -> None:
        self._stop.set()
        for backend in self._backends.values():
            backend.close()

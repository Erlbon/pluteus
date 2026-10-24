"""The pluteus cast agent: the small program that sits next to the Chromecasts and plays what pluteus tells it to.

Run it on a machine that is on the same network as the Chromecasts and can reach the pluteus server (a Raspberry Pi, a mini PC, the Docker
host). It only ever connects OUT to pluteus, so nothing needs opening at that site. It is one file with no pluteus code in it: copy it
anywhere and install the one library it needs,

    pip install pychromecast
    python castagent.py --server https://pluteus.example.org --token-file agent.token

What it does, over and over:
  * tells pluteus which Chromecasts it sees and what each is doing (POST /cast-agent/status, every few seconds),
  * asks for commands with a long poll (GET /cast-agent/poll) and carries them out: play, pause, resume, stop, seek, volume,
  * for "play", fetches the file from pluteus (a signed address that names one file) and serves it to the Chromecast from a small web
    server of its own on this network. The Chromecast never needs to reach pluteus itself: that is what lets it play from a pluteus
    that is only reachable over a tailnet.

The token comes from `pluteus cast-token --name SITE`; give it to the agent in a file (--token-file) or the PLUTEUS_CAST_TOKEN environment
variable. The agent's web server answers only addresses on this network, only for the files pluteus named, and only from pluteus's own host.
Add or restart Chromecasts, then restart the agent so it finds them again (it looks once at start)."""
from __future__ import annotations

import argparse
import http.client
import http.server
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

VERSION = 1
POLL_WAIT = 25.0            # the long poll waits this long on the server for a command
STATUS_EVERY = 3.0          # seconds between reports of what the Chromecasts are doing
RETRY_WAIT = 5.0            # after a failed request
REFUSED_WAIT = 60.0         # after the server refused the token (do not hammer it: it counts wrong tokens)
PROXY_SECONDS = 12 * 3600   # a registered media address lives this long
MAX_PROXY = 50
CHUNK = 65536
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Range, Content-Type",
        "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges"}
_COPIED = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges")


class Refused(Exception):
    """The server did not accept the agent's token."""


def log_line(text: str) -> None:
    print(time.strftime("%H:%M:%S ") + text, flush=True)


# ---- talking to pluteus ------------------------------------------------------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None                                   # never follow a redirect: the token would go along with it


class HttpTransport:
    """Requests to the pluteus server with the agent's token. `request` returns (status, decoded JSON or None)."""

    def __init__(self, server: str, token: str):
        self.server = server.rstrip("/")
        self.token = token
        self._opener = urllib.request.build_opener(_NoRedirect)

    def origin(self) -> str:
        parts = urlsplit(self.server)
        return urlunsplit((parts.scheme, parts.netloc, "", "", ""))

    def request(self, method: str, path: str, body=None, timeout: float = 40.0):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.server + path, data=data, method=method,
                                     headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json",
                                              "User-Agent": "pluteus-cast-agent/%d" % VERSION})
        try:
            with self._opener.open(req, timeout=timeout) as reply:
                status, raw = reply.status, reply.read(1 << 20)
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read(1 << 16)
        try:
            return status, (json.loads(raw.decode("utf-8")) if raw else None)
        except (ValueError, UnicodeDecodeError):
            return status, None


# ---- the web server the Chromecast fetches from --------------------------------------------------------------------------------------------
class MediaProxy:
    """Serves the files pluteus named to the Chromecast, on this network. `register(url)` takes an address on the pluteus server (any other
    host is refused) and returns the local address to hand to the Chromecast; the file is fetched from pluteus while the Chromecast reads it,
    Range requests (seeking) included, nothing is stored."""

    def __init__(self, origin: str, advertise_host: str, port: int = 8765, bind: str = "0.0.0.0"):
        parts = urlsplit(origin)
        self._origin = (parts.scheme, parts.netloc.lower())
        self.advertise_host, self.port, self._bind = advertise_host, port, bind
        self._items: dict = {}
        self._lock = threading.Lock()
        self._server = None

    def register(self, url: str) -> str:
        parts = urlsplit(url)
        if (parts.scheme, parts.netloc.lower()) != self._origin:
            raise ValueError("that address is not on the pluteus server")
        ident = secrets.token_urlsafe(12)
        now = time.monotonic()
        with self._lock:
            self._items = {k: v for k, v in self._items.items() if v[1] > now}
            while len(self._items) >= MAX_PROXY:
                self._items.pop(min(self._items, key=lambda k: self._items[k][1]))
            self._items[ident] = (url, now + PROXY_SECONDS)
        return "http://%s:%d/p/%s/media" % (self.advertise_host, self.port, ident)

    def lookup(self, ident: str):
        with self._lock:
            item = self._items.get(ident)
        return item[0] if item and item[1] > time.monotonic() else None

    def start(self) -> None:
        proxy = self

        class Handler(_Handler):
            owner = proxy
        self._server = http.server.ThreadingHTTPServer((self._bind, self.port), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    owner: MediaProxy = None

    def log_message(self, *args) -> None:
        pass

    def _lan_only(self) -> bool:
        try:
            ip = ipaddress.ip_address(self.client_address[0].split("%")[0])
        except ValueError:
            return False
        return ip.is_private or ip.is_loopback or ip.is_link_local

    def _plain(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        for k, v in CORS.items():
            self.send_header(k, v)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        for k, v in CORS.items():
            self.send_header(k, v)
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self) -> None:
        self._serve(False)

    def do_GET(self) -> None:
        self._serve(True)

    def _serve(self, with_body: bool) -> None:
        if not self._lan_only():
            return self._plain(403, "Not from here.")
        match = re.fullmatch(r"/p/([A-Za-z0-9_-]{8,40})/media", self.path.split("?", 1)[0])
        url = self.owner.lookup(match.group(1)) if match else None
        if not url:
            return self._plain(404, "Not found.")
        parts = urlsplit(url)
        connection = (http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection)(parts.hostname, parts.port, timeout=30)
        headers = {"User-Agent": "pluteus-cast-agent/%d" % VERSION, "Accept-Encoding": "identity"}
        if self.headers.get("Range"):
            headers["Range"] = self.headers["Range"]
        try:
            connection.request("GET" if with_body else "HEAD", parts.path + ("?" + parts.query if parts.query else ""), headers=headers)
            reply = connection.getresponse()
        except (OSError, http.client.HTTPException):
            connection.close()
            return self._plain(502, "Could not reach pluteus.")
        try:
            self.send_response(reply.status)
            for name in _COPIED:
                if reply.getheader(name):
                    self.send_header(name, reply.getheader(name))
            for k, v in CORS.items():
                self.send_header(k, v)
            if not reply.getheader("Content-Length"):
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            while with_body:
                chunk = reply.read(CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (OSError, http.client.HTTPException):         # the Chromecast went away or pluteus stopped answering: nothing more to do
            self.close_connection = True
        finally:
            connection.close()


# ---- the Chromecasts ---------------------------------------------------------------------------------------------------------------------
class Backend:
    """What the agent needs from the Chromecast library; the tests put a fake in its place."""

    def devices(self) -> list:
        """[{"id", "name"}] for every Chromecast found."""
        raise NotImplementedError

    def status(self, device: str) -> dict:
        """{"state": idle|playing|paused|buffering, "position", "duration", "volume" (0 to 100), "title"}."""
        raise NotImplementedError

    def play(self, device: str, url: str, mime: str, title: str, start: float) -> None:
        raise NotImplementedError

    def pause(self, device: str) -> None:
        raise NotImplementedError

    def resume(self, device: str) -> None:
        raise NotImplementedError

    def stop(self, device: str) -> None:
        raise NotImplementedError

    def seek(self, device: str, seconds: float) -> None:
        raise NotImplementedError

    def set_volume(self, device: str, percent: int) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class PyChromecastBackend(Backend):
    """The real thing, through the pychromecast library (version 14). Looks for Chromecasts once, at start: with `hosts` (their addresses)
    it asks them directly, which is the way that works on a network where mDNS does not pass; without, it listens for them."""

    def __init__(self, hosts=(), log=log_line):
        import pychromecast                              # only here, so the rest of this file works without the library installed
        self._log = log
        casts, self._browser = pychromecast.get_chromecasts(known_hosts=list(hosts) or None, timeout=10)
        self._casts = {str(c.uuid): c for c in casts}
        for cast in casts:
            log("found Chromecast %r (%s) at %s" % (cast.name, cast.cast_type, cast.uri))
        if not casts:
            log("no Chromecast found: check that they are on this network, or give their addresses with --host")

    def devices(self) -> list:
        return [{"id": ident, "name": cast.name} for ident, cast in self._casts.items()]

    def _cast(self, device: str):
        cast = self._casts.get(device)
        if cast is None:
            raise KeyError("no such Chromecast")
        cast.wait(timeout=10)
        return cast

    def status(self, device: str) -> dict:
        cast = self._casts[device]
        media = cast.media_controller.status
        state = {"PLAYING": "playing", "PAUSED": "paused", "BUFFERING": "buffering"}.get(media.player_state, "idle")
        level = cast.status.volume_level if cast.status else None
        return {"state": state, "position": media.adjusted_current_time if state != "idle" else None,
                "duration": media.duration if state != "idle" else None, "title": (media.title or "") if state != "idle" else "",
                "volume": None if level is None else round(level * 100)}

    def play(self, device: str, url: str, mime: str, title: str, start: float) -> None:
        controller = self._cast(device).media_controller
        controller.play_media(url, mime, title=title or None, current_time=start or None, autoplay=True, stream_type="BUFFERED")
        controller.block_until_active(timeout=15)

    def pause(self, device: str) -> None:
        self._cast(device).media_controller.pause()

    def resume(self, device: str) -> None:
        self._cast(device).media_controller.play()

    def stop(self, device: str) -> None:
        self._cast(device).media_controller.stop()

    def seek(self, device: str, seconds: float) -> None:
        self._cast(device).media_controller.seek(seconds)

    def set_volume(self, device: str, percent: int) -> None:
        self._cast(device).set_volume(max(0, min(100, percent)) / 100)

    def close(self) -> None:
        try:
            self._browser.stop_discovery()
        except Exception:                               # noqa: BLE001
            pass


# ---- the agent ---------------------------------------------------------------------------------------------------------------------------
class Agent:
    def __init__(self, transport, backend: Backend, proxy: MediaProxy, log=log_line):
        self.transport, self.backend, self.proxy, self.log = transport, backend, proxy, log
        self.errors: dict = {}                            # device id -> what went wrong with the last command (shown until the next one works)

    def status_payload(self) -> dict:
        devices = []
        for device in self.backend.devices():
            entry = {"id": device["id"], "name": device["name"]}
            try:
                entry.update(self.backend.status(device["id"]))
            except Exception as e:                       # noqa: BLE001 - a Chromecast that does not answer is reported, not fatal
                entry.update({"state": "idle", "error": "does not answer (%s)" % e.__class__.__name__})
            if self.errors.get(device["id"]) and not entry.get("error"):
                entry["error"] = self.errors[device["id"]]
            devices.append(entry)
        return {"devices": devices}

    def media_url(self, path: str) -> str:
        """The pluteus address for a path the server sent (it already carries any base path of the site)."""
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("the server sent an address that is not a path on itself")
        return self.transport.origin() + path

    def handle(self, command: dict) -> None:
        op, device = command.get("op"), str(command.get("device", ""))
        try:
            if op == "play":
                local = self.proxy.register(self.media_url(str(command["url"])))
                self.backend.play(device, local, str(command.get("mime", "")), str(command.get("title", "")), float(command.get("start") or 0))
            elif op == "pause":
                self.backend.pause(device)
            elif op == "resume":
                self.backend.resume(device)
            elif op == "stop":
                self.backend.stop(device)
            elif op == "seek":
                self.backend.seek(device, float(command["value"]))
            elif op == "volume":
                self.backend.set_volume(device, int(command["value"]))
            else:
                raise ValueError("unknown command %r" % (op,))
            self.errors.pop(device, None)
            self.log("%s on %s" % (op, device))
        except Exception as e:                           # noqa: BLE001 - reported to the user through the next status
            self.errors[device] = ("%s failed: %s" % (op, e))[:200]
            self.log("%s on %s failed: %s" % (op, device, e))

    def send_status(self) -> None:
        status, _ = self.transport.request("POST", "/cast-agent/status", self.status_payload(), timeout=15.0)
        self._check(status)

    def poll_once(self, wait: float = POLL_WAIT) -> int:
        """Ask for commands (waiting up to `wait` seconds) and carry them out; the number of commands handled."""
        status, data = self.transport.request("GET", "/cast-agent/poll?wait=%g" % wait, timeout=wait + 15.0)
        self._check(status)
        commands = data.get("commands", []) if isinstance(data, dict) else []
        for command in commands:
            if isinstance(command, dict):
                self.handle(command)
        return len(commands)

    @staticmethod
    def _check(status: int) -> None:
        if status in (401, 403, 429):
            raise Refused("the server refused the token (HTTP %d)" % status)
        if status != 200:
            raise ConnectionError("the server answered HTTP %d" % status)

    def _loop(self, step, stop: threading.Event, every: float) -> None:
        while not stop.is_set():
            wait = every
            try:
                step()
            except Refused as e:
                self.log("%s: check the token; trying again in %d seconds" % (e, REFUSED_WAIT))
                wait = REFUSED_WAIT
            except Exception as e:                       # noqa: BLE001 - a network slip or a strange reply must not end the agent
                self.log("cannot talk to pluteus (%s: %s); trying again" % (e.__class__.__name__, e))
                wait = RETRY_WAIT
            stop.wait(wait)

    def run(self, stop: threading.Event) -> None:
        threading.Thread(target=self._loop, args=(self.send_status, stop, STATUS_EVERY), daemon=True).start()
        self._loop(self.poll_once, stop, 0.0)


# ---- the command line ----------------------------------------------------------------------------------------------------------------------
def lan_address() -> str:
    """This machine's address on the network it reaches the world through (nothing is sent)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="castagent", description="Plays pluteus files on the Chromecasts of this network.")
    ap.add_argument("--server", default=os.environ.get("PLUTEUS_SERVER", ""), help="the pluteus address, e.g. https://pluteus.example.org (or a tailnet address)")
    ap.add_argument("--token-file", default="", help="a file holding the agent token (made by: pluteus cast-token --name SITE); or set PLUTEUS_CAST_TOKEN")
    ap.add_argument("--host", action="append", default=[], metavar="ADDRESS", help="a Chromecast's address; repeat for more. Without it they are searched for")
    ap.add_argument("--listen-port", type=int, default=8765, help="the port of the agent's own web server (default 8765)")
    ap.add_argument("--advertise-host", default="", help="the address of this machine as the Chromecasts see it (default: found automatically)")
    args = ap.parse_args(argv)
    token = os.environ.get("PLUTEUS_CAST_TOKEN", "")
    if args.token_file:
        try:
            with open(args.token_file, "r", encoding="utf-8") as f:
                token = f.read().strip()
        except OSError as e:
            print("Error: cannot read the token file: %s" % e, file=sys.stderr)
            return 2
    if not args.server or not token:
        print("Error: give --server and a token (--token-file or PLUTEUS_CAST_TOKEN).", file=sys.stderr)
        return 2
    if urlsplit(args.server).scheme not in ("http", "https") or not urlsplit(args.server).netloc:
        print("Error: --server must look like https://host[:port][/base]", file=sys.stderr)
        return 2
    try:
        backend = PyChromecastBackend(args.host)
    except ImportError:
        print("Error: the pychromecast library is not installed (pip install pychromecast).", file=sys.stderr)
        return 2
    transport = HttpTransport(args.server, token)
    proxy = MediaProxy(transport.origin(), args.advertise_host or lan_address(), args.listen_port)
    proxy.start()
    log_line("cast agent %d: serving on %s:%d, talking to %s" % (VERSION, proxy.advertise_host, proxy.port, args.server))
    stop = threading.Event()
    try:
        Agent(transport, backend, proxy).run(stop)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        proxy.stop()
        backend.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

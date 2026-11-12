"""Fetching a station logo from an address that came from a request, safely.

The address is untrusted. The server therefore only connects to a public address (every address a name resolves to must be public, and
the connection goes to that address, so the name cannot change its answer between the check and the connection), only on the usual web
ports, follows at most three redirects (each one checked again), reads a limited amount in a limited time, and never trusts the
reply's own description of itself: the bytes must open as a PNG, JPEG, GIF or WebP picture, and what is kept is a fresh, small PNG or
JPEG that this module wrote, never the downloaded bytes."""
from __future__ import annotations

import http.client
import io
import ipaddress
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _LookupTimeout
from urllib.parse import urljoin, urlsplit

from .thumbs import MAX_PIXELS, pillow

MAX_BYTES = 1_500_000
MAX_SIDE = 256
TIMEOUT = 6.0                         # one blocking step (connect, handshake, a read)
DEADLINE = 10.0                       # the whole fetch, from the name look-up to the last byte
PORTS = {"http": (80,), "https": (443,)}
FORMATS = {"PNG", "JPEG", "GIF", "WEBP"}


class Refused(Exception):
    """The address or the reply is not acceptable (the logo is simply left out)."""


# Hosts the owner has declared trusted (config: relay_allow = ["192.168.1.99:9191"]), as exact "host:port" strings. Only these may be
# on a private address or an unusual port; everything else keeps every rule below. Set once at start-up from the configuration.
_ALLOWED: frozenset = frozenset()


def set_allowed(entries) -> None:
    global _ALLOWED
    _ALLOWED = frozenset(str(e).strip().lower() for e in (entries or ()) if str(e).strip())


def is_allowed(host: str, port: int) -> bool:
    return ("%s:%d" % ((host or "").lower(), port)) in _ALLOWED


_NAT64 = ipaddress.ip_network("64:ff9b::/96")             # addresses in it are IPv4 addresses in disguise: on a NAT64 network they reach private hosts


def _public_addresses(host: str, port: int) -> list:
    try:
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, ValueError):
        raise Refused("unknown host")
    addresses = []
    trusted = is_allowed(host, port)
    for family, _, _, _, sockaddr in found:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            raise Refused("not a public address")
        hidden = not ip.is_global or (ip.version == 6 and ip in _NAT64)
        if hidden and not trusted:                        # private, loopback, link-local, multicast, reserved, unspecified, NAT64 ... (unless the owner listed this host)
            raise Refused("not a public address")
        addresses.append((family, sockaddr))
    if not addresses:
        raise Refused("unknown host")
    return addresses


def _split(url: str):
    try:
        parts = urlsplit(url)
        scheme, host, port = parts.scheme, parts.hostname, parts.port          # .port raises ValueError for ":abc" and ":99999"
        named = parts.username or parts.password
    except ValueError:                                    # a bad port, a bracket that never closes: not an address, and never an error
        raise Refused("not a plain web address")
    if scheme not in PORTS or not host or named:
        raise Refused("not a plain web address")
    port = port or PORTS[scheme][0]
    if port not in PORTS[scheme] and not is_allowed(host, port):
        raise Refused("unusual port")
    path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
    return scheme, host, port, path


def _left(deadline: float | None, limit: float = TIMEOUT) -> float:
    """The timeout for the next blocking step: `limit`, or what is left before the overall deadline when that is less. Raises
    Refused when the deadline has passed. No deadline (the stream relay): always `limit`."""
    if deadline is None:
        return limit
    left = deadline - time.monotonic()
    if left <= 0:
        raise Refused("too slow")
    return min(limit, left)


class _TimedReader(io.RawIOBase):
    def __init__(self, timed):
        self._timed = timed

    def readable(self):
        return True

    def readinto(self, buffer):
        return self._timed.recv_into(buffer)

    def close(self):
        if not self.closed:
            super().close()
            self._timed._reader_closed()


class _Timed:
    """The connected socket as http.client uses it (sendall, makefile, close), with the timeout set again before every blocking
    step to the time left before the overall deadline. A plain socket timeout starts afresh with every read, so a server that
    sends one byte every few seconds could hold a worker thread for days."""

    def __init__(self, sock, deadline: float, limit: float):
        self._sock, self._deadline, self._limit = sock, deadline, limit
        self._readers, self._closed = 0, False

    def _arm(self) -> None:
        self._sock.settimeout(_left(self._deadline, self._limit))

    def sendall(self, data, *args):
        self._arm()
        return self._sock.sendall(data, *args)

    def recv_into(self, buffer, *args):
        self._arm()
        return self._sock.recv_into(buffer, *args)

    def makefile(self, mode="rb", *args, **kw):
        self._readers += 1
        return io.BufferedReader(_TimedReader(self))

    def _reader_closed(self) -> None:
        self._readers -= 1
        if self._closed and self._readers <= 0:
            self._sock.close()

    def close(self):
        # Like a real socket: http.client closes the connection as soon as it has the reply's headers ("Connection: close") and
        # goes on reading the body from the file it made, so the socket itself closes when that file does.
        self._closed = True
        if self._readers <= 0:
            self._sock.close()

    def __getattr__(self, name):
        return getattr(self._sock, name)


class _Connection(http.client.HTTPConnection):
    """Connects to an address that was already checked, not to the name (which could answer differently a second time).
    With a `deadline` (time.monotonic) the whole exchange ends by then: connecting, the TLS handshake, sending, every read."""

    def __init__(self, host, port, sockaddr, family, timeout, deadline: float | None = None):
        super().__init__(host, port, timeout=timeout)
        self._target = (sockaddr, family)
        self._deadline = deadline

    def _secure(self, sock):
        return sock

    def connect(self):
        sockaddr, family = self._target
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.settimeout(_left(self._deadline, self.timeout))
            sock.connect(sockaddr)
            sock = self._secure(sock)
        except BaseException:
            sock.close()
            raise
        self.sock = sock if self._deadline is None else _Timed(sock, self._deadline, self.timeout)


class _SecureConnection(_Connection):
    def _secure(self, sock):
        sock.settimeout(_left(self._deadline, self.timeout))            # the handshake is a blocking step of its own
        context = ssl.create_default_context()
        return context.wrap_socket(sock, server_hostname=self.host)


# Name look-ups cannot be given a timeout, so they run on a few threads of their own and the caller waits no longer than the
# deadline allows. A look-up that hangs keeps its thread until the system gives up; with all of them busy, new look-ups are refused.
_RESOLVER = ThreadPoolExecutor(max_workers=4, thread_name_prefix="logo-dns")


def _resolve(host: str, port: int, deadline: float) -> list:
    """_public_addresses within the deadline."""
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if literal:
        found = _public_addresses(host, port)             # an address needs no look-up: nothing to wait for
    else:
        job = _RESOLVER.submit(_public_addresses, host, port)
        try:
            found = job.result(timeout=_left(deadline))
        except _LookupTimeout:
            job.cancel()
            raise Refused("too slow")
    _left(deadline)
    return found


def download(url: str, deadline: float | None = None) -> bytes:
    """The body of the address (at most MAX_BYTES). Raises Refused for anything not acceptable. One deadline (DEADLINE seconds from
    now, unless one is given) covers everything: the name look-up, connecting, the TLS handshake, the headers, the body and every
    redirect."""
    deadline = deadline or time.monotonic() + DEADLINE
    for _ in range(4):
        scheme, host, port, path = _split(url)
        family, sockaddr = _resolve(host, port, deadline)[0]
        connection = (_SecureConnection if scheme == "https" else _Connection)(host, port, sockaddr, family, TIMEOUT, deadline)
        try:
            connection.request("GET", path, headers={"Host": host if port in (80, 443) else "%s:%d" % (host, port),
                                                     "User-Agent": "pluteus-logo/1", "Accept": "image/*", "Connection": "close"})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location") or ""
                if not location:
                    raise Refused("redirect without a place")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise Refused("HTTP %d" % response.status)
            body = bytearray()
            while len(body) <= MAX_BYTES:
                _left(deadline)
                chunk = response.read(16384)
                if not chunk:
                    return bytes(body)
                body += chunk
            raise Refused("too large")
        except (OSError, http.client.HTTPException) as e:
            raise Refused("could not fetch: %s" % e.__class__.__name__)
        finally:
            connection.close()
    raise Refused("too many redirects")


def small_picture(data: bytes, side: int = MAX_SIDE):
    """(bytes, extension) of a fresh picture made from `data`, at most `side` wide or tall; Refused when it is not a picture we take."""
    Image = pillow()
    if Image is None:
        raise Refused("no picture library")
    try:
        image = Image.open(io.BytesIO(data))
        if image.format not in FORMATS:
            raise Refused("not a picture we take")
        if image.width * image.height > MAX_PIXELS:        # from the header: nothing has been decoded yet (Pillow's own limit only warns)
            raise Refused("too many pixels")
        image.load()
        if image.width < 8 or image.height < 8:
            raise Refused("too small")
        image.thumbnail((side, side))
        out = io.BytesIO()
        if "A" in image.getbands() or image.mode in ("P", "LA", "PA") or "transparency" in image.info:
            image.convert("RGBA").save(out, "PNG", optimize=True)
            return out.getvalue(), "png"
        image.convert("RGB").save(out, "JPEG", quality=88, optimize=True)
        return out.getvalue(), "jpg"
    except Refused:
        raise
    except Exception:                                      # noqa: BLE001  a damaged or hostile file
        raise Refused("not a readable picture")


def fetch_logo(url: str):
    """(bytes, extension) of a small picture from the address, or None (a logo is optional: never an error for the caller)."""
    try:
        return small_picture(download(url))
    except (Refused, ValueError):                          # ValueError: an address so malformed that a library choked on it
        return None

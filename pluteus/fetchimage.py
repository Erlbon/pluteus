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
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 1_500_000
MAX_SIDE = 256
TIMEOUT = 6.0
DEADLINE = 10.0
PORTS = {"http": (80,), "https": (443,)}
FORMATS = {"PNG", "JPEG", "GIF", "WEBP"}


class Refused(Exception):
    """The address or the reply is not acceptable (the logo is simply left out)."""


def _public_addresses(host: str, port: int) -> list:
    try:
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise Refused("unknown host")
    addresses = []
    for family, _, _, _, sockaddr in found:
        ip = ipaddress.ip_address(sockaddr[0])
        if not ip.is_global:                              # private, loopback, link-local, multicast, reserved, unspecified ...
            raise Refused("not a public address")
        addresses.append((family, sockaddr))
    if not addresses:
        raise Refused("unknown host")
    return addresses


def _split(url: str):
    parts = urlsplit(url)
    if parts.scheme not in PORTS or not parts.hostname or parts.username or parts.password:
        raise Refused("not a plain web address")
    port = parts.port or PORTS[parts.scheme][0]
    if port not in PORTS[parts.scheme]:
        raise Refused("unusual port")
    path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
    return parts.scheme, parts.hostname, port, path


class _Connection(http.client.HTTPConnection):
    """Connects to an address that was already checked, not to the name (which could answer differently a second time)."""

    def __init__(self, host, port, sockaddr, family, timeout):
        super().__init__(host, port, timeout=timeout)
        self._target = (sockaddr, family)

    def connect(self):
        sockaddr, family = self._target
        self.sock = socket.socket(family, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(sockaddr)


class _SecureConnection(_Connection):
    def connect(self):
        super().connect()
        context = ssl.create_default_context()
        self.sock = context.wrap_socket(self.sock, server_hostname=self.host)


def download(url: str, deadline: float | None = None) -> bytes:
    """The body of the address (at most MAX_BYTES). Raises Refused for anything not acceptable."""
    deadline = deadline or time.monotonic() + DEADLINE
    for _ in range(4):
        scheme, host, port, path = _split(url)
        family, sockaddr = _public_addresses(host, port)[0]
        connection = (_SecureConnection if scheme == "https" else _Connection)(host, port, sockaddr, family, TIMEOUT)
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
                if time.monotonic() > deadline:
                    raise Refused("too slow")
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


def small_picture(data: bytes):
    """(bytes, extension) of a fresh picture made from `data`, at most MAX_SIDE wide or tall; Refused when it is not a picture we take."""
    try:
        from PIL import Image
    except ImportError:
        raise Refused("no picture library")
    try:
        Image.MAX_IMAGE_PIXELS = 25_000_000
        image = Image.open(io.BytesIO(data))
        if image.format not in FORMATS:
            raise Refused("not a picture we take")
        image.load()
        if image.width < 8 or image.height < 8:
            raise Refused("too small")
        image.thumbnail((MAX_SIDE, MAX_SIDE))
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
    except Refused:
        return None

"""The stream relay: the server fetches a radio stream on the browser's behalf and passes it on.

Why: a browser cannot play a plain http:// stream on an https:// page (mixed content) and cannot read a stream whose server does not
allow web pages (CORS). The relay is off by default (`relay = true`), only for users with an upload folder, and only fetches the address a
station file of the user's own holds (the page names the file and the number of the station, never an address).

The address is untrusted like any other: only http and https, no user name or password in it, every address the name resolves to must be
public (the connection goes to the checked address itself), at most three redirects with each one checked again, a time limit to connect
which also ends a stream that stalls, and the reply must be an audio stream. The limits on how many streams are open at once are per user
and in total. This reuses the address guard of pluteus/fetchimage.py."""
from __future__ import annotations

import http.client
import threading
import time
from urllib.parse import urljoin, urlsplit

from . import fetchimage

TIMEOUT = 15.0            # seconds to connect, and the longest a stream may stall before the relay ends it
CHUNK = 16384
AUDIO = ("audio/", "application/ogg")
FALLBACK_TYPE = "audio/mpeg"


class Refused(Exception):
    """The address or the reply is not acceptable: `message` is a sentence for the user, `status` the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message, self.status = message, status


class Limiter:
    """How many streams are open: at most `per_user` for one user and `total` altogether."""

    def __init__(self, per_user: int, total: int):
        self.per_user, self.total = per_user, total
        self._open: dict = {}
        self._lock = threading.Lock()

    def acquire(self, user: str) -> bool:
        with self._lock:
            if sum(self._open.values()) >= self.total or self._open.get(user, 0) >= self.per_user:
                return False
            self._open[user] = self._open.get(user, 0) + 1
            return True

    def release(self, user: str) -> None:
        with self._lock:
            left = self._open.get(user, 0) - 1
            if left > 0:
                self._open[user] = left
            else:
                self._open.pop(user, None)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._open)


def _split(url: str):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise Refused("That address is not a plain web address.", 400)
    try:
        port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise Refused("That address has a bad port.", 400)
    if not 1 <= port <= 65535:
        raise Refused("That address has a bad port.", 400)
    return parts.scheme, parts.hostname, port, (parts.path or "/") + ("?" + parts.query if parts.query else "")


def open_stream(url: str):
    """(connection, response, content type) for the stream, after the checks. The caller closes the connection. Raises Refused."""
    for _ in range(4):
        scheme, host, port, path = _split(url)
        try:
            family, sockaddr = fetchimage._public_addresses(host, port)[0]
        except fetchimage.Refused:
            raise Refused("The stream's server is not a public address.", 502)
        cls = fetchimage._SecureConnection if scheme == "https" else fetchimage._Connection
        connection = cls(host, port, sockaddr, family, TIMEOUT)         # one limit for connecting and for every read: a stalled stream ends
        try:
            connection.request("GET", path, headers={"Host": host if port in (80, 443) else "%s:%d" % (host, port),
                                                     "User-Agent": "pluteus-relay/1", "Accept": "*/*", "Connection": "close"})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location") or ""
                connection.close()
                if not location:
                    raise Refused("The stream's server sent a redirect without a place.")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                connection.close()
                raise Refused("The stream's server answered %d." % response.status)
            kind = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
            if kind in ("", "application/octet-stream", "binary/octet-stream"):
                kind = FALLBACK_TYPE                      # Shoutcast and old Icecast servers often send no type
            if not kind.startswith(AUDIO):
                connection.close()
                raise Refused("That is not an audio stream (%s)." % (kind or "unknown type"), 415)
            return connection, response, kind
        except Refused:
            raise
        except (OSError, http.client.HTTPException) as e:
            connection.close()
            raise Refused("Could not reach the stream's server (%s)." % e.__class__.__name__)
    raise Refused("The stream's server redirected too many times.")


def read_chunk(response) -> bytes:
    """The next piece of the stream (blocks until there is some); b'' at the end or when the stream stalls longer than the idle limit."""
    try:
        return response.read1(CHUNK)
    except (OSError, http.client.HTTPException):
        return b""

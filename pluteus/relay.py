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
import re
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


def open_stream(url: str, extra: dict | None = None):
    """(connection, response, content type) for the stream, after the checks. The caller closes the connection. Raises Refused.
    `extra`: more request headers (the now-playing look-up asks for Icy-MetaData)."""
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
                                                     "User-Agent": "pluteus-relay/1", "Accept": "*/*", "Connection": "close", **(extra or {})})
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


# ---- now playing: the title a radio station sends inside its stream (ICY metadata, what DAB shows as the programme text) ---------------
MAX_METAINT = 64000            # the stream sends a metadata block after this many audio bytes (usually 8192 or 16000)
PEEK_SECONDS = 8.0
CACHE_SECONDS = 10.0
_STREAM_TITLE = re.compile(r"StreamTitle='(.*?)';", re.DOTALL)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_cache: dict = {}
_cache_lock = threading.Lock()
_peeks = threading.BoundedSemaphore(4)       # at most four look-ups at once, however many listeners ask


def parse_stream_title(block: bytes) -> str:
    """The text of StreamTitle='...'; in an ICY metadata block ('' when there is none). Stations send UTF-8 or Latin-1."""
    try:
        text = block.rstrip(b"\x00").decode("utf-8")
    except UnicodeDecodeError:
        text = block.rstrip(b"\x00").decode("latin-1")
    match = _STREAM_TITLE.search(text)
    return _CONTROL.sub(" ", match.group(1)).strip()[:200] if match else ""


def split_title(text: str) -> dict:
    """{"text": "Artist - Title", "artist": "Artist", "title": "Title"} (artist and title only when the text has the usual ' - ')."""
    out = {"text": text}
    if " - " in text:
        artist, _, title = text.partition(" - ")
        if artist.strip() and title.strip():
            out["artist"], out["title"] = artist.strip(), title.strip()
    return out


def _read_exact(response, count: int, deadline: float) -> bytes:
    data = bytearray()
    while len(data) < count:
        if time.monotonic() > deadline:
            raise Refused("too slow")
        chunk = response.read1(min(CHUNK, count - len(data)))
        if not chunk:
            raise Refused("the stream ended")
        data += chunk
    return bytes(data)


def now_playing(url: str, station_name: str = "") -> dict:
    """The title the station is playing now, as {"text", "artist", "title"}, or {} (the station sends none, or nothing useful). Opens the
    stream for a moment with Icy-MetaData: 1, reads up to the first metadata block (about 16 KB) and closes: the audio is not carried.
    Answers are kept for ten seconds, so any number of listeners cost one look-up. Never raises."""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(url)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    result: dict = {}
    if _peeks.acquire(blocking=False):
        connection = None
        try:
            connection, response, _ = open_stream(url, {"Icy-MetaData": "1"})
            try:
                metaint = int(response.getheader("icy-metaint") or 0)
            except ValueError:
                metaint = 0
            if 0 < metaint <= MAX_METAINT:
                deadline = time.monotonic() + PEEK_SECONDS
                _read_exact(response, metaint, deadline)                         # the audio before the first block: thrown away
                size = _read_exact(response, 1, deadline)[0] * 16
                if size:
                    text = parse_stream_title(_read_exact(response, size, deadline))
                    if text and text.lower() != station_name.strip().lower():     # many stations just repeat their own name
                        result = split_title(text)
        except (Refused, OSError, http.client.HTTPException, ValueError):
            result = {}
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:                                                # noqa: BLE001
                    pass
            _peeks.release()
    with _cache_lock:
        if len(_cache) > 200:
            _cache.clear()
        _cache[url] = (time.monotonic(), result)
    return result

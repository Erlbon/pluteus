"""The stream relay: the server fetches a radio stream on the browser's behalf and passes it on.

Why: a browser cannot play a plain http:// stream on an https:// page (mixed content) and cannot read a stream whose server does not
allow web pages (CORS). The relay is off by default (`relay = true`), only for users with an upload folder, and only fetches the address a
station file of the user's own holds (the page names the file and the number of the station, never an address).

The address is untrusted like any other: only http and https, no user name or password in it, every address the name resolves to must be
public (the connection goes to the checked address itself), at most three redirects with each one checked again, a time limit to connect
which also ends a stream that stalls, and the reply must be an audio stream. The limits on how many streams are open at once are per user
and in total. This reuses the address guard of pluteus/fetchimage.py."""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import re
import secrets
import threading
import time
from urllib.parse import urljoin, urlsplit

from . import fetchimage, vault

TIMEOUT = 15.0            # seconds to connect, and the longest a stream may stall before the relay ends it
CHUNK = 16384
AUDIO = ("audio/", "application/ogg")
FALLBACK_TYPE = "audio/mpeg"
TS = ("video/mp2t", "video/mpeg", "video/x-mpegts", "video/x-ts")       # an MPEG transport stream (an IPTV panel's channel)
TS_TYPE = "video/mp2t"


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
        self._leases: dict = {}
        self._pieces: dict = {}
        self._streams: dict = {}
        self._totals: dict = {}
        self._serial = 0
        self._stopped: dict = {}                    # (user, channel key) -> until when a stopped HLS channel is refused
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

    # what the admin page shows: who has a stream open (a label, never an address), how much each user has carried since the start, and
    # a Stop that ends one (the pump looks at the flag between chunks)
    def register(self, user: str, label: str, kind: str) -> dict:
        with self._lock:
            self._serial += 1
            handle = {"id": self._serial, "user": user, "label": label, "kind": kind, "since": time.time(), "bytes": 0, "stop": False}
            self._streams[self._serial] = handle
            return handle

    def unregister(self, handle: dict) -> None:
        with self._lock:
            self._streams.pop(handle["id"], None)
            totals = self._totals
            totals[handle["user"]] = totals.get(handle["user"], 0) + handle["bytes"]
            self._totals = totals

    def streams(self) -> list:
        with self._lock:
            return [dict(h) for h in self._streams.values()]

    def totals(self) -> dict:
        """Bytes carried per user since the start: finished streams plus what the open ones have carried so far."""
        with self._lock:
            out = dict(self._totals)
            for h in self._streams.values():
                out[h["user"]] = out.get(h["user"], 0) + h["bytes"]
            return out

    def stop(self, stream_id: int) -> bool:
        with self._lock:
            handle = self._streams.get(stream_id)
            if handle is None:
                return False
            handle["stop"] = True
            return True

    # an HLS stream is many short requests, so it holds a lease that every playlist refresh renews and that lapses when the player leaves
    def lease(self, user: str, key: str, ttl: float = 45.0) -> bool:
        now = time.monotonic()
        with self._lock:
            if self._stopped.get((user, key), 0) > now:
                return False
            leases = {k: v for k, v in getattr(self, "_leases", {}).items() if v > now}
            held = [k for k in leases if k[0] == user]
            if (user, key) not in leases and (len(held) + self._open.get(user, 0) >= self.per_user
                                              or len(leases) + sum(self._open.values()) >= self.total):
                self._leases = leases
                return False
            leases[(user, key)] = now + ttl
            self._leases = leases
            return True

    def lease_list(self) -> list:
        """(user, label) of the TV channels that are streaming now (a lease that has not lapsed)."""
        now = time.monotonic()
        with self._lock:
            return sorted(k for k, v in self._leases.items() if v > now)

    def stop_lease(self, user: str, key: str, hold: float = 120.0) -> bool:
        """The admin's Stop for an HLS channel: its lease goes and its playlist and pieces are refused for `hold` seconds, so the player
        gives up. True when the channel was streaming."""
        now = time.monotonic()
        with self._lock:
            live = self._leases.pop((user, key), 0) > now
            self._stopped = {k: v for k, v in self._stopped.items() if v > now}
            self._stopped[(user, key)] = now + hold
            return live

    def stopped(self, user: str, key: str) -> bool:
        with self._lock:
            return self._stopped.get((user, key), 0) > time.monotonic()

    def drop_lease(self, user: str, key: str) -> None:
        """A playlist that could not be fetched does not keep its place."""
        with self._lock:
            self._leases.pop((user, key), None)

    def acquire_piece(self, user: str, per_user: int = 8) -> bool:
        with self._lock:
            pieces = getattr(self, "_pieces", {})
            if pieces.get(user, 0) >= per_user or sum(pieces.values()) >= 8 * self.total:
                return False
            pieces[user] = pieces.get(user, 0) + 1
            self._pieces = pieces
            return True

    def release_piece(self, user: str) -> None:
        with self._lock:
            pieces = getattr(self, "_pieces", {})
            left = pieces.get(user, 0) - 1
            if left > 0:
                pieces[user] = left
            else:
                pieces.pop(user, None)
            self._pieces = pieces


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


def open_stream(url: str, extra: dict | None = None, accept=AUDIO, entry: dict | None = None, fallback: str = FALLBACK_TYPE):
    """(connection, response, content type) for the stream, after the checks. The caller closes the connection. Raises Refused.
    `extra`: more request headers (the now-playing look-up asks for Icy-MetaData). `accept`: the content types allowed (audio by default;
    None for the pieces of an HLS stream, which are checked by what they contain). `entry`: a stored stream (vault.py): its login and headers are
    sent only to the hosts it names, decided again for every hop, so a redirect to another host never carries them.
    `connection.final_url` is the address after redirects."""
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
                                                     "User-Agent": "pluteus-relay/1", "Accept": "*/*", "Connection": "close",
                                                     **(vault.request_headers(entry, host) if entry else {}), **(extra or {})})
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
            connection.final_url = url
            if accept is None:
                return connection, response, kind
            if kind in ("", "application/octet-stream", "binary/octet-stream"):
                kind = fallback                           # Shoutcast and old Icecast servers often send no type (an IPTV panel often sends none either)
            if not kind.startswith(accept):
                connection.close()
                raise Refused("That is not %s (%s)." % ("a transport stream" if accept == TS else "an audio stream", kind or "unknown type"), 415)
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


def now_playing(url: str, station_name: str = "", entry: dict | None = None, user: str = "") -> dict:
    """The title the station is playing now, as {"text", "artist", "title"}, or {} (the station sends none, or nothing useful). Opens the
    stream for a moment with Icy-MetaData: 1, reads up to the first metadata block (about 16 KB) and closes: the audio is not carried.
    Answers are kept for ten seconds, so any number of listeners cost one look-up. Never raises."""
    now = time.monotonic()
    key = (user, url) if entry else url                  # a protected stream's title is kept per user, never shown to another
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    result: dict = {}
    if _peeks.acquire(blocking=False):
        connection = None
        try:
            connection, response, _ = open_stream(url, {"Icy-MetaData": "1"}, entry=entry)
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
        _cache[key] = (time.monotonic(), result)
    return result


# ---- HLS (TV channels): the playlist is rewritten so that every piece goes through the server --------------------------------------------
_KEY = secrets.token_bytes(32)          # signs the tokens below; new at every start (a running player just asks for its playlist again)
TOKEN_SECONDS = 3600
MAX_PLAYLIST = 1_000_000
MAX_PIECE = 30_000_000
_URI_ATTRIBUTE = re.compile(r'URI="([^"]*)"')


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(url: str, user: str, lease: str, now: float | None = None, stream: str = "") -> str:
    """A token that names `url` for this user and no other: only the server can make one, so a page can only fetch what the server
    offered it in a playlist it fetched itself. It lapses after an hour. `stream` is the NAME of the stored stream the pieces need (never a
    login or a header: those are looked up when the piece is fetched)."""
    payload = json.dumps({"u": url, "n": user, "k": lease, "s": stream, "e": int((now or time.time()) + TOKEN_SECONDS)}, separators=(",", ":")).encode("utf-8")
    body = _b64(payload)
    return body + "." + _b64(hmac.new(_KEY, body.encode("ascii"), hashlib.sha256).digest()[:18])


def verify(token: str, user: str, now: float | None = None):
    """(url, lease key, stored stream name) for a token made by sign() for this user and still valid, else None."""
    try:
        body, _, signature = token.partition(".")
        good = _b64(hmac.new(_KEY, body.encode("ascii"), hashlib.sha256).digest()[:18])
        if not hmac.compare_digest(signature, good):
            return None
        data = json.loads(_unb64(body).decode("utf-8"))
        if data.get("n") != user or int(data.get("e", 0)) < (now or time.time()):
            return None
        return str(data["u"]), str(data.get("k", "")), str(data.get("s", ""))
    except (ValueError, KeyError, TypeError, UnicodeError):
        return None


def rewrite_playlist(text: str, base_url: str, point) -> str:
    """The HLS playlist with each address (a line of its own, or URI="..." in a tag: key, map, media, variant) replaced by point(address),
    resolved against the playlist's own address. Anything that is not a plain http(s) address is left as it is."""
    def target(ref: str) -> str:
        full = urljoin(base_url, ref.strip())
        return point(full) if urlsplit(full).scheme in ("http", "https") else ref
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if not stripped:
            out.append(line)
        elif stripped.startswith("#"):
            out.append(_URI_ATTRIBUTE.sub(lambda m: 'URI="%s"' % target(m.group(1)), line) if "URI=" in line else line)
        else:
            out.append(target(stripped))
    return "\n".join(out)


def fetch_body(url: str, cap: int, entry: dict | None = None):
    """(bytes, content type, final address) of a small reply (a playlist or one piece of a stream), after the checks; Refused if it is
    larger than `cap` bytes."""
    connection, response, kind = open_stream(url, accept=None, entry=entry)
    try:
        data = bytearray()
        while len(data) <= cap:
            chunk = response.read1(CHUNK)
            if not chunk:
                return bytes(data), kind, connection.final_url
            data += chunk
        raise Refused("A piece of the stream is too large.")
    except (OSError, http.client.HTTPException):
        raise Refused("The stream's server stopped answering.")
    finally:
        connection.close()


def is_playlist(data: bytes) -> bool:
    return data[:1024].lstrip(b"\xef\xbb\xbf \r\n\t").startswith(b"#EXTM3U")


def piece_type(data: bytes, url: str) -> str:
    """The type of a piece of a stream, decided by what it contains (never taken from the source): transport stream, fragmented mp4,
    AAC, WebVTT; anything else is an opaque download."""
    if data[:1] == b"\x47" and (len(data) < 189 or data[188:189] == b"\x47"):
        return "video/mp2t"
    if data[4:8] in (b"ftyp", b"styp", b"moof", b"moov", b"sidx"):
        return "video/mp4"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xf1", b"\xff\xf9"):
        return "audio/aac"
    if data[:6] == b"WEBVTT":
        return "text/vtt"
    return "application/octet-stream"

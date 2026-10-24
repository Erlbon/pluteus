"""Casting: play a file of the library on a Chromecast, through a cast agent.

A Chromecast cannot log in, cannot join a tailnet and fetches its media itself, so pluteus does not talk to it. A small separate program, the
cast agent (castagent.py), runs on the same network as the Chromecasts (a Pi, a mini PC, the Docker host) and reaches pluteus over the
network, outbound only: nothing has to be opened at that site. The agent
  * reports the Chromecasts it sees and what each is doing (POST /cast-agent/status),
  * asks for commands with a long poll (GET /cast-agent/poll) and carries them out,
  * fetches the media from a signed address (GET /cast/<token>/<name>) and hands it to the Chromecast on its own network.

The agent proves who it is with a token (Authorization: Bearer); the config keeps only the token's SHA-256. A media address carries a signed
token that names one file for one user and lapses after twelve hours: whoever holds it can read that file and nothing else, and the file is
checked again, with the user's current rights, every time it is fetched. The key that signs the tokens is kept in cast.key in the data folder, so
a running film survives a restart of the server.

This module holds what has no web framework in it: the tokens, the key, and the Hub that remembers each site's devices and queued commands."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections import deque

TOKEN_SECONDS = 12 * 3600          # a media address works this long (a long film, a paused evening)
POLL_SECONDS = 25.0                # how long a poll waits for a command before it answers with none
ONLINE_SECONDS = 60.0              # a site whose agent has not been heard from for this long is offline
COMMAND_SECONDS = 60.0             # a command nobody collected in this time is dropped (the agent was away: a late "play" would surprise)
MAX_QUEUE = 50                     # commands waiting for one site
MAX_DEVICES = 20
OPS = ("play", "pause", "resume", "stop", "seek", "volume")
STATES = ("idle", "playing", "paused", "buffering")
# what a Chromecast plays without conversion (the second generation: H.264 in MP4, VP8/VP9 in WebM; MP3, AAC, FLAC, Vorbis/Opus, WAV)
NATIVE_TYPES = ("audio/mpeg", "audio/mp4", "audio/aac", "audio/ogg", "audio/wav", "audio/flac", "video/mp4", "video/webm")
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Range, Content-Type",
        "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges"}
_DEVICE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_TOKEN_PATH = re.compile(r"/cast/[^/?#]+")


class CastError(Exception):
    """A refusal with a sentence for the user and the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


# ---- the signing key and the tokens ------------------------------------------------------------------------------------------------------
def load_key(data_dir: str) -> bytes:
    """The key that signs media addresses: cast.key in the data folder (made when missing), or one for this run only without a data folder.
    A key file that exists but cannot be used is an error, never replaced: replacing it would end every address in use."""
    if not data_dir:
        return secrets.token_bytes(32)
    path = os.path.join(data_dir, "cast.key")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="ascii") as f:
                text = f.read().strip()
            if not re.fullmatch(r"[0-9a-f]{64}", text):
                raise ValueError
            return bytes.fromhex(text)
        except (OSError, ValueError):
            raise CastError("%s exists but is not a usable key" % path, 500)
    os.makedirs(data_dir, exist_ok=True)
    key = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(key.hex() + "\n")
    return key


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(key: bytes, root: str, parts, user: str, now: float | None = None) -> str:
    """A token that names this file for this user and no other; only the server can make one."""
    payload = json.dumps({"r": root, "p": list(parts), "n": user, "e": int((now or time.time()) + TOKEN_SECONDS)}, separators=(",", ":")).encode("utf-8")
    body = _b64(payload)
    return body + "." + _b64(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())


def verify(key: bytes, token: str, now: float | None = None):
    """(root, parts, user name) for a token made by sign() and still valid, else None."""
    try:
        body, _, signature = token.partition(".")
        if not hmac.compare_digest(signature, _b64(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())):
            return None
        data = json.loads(_unb64(body).decode("utf-8"))
        if int(data["e"]) < (now or time.time()):
            return None
        parts = data["p"]
        if not isinstance(parts, list) or not all(isinstance(p, str) for p in parts):
            return None
        return str(data["r"]), tuple(parts), str(data["n"])
    except (ValueError, KeyError, TypeError, UnicodeError):
        return None


def new_agent_token() -> tuple:
    """(a fresh agent token, the line for the config): the token goes to the agent once, the config keeps only its hash."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return "sha256$" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def masked(path: str) -> str:
    """The path with a media token removed, for logs (the token is a twelve-hour key to one file)."""
    return _TOKEN_PATH.sub("/cast/***", path, count=1) if "/cast/" in path else path


# ---- what the agents report --------------------------------------------------------------------------------------------------------------
def _text(value, limit: int) -> str:
    return _CONTROL.sub("", str(value or "")).strip()[:limit]


def _number(value, high: float):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= high else None


def clean_devices(raw) -> list:
    """The devices an agent reported, made safe to store and show: nothing in it is trusted."""
    out = []
    for item in raw[:MAX_DEVICES] if isinstance(raw, list) else ():
        if not isinstance(item, dict) or not _DEVICE_ID.fullmatch(str(item.get("id", ""))):
            continue
        state = item.get("state")
        volume = _number(item.get("volume"), 100)
        out.append({"id": str(item["id"]), "name": _text(item.get("name"), 80) or str(item["id"]),
                    "state": state if state in STATES else "idle", "position": _number(item.get("position"), 10 ** 6),
                    "duration": _number(item.get("duration"), 10 ** 6), "volume": None if volume is None else round(volume),
                    "title": _text(item.get("title"), 200), "error": _text(item.get("error"), 200)})
    return out


class Hub:
    """The agents' sites: who is online, which Chromecasts they reported and the commands waiting for each. Used from the web handlers."""

    def __init__(self, sites):
        self._sites = {s.name: s for s in sites}
        self._lock = threading.Lock()
        self._state = {s.name: {"seen": 0.0, "devices": [], "queue": deque(), "serial": 0} for s in sites}

    def site_for_token(self, token: str) -> str:
        """The name of the site whose agent token this is, or ''. Every site is compared, so the time taken does not say which matched."""
        found = ""
        digest = hash_token(token or "")
        for site in self._sites.values():
            if hmac.compare_digest(site.token_hash, digest):
                found = site.name
        return found

    def touch(self, name: str) -> None:
        with self._lock:
            self._state[name]["seen"] = time.monotonic()

    def report(self, name: str, devices) -> None:
        with self._lock:
            state = self._state[name]
            state["devices"] = clean_devices(devices)
            state["seen"] = time.monotonic()

    def online(self, name: str) -> bool:
        with self._lock:
            seen = self._state[name]["seen"]
        return bool(seen) and time.monotonic() - seen < ONLINE_SECONDS

    def may_use(self, name: str, user_name: str) -> bool:
        site = self._sites.get(name)
        return site is not None and (not site.users or user_name in site.users)

    def snapshot(self, user_name: str) -> list:
        """The sites this user may cast to, with their devices (only while the agent is online)."""
        out = []
        for name in self._sites:
            if not self.may_use(name, user_name):
                continue
            online = self.online(name)
            with self._lock:
                devices = [dict(d) for d in self._state[name]["devices"]] if online else []
            out.append({"name": name, "online": online, "devices": devices})
        return out

    def enqueue(self, name: str, user_name: str, device: str, op: str, **fields) -> int:
        """Queue a command for a site's agent; CastError says why not. Returns the command's number."""
        if not self.may_use(name, user_name):
            raise CastError("There is no such place to cast to.", 404)
        if op not in OPS:
            raise CastError("That is not something a Chromecast can be told to do.", 400)
        if not self.online(name):
            raise CastError("The cast agent at %s is not connected right now." % name, 503)
        with self._lock:
            state = self._state[name]
            if not any(d["id"] == device for d in state["devices"]):
                raise CastError("That Chromecast is not known at %s." % name, 404)
            if len(state["queue"]) >= MAX_QUEUE:
                raise CastError("Too many commands are waiting for %s." % name, 429)
            state["serial"] += 1
            state["queue"].append({"id": state["serial"], "device": device, "op": op, "at": time.monotonic(), **fields})
            return state["serial"]

    def take(self, name: str) -> list:
        """The commands waiting for this site, oldest first (taken: the agent will not be offered them again); late ones are dropped."""
        now = time.monotonic()
        with self._lock:
            queue = self._state[name]["queue"]
            commands = [c for c in queue if now - c["at"] < COMMAND_SECONDS]
            queue.clear()
        return [{k: v for k, v in c.items() if k != "at"} for c in commands]

    async def wait_commands(self, name: str, timeout: float = POLL_SECONDS, step: float = 0.2) -> list:
        """The next commands for the site, waiting up to `timeout` seconds for one (a long poll). Counts as the agent being online."""
        deadline = time.monotonic() + timeout
        while True:
            self.touch(name)
            found = self.take(name)
            if found or time.monotonic() >= deadline:
                return found
            await asyncio.sleep(step)

"""Casting: signed media addresses, so a TV or Chromecast on the network can fetch one file from Pluteus without a login.

A casting device cannot log in, so Pluteus gives it an address that carries its own credential: `/cast/<token>/<name>`. The token is signed, names
ONE file for ONE user and lapses after twelve hours: whoever holds the address can read that file and nothing else, and the file is looked up again,
with that user's current rights, every time it is fetched (a removed user, a folder closed since or a deleted file ends it at once). The key that
signs the tokens is kept in cast.key in the data folder, so a film that is playing survives a restart of the server.

The devices themselves (which are configured, how each kind is told what to play) are in castdevices.py; this module has no web framework and
no network in it: the tokens, the key, and the little that both sides share."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time

TOKEN_SECONDS = 12 * 3600          # a media address works this long (a long film, a paused evening)
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Range, Content-Type",
        "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges"}
_TOKEN_PATH = re.compile(r"/cast/[^/?#]+")


class CastError(Exception):
    """A refusal with a sentence for the user and the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


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


def masked(path: str) -> str:
    """The path with a media token removed, for logs (the token is a twelve-hour key to one file)."""
    return _TOKEN_PATH.sub("/cast/***", path, count=1) if "/cast/" in path else path

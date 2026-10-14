"""The vault: stored logins and headers for protected streams, encrypted at rest.

An entry ("My subscription") holds an optional login or token, optional headers (Referer, User-Agent ...) and the host names those may be sent
to. It is stored in the state database as an opaque encrypted blob, per user; only that user's own relay requests ever open it.

The encryption uses nothing but the Python standard library, built from standard parts: keys are derived from the master key with HMAC-SHA256
(one for encryption, one for authentication), the data is encrypted by XOR with an HMAC-SHA256 keystream in counter mode (a fresh random
nonce for every entry), and an HMAC-SHA256 tag over nonce and ciphertext is checked, in constant time, before anything is decrypted
(encrypt-then-MAC). A wrong key or a changed blob is refused, never decrypted to garbage.

The master key is `relay_key` from the config when there is one; otherwise a random key kept in `relay.key` in the data folder (made on first
use, readable by the server only where the system allows); with no data folder (state kept in memory) a key that lives as long as the
process. Each stored entry carries the fingerprint of the key it was sealed with, so a changed key is reported as such."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets

NONCE = 16
TAG = 32
_FORBIDDEN_HEADERS = {"host", "content-length", "content-type", "transfer-encoding", "connection", "upgrade", "te", "trailer", "keep-alive",
                      "authorization", "proxy-authorization", "icy-metadata", "range", "if-range", "expect"}
_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,40}$")
_HOSTNAME = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?)(\.[A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?)*$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MAX_HEADERS = 10
MAX_HOSTS = 10


class VaultError(Exception):
    """The key is wrong, the blob was changed, or the key file is unusable."""


def _derive(master: bytes, label: bytes) -> bytes:
    return hmac.new(master, b"pluteus-vault|" + label, hashlib.sha256).digest()


class Vault:
    def __init__(self, master: bytes):
        if len(master) < 16:
            raise VaultError("the key is too short")
        self._enc, self._mac = _derive(master, b"encrypt"), _derive(master, b"authenticate")
        self.key_id = _derive(master, b"fingerprint").hex()[:12]            # not the key: only to tell one key from another

    def _keystream(self, nonce: bytes, length: int) -> bytes:
        out, counter = bytearray(), 0
        while len(out) < length:
            out += hmac.new(self._enc, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
            counter += 1
        return bytes(out[:length])

    def seal(self, data: dict) -> bytes:
        plain = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
        nonce = secrets.token_bytes(NONCE)
        cipher = bytes(a ^ b for a, b in zip(plain, self._keystream(nonce, len(plain))))
        return nonce + cipher + hmac.new(self._mac, nonce + cipher, hashlib.sha256).digest()

    def open(self, blob: bytes) -> dict:
        if len(blob) < NONCE + TAG:
            raise VaultError("the entry is damaged")
        nonce, cipher, tag = blob[:NONCE], blob[NONCE:-TAG], blob[-TAG:]
        if not hmac.compare_digest(tag, hmac.new(self._mac, nonce + cipher, hashlib.sha256).digest()):
            raise VaultError("the entry was stored with another key, or was changed")
        plain = bytes(a ^ b for a, b in zip(cipher, self._keystream(nonce, len(cipher))))
        try:
            return json.loads(plain.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise VaultError("the entry is damaged")


def load_master(configured: str, data_dir: str) -> tuple:
    """(master key bytes, where it came from: 'config', 'file' or 'memory'). The config wins; then relay.key in the data folder (made when
    missing); with no data folder a key for this run only. A key file that exists but cannot be used is an error, never replaced:
    replacing it would make every stored entry unreadable."""
    if configured:
        return hashlib.pbkdf2_hmac("sha256", configured.encode("utf-8"), b"pluteus-relay-key", 100_000), "config"
    if not data_dir:
        return secrets.token_bytes(32), "memory"
    path = os.path.join(data_dir, "relay.key")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="ascii") as f:
                text = f.read().strip()
            if not re.fullmatch(r"[0-9a-f]{64}", text):
                raise ValueError
            return bytes.fromhex(text), "file"
        except (OSError, ValueError):
            raise VaultError("%s exists but is not a usable key; move it away only if you accept that stored streams become unreadable" % path)
    os.makedirs(data_dir, exist_ok=True)
    key = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(key.hex() + "\n")
    return key, "file"


def clean_entry(auth_type: str, user: str, password: str, token: str, headers_text: str, hosts_text: str) -> dict:
    """The entry to store, from what the form sent, or ValueError with a sentence for the user. Nothing here is trusted."""
    entry: dict = {"auth": {"type": "none"}, "headers": {}, "hosts": []}
    if auth_type == "basic":
        if not user or _CONTROL.search(user) or ":" in user or len(user) > 200 or not password or _CONTROL.search(password) or len(password) > 200:
            raise ValueError("A login needs a user name (without a colon) and a password.")
        entry["auth"] = {"type": "basic", "user": user, "password": password}
    elif auth_type == "bearer":
        if not token or len(token) > 2000 or re.search(r"[\s\x00-\x1f\x7f]", token):
            raise ValueError("A token is one piece of text without spaces.")
        entry["auth"] = {"type": "bearer", "token": token}
    elif auth_type not in ("", "none"):
        raise ValueError("Unknown kind of login.")
    for line in (headers_text or "").replace("\r\n", "\n").split("\n"):
        if not line.strip():
            continue
        name, sep, value = line.partition(":")
        name, value = name.strip(), value.strip()
        if not sep or not _HEADER_NAME.match(name) or not value or len(value) > 500 or _CONTROL.search(value):
            raise ValueError("A header is written as Name: value (for example Referer: https://example.org/).")
        if name.lower() in _FORBIDDEN_HEADERS or name.lower().startswith("proxy-"):
            raise ValueError("The header %s cannot be set here." % name)
        entry["headers"][name] = value
    if len(entry["headers"]) > MAX_HEADERS:
        raise ValueError("At most %d headers." % MAX_HEADERS)
    hosts = [h.strip().lower().rstrip(".") for h in re.split(r"[,\s]+", hosts_text or "") if h.strip()]
    if not hosts or len(hosts) > MAX_HOSTS or not all(_HOSTNAME.match(h) for h in hosts):
        raise ValueError("Name the host names these may be sent to, for example stream.example.org (up to %d, no http:// and no path)." % MAX_HOSTS)
    entry["hosts"] = sorted(set(hosts))
    if entry["auth"]["type"] == "none" and not entry["headers"]:
        raise ValueError("Give a login, a token or at least one header.")
    return entry


def clean_name(name: str) -> str:
    name = (name or "").strip()
    if not 1 <= len(name) <= 60 or _CONTROL.search(name) or '"' in name:
        raise ValueError("The name is 1 to 60 characters, without quotation marks.")
    return name


def request_headers(entry: dict, host: str) -> dict:
    """The headers to send to `host` for this entry: the login or token and the headers, but only when the host is one the entry names
    (an exact match, case-insensitive). For any other host, for example after a redirect, nothing."""
    if host.lower().rstrip(".") not in entry.get("hosts", ()):
        return {}
    out = dict(entry.get("headers", {}))
    auth = entry.get("auth", {})
    if auth.get("type") == "basic":
        import base64
        out["Authorization"] = "Basic " + base64.b64encode(("%s:%s" % (auth["user"], auth["password"])).encode("utf-8")).decode("ascii")
    elif auth.get("type") == "bearer":
        out["Authorization"] = "Bearer " + auth["token"]
    return out


def describe(entry: dict) -> dict:
    """What may be shown about an entry: the kind of login, the header names and the hosts, never a value."""
    return {"login": entry.get("auth", {}).get("type", "none"), "headers": sorted(entry.get("headers", {})), "hosts": list(entry.get("hosts", []))}

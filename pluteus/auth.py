"""HTTP Basic authentication with scrypt password hashes (standard library only).

OPDS reader apps send Basic credentials on every request, so a successful
check is cached in memory for a few minutes, keyed by a hash of the header
(the raw credentials are never kept).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

DEFAULT_N, DEFAULT_R, DEFAULT_P = 2 ** 14, 8, 1


def hash_password(password: str, *, n: int = DEFAULT_N, r: int = DEFAULT_R, p: int = DEFAULT_P) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=2 ** 26)
    return "scrypt$%d$%d$%d$%s$%s" % (n, r, p, salt.hex(), dk.hex())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r),
                            p=int(p), dklen=len(expected) // 2, maxmem=2 ** 26)
        return hmac.compare_digest(dk.hex(), expected)
    except (ValueError, TypeError):
        return False


class Authenticator:
    def __init__(self, users: dict, cache_seconds: int = 300):
        self.users = users
        self.cache_seconds = cache_seconds
        self._cache: dict = {}
        # Burned on unknown user names so they cost the same time as a wrong password.
        self._dummy = hash_password("dummy")

    def set_users(self, users: dict) -> None:
        """Swap the user table (config reload). Cached logins are dropped so removed or changed users stop at once."""
        self.users = users
        self._cache = {}

    def authenticate(self, header):
        """Return the User for an Authorization header value, or None."""
        if not header or header[:6].lower() != "basic ":
            return None
        token = header[6:].strip()
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and hit[1] > now:
            return self.users.get(hit[0])
        try:
            raw = base64.b64decode(token, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
        name, sep, password = raw.partition(":")
        if not sep:
            return None
        user = self.users.get(name)
        if user is None:
            verify_password(password, self._dummy)
            return None
        if not verify_password(password, user.password_hash):
            return None
        if len(self._cache) > 1000:
            self._cache = {k: v for k, v in self._cache.items() if v[1] > now}
        self._cache[key] = (name, now + self.cache_seconds)
        return user

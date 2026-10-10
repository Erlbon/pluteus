"""Browser login sessions: a random token in an HttpOnly cookie, only its hash stored server-side.

A session dies when it expires, when its user is removed, or when the user's password changes (each
session remembers a fingerprint of the password hash it was created with).
"""
from __future__ import annotations

import hashlib
import secrets
import time

from .config import User
from .state import StateDB

COOKIE = "pluteus_session"
_TOUCH_EVERY = 3600          # slide the expiry at most once an hour


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def password_fingerprint(user: User) -> str:
    return hashlib.sha256(user.password_hash.encode("utf-8")).hexdigest()[:16]


class SessionManager:
    def __init__(self, state: StateDB, days: int = 30):
        self.state = state
        self.ttl = days * 86400

    def start(self, user: User) -> str:
        token = new_token()
        self.state.create_session(hash_token(token), user.name, password_fingerprint(user), self.ttl)
        return token

    def user_for(self, token: str | None, users: dict):
        """The User a cookie token belongs to, or None (unknown, expired, user gone, password changed)."""
        if not token:
            return None
        key = hash_token(token)
        row = self.state.get_session(key)
        if row is None:
            return None
        user = users.get(row["username"])
        if user is None or password_fingerprint(user) != row["pwfp"]:
            self.state.delete_session(key)
            return None
        if time.time() - row["last_seen"] > _TOUCH_EVERY:
            self.state.touch_session(key, self.ttl)
        return user

    def end(self, token: str | None) -> None:
        if token:
            self.state.delete_session(hash_token(token))

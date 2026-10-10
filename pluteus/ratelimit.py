"""Failed-login throttling, per user name and per client address (in memory).

A name or address that fails too often inside the window is refused (429 + Retry-After) until the oldest
failure ages out. The per-name limit stops guessing against one account even from many addresses; the
per-address limit is looser so one wrong guess on a shared address does not lock a household out.
"""
from __future__ import annotations

import threading
import time


class FailureLimiter:
    def __init__(self, max_per_user: int = 5, max_per_ip: int = 30, window: float = 300.0):
        self.max = {"user": max_per_user, "ip": max_per_ip}
        self.window = window
        self._fails: dict = {}
        self._lock = threading.Lock()

    def _recent(self, key, now):
        recent = [t for t in self._fails.get(key, ()) if now - t < self.window]
        if recent:
            self._fails[key] = recent
        else:
            self._fails.pop(key, None)
        return recent

    def wait_seconds(self, username: str, ip: str, now=None) -> int:
        """Seconds the caller must wait (0 = allowed)."""
        now = time.time() if now is None else now
        wait = 0.0
        with self._lock:
            for kind, name in (("user", (username or "").lower()), ("ip", ip)):
                recent = self._recent((kind, name), now)
                if len(recent) >= self.max[kind]:
                    wait = max(wait, recent[0] + self.window - now)
        return int(wait) + 1 if wait > 0 else 0

    def failure(self, username: str, ip: str, now=None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            for kind, name in (("user", (username or "").lower()), ("ip", ip)):
                self._fails.setdefault((kind, name), []).append(now)
            if len(self._fails) > 5000:
                for key in list(self._fails):
                    self._recent(key, now)

    def success(self, username: str) -> None:
        with self._lock:
            self._fails.pop(("user", (username or "").lower()), None)

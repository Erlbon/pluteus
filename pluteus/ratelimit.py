"""Failed-login throttling (in memory): per user name, per client address and per known device.

Two kinds of callers are told apart. A KNOWN DEVICE is a browser that has logged in as this user before and still carries
the device cookie from that login (see sessions.py): its mistakes count only against that device (5 per window), never
against the account, and a locked account does not stop it. Everything else is a STRANGER (a new phone, a guesser, a
reader app that does not keep cookies).

Strangers are limited per NAME: when a name collects too many wrong passwords inside the window it is locked (429 +
Retry-After) for a while. Each further lock of the same name is longer, 6 times as long each time, up to MAX_LOCK
(5 minutes, 30 minutes, 3 hours, 6 hours, 6 hours, ...). A name left alone for a day, or that has a successful login,
starts again from the short lock. Without this a patient attacker gets the same few guesses every five minutes for ever.
There is also a looser per-address limit (so one wrong guess on a shared address does not lock a household out).

What a lock costs the owner: new logins from NEW devices wait, but a browser with its device cookie, an open session, and
a reader app whose credentials were verified before keep working. A restart of pluteus clears every lock.
"""
from __future__ import annotations

import threading
import time

ESCALATION = 6                 # each further lock of the same name is this many times longer
MAX_LOCK = 6 * 3600.0          # ... but never longer than this
FORGET_AFTER = 24 * 3600.0     # a name left alone this long goes back to the shortest lock


class FailureLimiter:
    def __init__(self, max_per_user: int = 5, max_per_ip: int = 30, window: float = 300.0, max_per_device: int = 5):
        self.max = {"user": max_per_user, "ip": max_per_ip, "dev": max_per_device}
        self.window = window
        self._fails: dict = {}
        self._locked: dict = {}            # name -> time the current lock ends
        self._strikes: dict = {}           # name -> [number of locks so far, time the last lock ended]
        self._lock = threading.Lock()

    def _recent(self, key, now):
        recent = [t for t in self._fails.get(key, ()) if now - t < self.window]
        if recent:
            self._fails[key] = recent
        else:
            self._fails.pop(key, None)
        return recent

    def _bucket_wait(self, kind: str, name: str, now: float) -> float:
        recent = self._recent((kind, name), now)
        return recent[0] + self.window - now if len(recent) >= self.max[kind] else 0.0

    def wait_seconds(self, username: str, ip: str, now=None, device: str = "", known: bool = False) -> int:
        """Seconds the caller must wait (0 = allowed). `known`: `device` is a device this user has logged in from."""
        now = time.time() if now is None else now
        wait = 0.0
        with self._lock:
            if known and device:
                wait = self._bucket_wait("dev", device, now)       # the account lock does not apply to its own devices
            else:
                name = (username or "").lower()
                until = self._locked.get(name, 0)
                if until > now:
                    wait = until - now
                elif name in self._locked:
                    del self._locked[name]
                if ip:
                    wait = max(wait, self._bucket_wait("ip", ip, now))
        return int(wait) + 1 if wait > 0 else 0

    def failure(self, username: str, ip: str, now=None, device: str = "", known: bool = False) -> tuple:
        """Count one wrong password. Returns (seconds, lock number) when this started a NEW lock on the name, else (0, 0)."""
        now = time.time() if now is None else now
        with self._lock:
            if known and device:
                self._fails.setdefault(("dev", device), []).append(now)    # a known device's typo is its own problem
                return 0, 0
            name = (username or "").lower()
            if self._locked.get(name, 0) > now:
                return 0, 0                                # already locked: more guesses do not stack up lock numbers
            self._fails.setdefault(("user", name), []).append(now)
            if ip:
                self._fails.setdefault(("ip", ip), []).append(now)
            started = (0, 0)
            recent = self._recent(("user", name), now)
            if len(recent) >= self.max["user"]:
                count, last_end = self._strikes.get(name, [0, 0.0])
                if count and now - last_end > FORGET_AFTER:
                    count = 0                              # left alone for a day: forgiven
                count += 1
                length = min(self.window * ESCALATION ** (count - 1), MAX_LOCK)
                # The first lock ends when the oldest failure ages out (the plain behaviour); later ones run their full length.
                until = max(recent[0] + length, now) if count == 1 else now + length
                self._locked[name] = until
                self._strikes[name] = [count, until]
                self._fails.pop(("user", name), None)      # a fresh round of attempts once the lock is over
                started = (int(until - now), count)
            if len(self._fails) > 5000:
                for key in list(self._fails):
                    self._recent(key, now)
        return started

    def snapshot(self, now=None) -> list:
        """What the admin page shows: [{'name', 'locked_for' (seconds, 0 = not locked), 'locks' (how many so far),
        'recent_failures' (wrong passwords inside the window)}] for every name that has any of those. Read-only."""
        now = time.time() if now is None else now
        rows = {}
        with self._lock:
            for name, until in list(self._locked.items()):
                if until > now:
                    rows.setdefault(name, {})["locked_for"] = int(until - now) + 1
            for name, (count, _end) in self._strikes.items():
                rows.setdefault(name, {})["locks"] = count
            for (kind, key) in list(self._fails):
                if kind == "user":
                    recent = self._recent((kind, key), now)
                    if recent:
                        rows.setdefault(key, {})["recent_failures"] = len(recent)
        return sorted(({"name": n, "locked_for": r.get("locked_for", 0), "locks": r.get("locks", 0),
                        "recent_failures": r.get("recent_failures", 0)} for n, r in rows.items()),
                      key=lambda r: (-r["locked_for"], r["name"]))

    def unlock(self, username: str) -> bool:
        """Clear the lock, the lock count and the recent failures of one name. True when there was anything to clear."""
        with self._lock:
            name = (username or "").lower()
            found = name in self._locked or name in self._strikes or ("user", name) in self._fails
            self._locked.pop(name, None)
            self._strikes.pop(name, None)
            self._fails.pop(("user", name), None)
        return found

    def success(self, username: str, device: str = "") -> None:
        with self._lock:
            name = (username or "").lower()
            self._fails.pop(("user", name), None)
            self._locked.pop(name, None)
            self._strikes.pop(name, None)
            if device:
                self._fails.pop(("dev", device), None)

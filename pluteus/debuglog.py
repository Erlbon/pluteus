"""Debug logging: off until the administrator switches it on (admin page, Debug), and it switches itself off again after a few minutes.

While it is on, every request to the site leaves one line: when, method, path (the names of the query parameters, never their values),
status, client address, what the proxy said about the client, the browser or app (User-Agent), the kind of login sent and what could be
said about it (lengths, whether the name matches a user, ...). Never recorded: passwords, keys, cookie values, query values, bodies. The
lines go to the normal log and to a short list in memory that the admin page shows and can download. Nothing is written to disk by this
module, and a restart forgets everything.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

log = logging.getLogger("pluteus")

DEFAULT_MINUTES = 15
MAX_MINUTES = 60
KEEP = 400                      # lines kept in memory


class DebugLog:
    def __init__(self, clock=time.monotonic, always: bool = False):
        self.always = always                    # debug = true in the config: on for as long as the server runs, no timer, no switch
        self._clock = clock
        self._until = 0.0
        self._lines: deque = deque(maxlen=KEEP)
        self._lock = threading.Lock()

    def active(self) -> bool:
        return self.always or self._until > self._clock()

    def remaining(self) -> int:
        """Seconds until it switches itself off (0 when it is off; 0 as well when the config keeps it on: see `always`)."""
        return 0 if self.always else max(0, int(self._until - self._clock()))

    def enable(self, minutes: int = DEFAULT_MINUTES) -> int:
        if self.always:
            return 0
        minutes = max(1, min(int(minutes), MAX_MINUTES))
        with self._lock:
            self._until = self._clock() + minutes * 60
        log.warning("debug logging is ON for %d minutes (switched on from the admin page)", minutes)
        return minutes

    def disable(self) -> None:
        if self.always:
            return
        with self._lock:
            was = self.active()
            self._until = 0.0
        if was:
            log.warning("debug logging is OFF")

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()

    def record(self, entry: dict) -> None:
        """Keep and log one line (only while it is on)."""
        if not self.active():
            return
        line = format_entry(entry)
        with self._lock:
            self._lines.append((time.strftime("%H:%M:%S"), line))
        log.info("debug %s", line)

    def lines(self) -> list:
        """[(time, text)], newest first."""
        with self._lock:
            return list(reversed(self._lines))


def format_entry(entry: dict) -> str:
    """One readable line from the fields of a request (see app.AccessLog)."""
    parts = ["%s %s -> %s" % (entry.get("method", "-"), entry.get("target", "-"), entry.get("status", "-")),
             "from %s" % (entry.get("client") or "-")]
    for label, key in (("took", "took"), ("answer", "answer"), ("http", "http"), ("via", "forwarded"), ("agent", "agent"), ("accept", "accept"), ("login", "login"), ("cookies", "cookies"), ("other headers", "headers"), ("note", "note")):
        if entry.get(key):
            parts.append("%s: %s" % (label, entry[key]))
    return "; ".join(parts)

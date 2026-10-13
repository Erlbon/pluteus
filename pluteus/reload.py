"""Picks up user changes made to config.toml (for example by `pluteus user ...`) while the server runs.

Only users are reloaded: roots, port and the other settings still need a restart. A changed file that
no longer parses is ignored (with a warning) and the previous users stay in force.
"""
from __future__ import annotations

import logging
import os
import time

from .auth import Authenticator
from .config import ConfigError, load_config

log = logging.getLogger("pluteus.config")


class UsersReloader:
    def __init__(self, path: str, auth: Authenticator, interval: float = 2.0, on_config=None):
        self.path = path
        self.auth = auth
        self.on_config = on_config            # called with the new Config after every valid reload (libraries follow the file)
        self.interval = interval
        self._stamp = self._current()
        self._next = 0.0

    def _current(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def maybe_reload(self) -> bool:
        """Cheap: one stat() at most every `interval` seconds. True when the users were replaced."""
        now = time.monotonic()
        if now < self._next:
            return False
        self._next = now + self.interval
        stamp = self._current()
        if stamp is None or stamp == self._stamp:
            return False
        self._stamp = stamp
        try:
            cfg = load_config(self.path, check_roots=False)
        except ConfigError as e:
            log.warning("config.toml changed but is not valid, keeping the previous users: %s", e)
            return False
        self.auth.set_users(cfg.users)
        if self.on_config:
            self.on_config(cfg)
        log.info("config.toml changed: %d user(s) loaded", len(cfg.users))
        return True

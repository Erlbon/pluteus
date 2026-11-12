"""Picks up user changes made to config.toml (for example by `pluteus user ...`) while the server runs.

Only users are reloaded: roots, port and the other settings still need a restart. A changed file that
no longer parses is ignored (with a warning) and the previous users stay in force.

maybe_reload() is the ONE entry point: every path that decides who a request is (the session and Basic check, the login
form, Subsonic, KOReader sync, the admin page) calls it first, so a removed user or a changed password is in force
everywhere at once, not only after the next ordinary page request. It is cheap (one stat() at most every `interval`
seconds) and safe to call from any thread.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .auth import Authenticator
from .config import ConfigError, load_config

log = logging.getLogger("pluteus.config")


class UsersReloader:
    def __init__(self, path: str, auth: Authenticator, interval: float = 2.0, on_config=None, on_removed=None):
        self.path = path
        self.auth = auth
        self.on_config = on_config            # called with the new Config after every valid reload (libraries follow the file)
        self.on_removed = on_removed          # called with each user name that was in the file and no longer is (state.purge_user)
        self.interval = interval
        self._stamp = self._current()
        self._next = 0.0
        self._names = set(auth.users)         # the names in the file as last read here: NOT auth.users, which the admin page updates itself
        self._lock = threading.Lock()

    def _current(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def maybe_reload(self) -> bool:
        """Cheap: one stat() at most every `interval` seconds. True when the users were replaced."""
        if time.monotonic() < self._next:
            return False
        with self._lock:                      # two requests at once: one reloads, the other finds it done
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
            gone = sorted(self._names - set(cfg.users))
            self._names = set(cfg.users)
            self.auth.set_users(cfg.users)
            if self.on_config:
                self.on_config(cfg)
            for name in gone:                 # a new user given the same name later must not inherit anything
                log.info("config.toml changed: user %r is gone", name[:40])
                if self.on_removed:
                    try:
                        self.on_removed(name)
                    except Exception:         # the reload itself has succeeded; never fail a request over the clean-up
                        log.exception("clean-up after removed user %r failed", name[:40])
            log.info("config.toml changed: %d user(s) loaded", len(cfg.users))
            return True

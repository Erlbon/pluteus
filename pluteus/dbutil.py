"""Shared SQLite opening: WAL mode, schema, and 'set the damaged file aside and start again'.

A damaged file is never deleted: it is renamed to <name>.corrupt-<time> (with its -wal/-shm files) so
anything in it can still be recovered by hand, then a fresh database is created.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time


def open_database(path: str, schema: str, *, quick_check: bool = False, log=None) -> sqlite3.Connection:
    log = log or logging.getLogger("pluteus.db")

    def connect() -> sqlite3.Connection:
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        try:
            if path != ":memory:":
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=NORMAL")
            db.executescript(schema)
            if quick_check:
                row = db.execute("PRAGMA quick_check").fetchone()
                if not row or row[0] != "ok":
                    raise sqlite3.DatabaseError("quick_check failed: %r" % (row,))
        except sqlite3.Error:
            db.close()          # Windows cannot move a file that is still open
            raise
        return db

    try:
        return connect()
    except sqlite3.DatabaseError as e:
        if path == ":memory:":
            raise
        log.error("database %s is damaged (%s); setting it aside and starting a new one", path, e)
        stamp = int(time.time())
        for suffix in ("", "-wal", "-shm"):
            try:
                os.replace(path + suffix, "%s.corrupt-%d%s" % (path, stamp, suffix))
            except OSError:
                pass
        return connect()

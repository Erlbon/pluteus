"""Shared SQLite opening: WAL mode, schema, and 'set the damaged file aside and start again'.

A damaged file is never deleted: it is renamed to <name>.corrupt-<time> (with its -wal/-shm files) so
anything in it can still be recovered by hand, then a fresh database is created.

Only real damage is treated that way: a failed quick_check, or SQLite saying the file is malformed or not a database.
Anything else that stops the file from opening (it is locked by another pluteus, the disk is full, a share that hiccups)
leaves the file where it is and raises DatabaseUnusable, so the start stops with a message instead of silently beginning
with empty sessions and reading progress.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
import weakref

_SQLITE_CORRUPT, _SQLITE_NOTADB = 11, 26


class DatabaseUnusable(sqlite3.OperationalError):
    """The database file could not be opened, and not because it is damaged. The file was left where it is."""


class _Connection(sqlite3.Connection):
    """A connection that can be found again (weakly) by the folder its file is in: see close_under()."""
    __slots__ = ("__weakref__", "pluteus_path")


_open = weakref.WeakSet()


class _QuickCheckFailed(sqlite3.DatabaseError):
    pass


def _is_damage(error: sqlite3.Error) -> bool:
    if isinstance(error, _QuickCheckFailed):
        return True
    code = getattr(error, "sqlite_errorcode", None)
    if isinstance(code, int) and code & 0xFF in (_SQLITE_CORRUPT, _SQLITE_NOTADB):
        return True
    text = str(error).lower()
    return "malformed" in text or "not a database" in text


def open_database(path: str, schema: str, *, quick_check: bool = False, log=None) -> sqlite3.Connection:
    log = log or logging.getLogger("pluteus.db")

    def connect() -> sqlite3.Connection:
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        db = sqlite3.connect(path, check_same_thread=False, timeout=10, factory=_Connection)
        try:
            if path != ":memory:":
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=NORMAL")
            db.executescript(schema)
            if quick_check:
                row = db.execute("PRAGMA quick_check").fetchone()
                if not row or row[0] != "ok":
                    raise _QuickCheckFailed("quick_check failed: %r" % (row,))
        except sqlite3.Error:
            db.close()          # Windows cannot move a file that is still open
            raise
        if path != ":memory:":
            try:
                db.pluteus_path = os.path.abspath(path)
                _open.add(db)
            except (AttributeError, TypeError):     # not our connection class (a test double): it is simply not tracked
                pass
        return db

    try:
        return connect()
    except sqlite3.Error as e:
        if path == ":memory:":
            raise
        if not _is_damage(e):
            message = ("the database %s cannot be opened: %s. The file was left where it is (it is not damaged). Check that no "
                       "other pluteus is running on the same data folder, that the disk is not full and that the folder can "
                       "be written, then start again" % (path, e))
            log.error("%s", message)
            raise DatabaseUnusable(message) from e
        log.error("database %s is damaged (%s); setting it aside and starting a new one", path, e)
        stamp = int(time.time())
        for suffix in ("", "-wal", "-shm"):
            try:
                os.replace(path + suffix, "%s.corrupt-%d%s" % (path, stamp, suffix))
            except OSError:
                pass
        return connect()


def close_under(folder: str) -> int:
    """Close every database this process opened inside `folder` (quick share: its temp data folder cannot be removed on
    Windows while a database in it is open). Returns how many were closed. Only for a server that has stopped."""
    base = os.path.normcase(os.path.abspath(folder)).rstrip(os.sep) + os.sep
    closed = 0
    for db in list(_open):
        if os.path.normcase(getattr(db, "pluteus_path", "")).startswith(base):
            try:
                db.close()
                closed += 1
            except sqlite3.Error:
                pass
            _open.discard(db)
    return closed

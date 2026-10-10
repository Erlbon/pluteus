"""Persistent state: login sessions, reading progress and per-user preferences (SQLite, WAL).

Unlike the metadata cache this is NOT disposable: reading progress matters. A damaged file is set
aside (never deleted) and a new one started, and backup() makes dated copies with SQLite's online
backup API (safe while the server runs). With no data_dir the state lives in memory and is lost on restart.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time

from .dbutil import open_database

log = logging.getLogger("pluteus.state")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, pwfp TEXT NOT NULL,
    created REAL NOT NULL, last_seen REAL NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS progress (
    username TEXT NOT NULL, root TEXT NOT NULL, path TEXT NOT NULL,
    locator TEXT NOT NULL, percent REAL NOT NULL, updated REAL NOT NULL,
    PRIMARY KEY (username, root, path));
CREATE INDEX IF NOT EXISTS progress_recent ON progress (username, updated DESC);
CREATE TABLE IF NOT EXISTS prefs (username TEXT PRIMARY KEY, data TEXT NOT NULL);
"""


class StateDB:
    def __init__(self, data_dir: str = ""):
        self.persistent = bool(data_dir)
        self.path = os.path.join(data_dir, "state.sqlite3") if data_dir else ":memory:"
        self._lock = threading.Lock()
        self._db = open_database(self.path, SCHEMA, quick_check=True, log=log)
        if not self.persistent:
            log.warning("no data_dir configured: sessions and reading progress are kept in memory "
                        "and will be lost when the server restarts")

    def _one(self, sql, args=()):
        with self._lock:
            return self._db.execute(sql, args).fetchone()

    def _all(self, sql, args=()):
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def _write(self, sql, args=()):
        with self._lock, self._db:
            return self._db.execute(sql, args).rowcount

    # ---- sessions ---------------------------------------------------------------
    def create_session(self, token_hash: str, username: str, pwfp: str, ttl: float, now=None) -> None:
        now = time.time() if now is None else now
        self._write("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?)",
                    (token_hash, username, pwfp, now, now, now + ttl))

    def get_session(self, token_hash: str, now=None):
        """{'username','pwfp','last_seen','expires'} or None; an expired session is deleted."""
        now = time.time() if now is None else now
        row = self._one("SELECT username, pwfp, last_seen, expires FROM sessions WHERE token_hash=?",
                        (token_hash,))
        if row is None:
            return None
        if row[3] <= now:
            self.delete_session(token_hash)
            return None
        return {"username": row[0], "pwfp": row[1], "last_seen": row[2], "expires": row[3]}

    def touch_session(self, token_hash: str, ttl: float, now=None) -> None:
        now = time.time() if now is None else now
        self._write("UPDATE sessions SET last_seen=?, expires=? WHERE token_hash=?", (now, now + ttl, token_hash))

    def delete_session(self, token_hash: str) -> None:
        self._write("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def delete_user_sessions(self, username: str) -> int:
        return self._write("DELETE FROM sessions WHERE username=?", (username,))

    def purge_expired(self, now=None) -> int:
        now = time.time() if now is None else now
        return self._write("DELETE FROM sessions WHERE expires<=?", (now,))

    # ---- reading progress -------------------------------------------------------
    def get_progress(self, username: str, root: str, path: str):
        row = self._one("SELECT locator, percent, updated FROM progress WHERE username=? AND root=? AND path=?",
                        (username, root, path))
        return {"locator": row[0], "percent": row[1], "updated": row[2]} if row else None

    def put_progress(self, username: str, root: str, path: str, locator: str, percent: float, now=None) -> None:
        now = time.time() if now is None else now
        self._write("INSERT OR REPLACE INTO progress VALUES (?,?,?,?,?,?)",
                    (username, root, path, locator, min(1.0, max(0.0, float(percent))), now))

    def recent_progress(self, username: str, limit: int = 20) -> list:
        rows = self._all("SELECT root, path, locator, percent, updated FROM progress WHERE username=? "
                         "ORDER BY updated DESC LIMIT ?", (username, limit))
        return [{"root": r[0], "path": r[1], "locator": r[2], "percent": r[3], "updated": r[4]} for r in rows]

    def progress_for(self, username: str, root: str, paths: list) -> dict:
        """{path: percent} for the given paths (used for the badges in a folder listing)."""
        out: dict = {}
        for i in range(0, len(paths), 400):
            chunk = paths[i:i + 400]
            rows = self._all("SELECT path, percent FROM progress WHERE username=? AND root=? AND path IN (%s)"
                             % ",".join("?" * len(chunk)), (username, root, *chunk))
            out.update({p: pct for p, pct in rows})
        return out

    # ---- preferences ------------------------------------------------------------
    def get_prefs(self, username: str) -> dict:
        row = self._one("SELECT data FROM prefs WHERE username=?", (username,))
        try:
            return json.loads(row[0]) if row else {}
        except ValueError:
            return {}

    def set_prefs(self, username: str, data: dict) -> None:
        self._write("INSERT OR REPLACE INTO prefs VALUES (?,?)", (username, json.dumps(data, ensure_ascii=False)))

    # ---- backups ----------------------------------------------------------------
    def backup(self, directory: str, keep: int = 7, now=None):
        """Dated copy of the state file made with SQLite's online backup API; older ones pruned."""
        if not self.persistent:
            return None
        os.makedirs(directory, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() if now is None else now))
        target = os.path.join(directory, "state-%s.sqlite3" % stamp)
        dst = sqlite3.connect(target)
        try:
            with self._lock:
                self._db.backup(dst)
        finally:
            dst.close()
        old = sorted(n for n in os.listdir(directory) if n.startswith("state-") and n.endswith(".sqlite3"))
        for name in old[:-keep] if keep > 0 else old:
            try:
                os.remove(os.path.join(directory, name))
            except OSError:
                pass
        return target

    def housekeeping(self, backup_dir: str, now=None, every: float = 20 * 3600) -> None:
        """Purge expired sessions and, when the newest backup is older than `every` seconds, make a new one."""
        now = time.time() if now is None else now
        self.purge_expired(now)
        if not self.persistent:
            return
        try:
            names = sorted(n for n in os.listdir(backup_dir) if n.startswith("state-")) if os.path.isdir(backup_dir) else []
            newest = os.path.getmtime(os.path.join(backup_dir, names[-1])) if names else 0
        except OSError:
            newest = 0
        if now - newest > every:
            try:
                self.backup(backup_dir, now=now)
            except (OSError, sqlite3.Error) as e:
                log.warning("state backup failed: %s", e)

    def close(self) -> None:
        with self._lock:
            self._db.close()

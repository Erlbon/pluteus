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
CREATE TABLE IF NOT EXISTS playqueue (username TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS dismissed (
    username TEXT NOT NULL, root TEXT NOT NULL, path TEXT NOT NULL, at REAL NOT NULL,
    PRIMARY KEY (username, root, path));
CREATE TABLE IF NOT EXISTS devices (
    token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, pwfp TEXT NOT NULL,
    first_seen REAL NOT NULL, last_seen REAL NOT NULL, last_ip TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS appkeys (username TEXT PRIMARY KEY, secret TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS streams (username TEXT NOT NULL, name TEXT NOT NULL, blob BLOB NOT NULL, key_id TEXT NOT NULL, created REAL NOT NULL,
    PRIMARY KEY (username, name));
CREATE TABLE IF NOT EXISTS shelves (
    username TEXT NOT NULL, shelf TEXT NOT NULL, root TEXT NOT NULL, path TEXT NOT NULL, is_dir INTEGER NOT NULL,
    rank REAL NOT NULL, added REAL NOT NULL, PRIMARY KEY (username, shelf, root, path));
CREATE TABLE IF NOT EXISTS bookmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, root TEXT NOT NULL, path TEXT NOT NULL,
    position REAL NOT NULL, note TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS bookmarks_file ON bookmarks (username, root, path);
CREATE TABLE IF NOT EXISTS kosync (
    username TEXT NOT NULL, document TEXT NOT NULL, progress TEXT NOT NULL, percentage REAL NOT NULL,
    device TEXT NOT NULL, device_id TEXT NOT NULL, timestamp REAL NOT NULL, PRIMARY KEY (username, document));
"""

MIN_SONG_SECONDS = 20           # a song heard for less than this was passed on the way (skipped), not one to pick up again
_SONG_TYPES = (".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wav", ".wma")      # an audiobook (.m4b) keeps its place however short


def passing_by(path: str, locator: str) -> bool:
    """True for a saved position that only records a skipped song: a song with fewer than MIN_SONG_SECONDS played. Such a row stays out of
    "pick up where you left off" (the home page and the Subsonic resume), so skipping through an album does not fill the list with its songs."""
    if os.path.splitext(path)[1].lower() not in _SONG_TYPES or not (locator or "").startswith("t:"):
        return False
    try:
        return float(locator[2:]) < MIN_SONG_SECONDS
    except ValueError:
        return False


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

    # ---- known devices: browsers that have logged in successfully before ----------------------------------------
    def remember_device(self, token_hash: str, username: str, pwfp: str, ip: str, now=None) -> None:
        now = time.time() if now is None else now
        with self._lock, self._db:
            row = self._db.execute("SELECT first_seen FROM devices WHERE token_hash=?", (token_hash,)).fetchone()
            self._db.execute("INSERT OR REPLACE INTO devices VALUES (?,?,?,?,?,?)",
                             (token_hash, username, pwfp, row[0] if row else now, now, ip or ""))

    def get_device(self, token_hash: str):
        """{'username','pwfp','last_seen','last_ip'} or None."""
        row = self._one("SELECT username, pwfp, last_seen, last_ip FROM devices WHERE token_hash=?", (token_hash,))
        return None if row is None else {"username": row[0], "pwfp": row[1], "last_seen": row[2], "last_ip": row[3]}

    def list_devices(self) -> list:
        """Every known device (browser that has logged in), newest first: no secrets, only the hash used as its id."""
        rows = self._all("SELECT token_hash, username, first_seen, last_seen, last_ip FROM devices ORDER BY last_seen DESC")
        return [{"id": r[0], "username": r[1], "first_seen": r[2], "last_seen": r[3], "last_ip": r[4]} for r in rows]

    def delete_device(self, token_hash: str) -> int:
        return self._write("DELETE FROM devices WHERE token_hash=?", (token_hash,))

    def session_counts(self, now=None) -> dict:
        """{user name: number of login sessions that have not expired}."""
        now = time.time() if now is None else now
        return {r[0]: r[1] for r in self._all("SELECT username, COUNT(*) FROM sessions WHERE expires>? GROUP BY username", (now,))}

    def delete_user_devices(self, username: str) -> int:
        return self._write("DELETE FROM devices WHERE username=?", (username,))

    def purge_old_devices(self, older_than: float = 400 * 86400, now=None) -> int:
        now = time.time() if now is None else now
        return self._write("DELETE FROM devices WHERE last_seen<?", (now - older_than,))

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
        """The files this user has a saved position in, newest first, leaving out the ones they removed from the home
        page list (see dismiss_progress) unless they have been opened again since."""
        rows = self._all("SELECT p.root, p.path, p.locator, p.percent, p.updated FROM progress p "
                         "LEFT JOIN dismissed d ON d.username=p.username AND d.root=p.root AND d.path=p.path "
                         "WHERE p.username=? AND (d.at IS NULL OR p.updated > d.at) "
                         "ORDER BY p.updated DESC LIMIT ?", (username, limit))
        return [{"root": r[0], "path": r[1], "locator": r[2], "percent": r[3], "updated": r[4]} for r in rows]

    # ---- the play queue a music app saved (Subsonic savePlayQueue): ONE row per user, replaced each time, so another device can resume -------
    def playqueue_put(self, username: str, data: dict, now=None) -> None:
        self._write("INSERT OR REPLACE INTO playqueue VALUES (?,?,?)",
                    (username, json.dumps(data, separators=(",", ":")), time.time() if now is None else now))

    def playqueue_get(self, username: str):
        row = self._one("SELECT data, updated FROM playqueue WHERE username=?", (username,))
        if not row:
            return None
        try:
            return {**json.loads(row[0]), "updated": row[1]}
        except ValueError:
            return None

    def playqueue_delete(self, username: str) -> int:
        return self._write("DELETE FROM playqueue WHERE username=?", (username,))

    def dismiss_progress(self, username: str, root: str, path: str, now=None) -> None:
        """Take a file off the home page list. Its saved position is kept; it comes back when it is opened again."""
        now = time.time() if now is None else now
        self._write("INSERT OR REPLACE INTO dismissed VALUES (?,?,?,?)", (username, root, path, now))

    def progress_for(self, username: str, root: str, paths: list) -> dict:
        """{path: percent} for the given paths (used for the badges in a folder listing)."""
        out: dict = {}
        for i in range(0, len(paths), 400):
            chunk = paths[i:i + 400]
            rows = self._all("SELECT path, percent FROM progress WHERE username=? AND root=? AND path IN (%s)"
                             % ",".join("?" * len(chunk)), (username, root, *chunk))
            out.update({p: pct for p, pct in rows})
        return out

    def progress_rows(self, username: str, root: str, paths: list) -> dict:
        """{path: {'locator', 'updated'}} for the given paths (used for OPDS-PSE lastRead)."""
        out: dict = {}
        for i in range(0, len(paths), 400):
            chunk = paths[i:i + 400]
            rows = self._all("SELECT path, locator, updated FROM progress WHERE username=? AND root=? AND path IN (%s)"
                             % ",".join("?" * len(chunk)), (username, root, *chunk))
            out.update({p: {"locator": loc, "updated": upd} for p, loc, upd in rows})
        return out

    # ---- app keys: the one secret a music app uses for the Subsonic API (read-only, revocable, not the password) ------------
    def get_appkey(self, username: str):
        row = self._one("SELECT secret FROM appkeys WHERE username=?", (username,))
        return row[0] if row else None

    def set_appkey(self, username: str, secret: str, now=None) -> None:
        self._write("INSERT OR REPLACE INTO appkeys VALUES (?,?,?)", (username, secret, time.time() if now is None else now))

    def delete_appkey(self, username: str) -> int:
        return self._write("DELETE FROM appkeys WHERE username=?", (username,))

    def appkeys(self) -> dict:
        """{user name: secret}: the Subsonic login keys (few: one per user)."""
        return {r[0]: r[1] for r in self._all("SELECT username, secret FROM appkeys")}

    # ---- stored streams: encrypted logins and headers for protected streams (see vault.py); the state never sees a clear value --------------
    def stream_put(self, username: str, name: str, blob: bytes, key_id: str, now=None) -> None:
        self._write("INSERT OR REPLACE INTO streams VALUES (?,?,?,?,?)", (username, name, blob, key_id, time.time() if now is None else now))

    def stream_get(self, username: str, name: str):
        """(blob, key_id) of this user's entry, or None. Another user's entry of the same name is a different row."""
        row = self._one("SELECT blob, key_id FROM streams WHERE username=? AND name=?", (username, name))
        return (bytes(row[0]), row[1]) if row else None

    def stream_list(self, username: str) -> list:
        return [(r[0], bytes(r[1]), r[2]) for r in self._all("SELECT name, blob, key_id FROM streams WHERE username=? ORDER BY name COLLATE NOCASE", (username,))]

    def stream_delete(self, username: str, name: str) -> int:
        return self._write("DELETE FROM streams WHERE username=? AND name=?", (username, name))

    def streams_delete_user(self, username: str) -> int:
        return self._write("DELETE FROM streams WHERE username=?", (username,))

    def purge_appkeys(self, existing) -> int:
        """Delete the keys of users that are no longer in the configuration (a new user with the same name must not inherit one)."""
        gone = [name for name in self.appkeys() if name not in existing]
        for name in gone:
            self.delete_appkey(name)
        for (name,) in self._all("SELECT DISTINCT username FROM streams"):             # stored streams of a user that is gone: the same rule
            if name not in existing:
                self.streams_delete_user(name)
        for (name,) in self._all("SELECT username FROM playqueue"):
            if name not in existing:
                self.playqueue_delete(name)
        return len(gone)

    def appkey_users(self) -> dict:
        """{user name: when the key was made}."""
        return {r[0]: r[1] for r in self._all("SELECT username, created FROM appkeys")}

    # ---- shelves: each user's "favourite" and "next" (read next) lists of files and folders ------------------------------------
    SHELVES = ("favourite", "next")
    MAX_SHELF = 500

    def shelf_add(self, username: str, shelf: str, root: str, path: str, is_dir: bool, now=None) -> bool:
        """Put an entry on a shelf (at the end). False when the shelf is full; adding twice changes nothing."""
        with self._lock, self._db:
            have = self._db.execute("SELECT COUNT(*), COALESCE(MAX(rank), 0) FROM shelves WHERE username=? AND shelf=?",
                                    (username, shelf)).fetchone()
            if have[0] >= self.MAX_SHELF:
                return False
            self._db.execute("INSERT OR IGNORE INTO shelves VALUES (?,?,?,?,?,?,?)",
                             (username, shelf, root, path, 1 if is_dir else 0, have[1] + 1, time.time() if now is None else now))
        return True

    def shelf_remove(self, username: str, shelf: str, root: str, path: str) -> int:
        return self._write("DELETE FROM shelves WHERE username=? AND shelf=? AND root=? AND path=?", (username, shelf, root, path))

    def shelf_list(self, username: str, shelf: str) -> list:
        """The entries of one shelf: "next" in the order the user chose, "favourite" newest first."""
        order = "rank ASC" if shelf == "next" else "added DESC"
        rows = self._all("SELECT root, path, is_dir FROM shelves WHERE username=? AND shelf=? ORDER BY " + order, (username, shelf))
        return [{"root": r[0], "path": r[1], "is_dir": bool(r[2])} for r in rows]

    def shelf_move(self, username: str, shelf: str, root: str, path: str, up: bool) -> bool:
        """Swap an entry of the "next" list with its neighbour above (up) or below."""
        with self._lock, self._db:
            me = self._db.execute("SELECT rank FROM shelves WHERE username=? AND shelf=? AND root=? AND path=?",
                                  (username, shelf, root, path)).fetchone()
            if me is None:
                return False
            cmp, order = ("<", "DESC") if up else (">", "ASC")
            other = self._db.execute("SELECT root, path, rank FROM shelves WHERE username=? AND shelf=? AND rank %s ? "
                                     "ORDER BY rank %s LIMIT 1" % (cmp, order), (username, shelf, me[0])).fetchone()
            if other is None:
                return False
            self._db.execute("UPDATE shelves SET rank=? WHERE username=? AND shelf=? AND root=? AND path=?",
                             (other[2], username, shelf, root, path))
            self._db.execute("UPDATE shelves SET rank=? WHERE username=? AND shelf=? AND root=? AND path=?",
                             (me[0], username, shelf, other[0], other[1]))
        return True

    def shelf_flags(self, username: str, root: str, paths: list) -> dict:
        """{path: [shelves it is on]} for the given paths of one library (the stars in a folder listing)."""
        out: dict = {}
        for i in range(0, len(paths), 400):
            chunk = paths[i:i + 400]
            rows = self._all("SELECT path, shelf FROM shelves WHERE username=? AND root=? AND path IN (%s)" % ",".join("?" * len(chunk)),
                             (username, root, *chunk))
            for p, shelf in rows:
                out.setdefault(p, []).append(shelf)
        return out

    # ---- bookmarks in audio and video files -------------------------------------------------------------------------------------
    MAX_BOOKMARKS = 200

    def add_bookmark(self, username: str, root: str, path: str, position: float, note: str, now=None):
        """The new bookmark's id, or None when this file already has the maximum for this user."""
        with self._lock, self._db:
            have = self._db.execute("SELECT COUNT(*) FROM bookmarks WHERE username=? AND root=? AND path=?", (username, root, path)).fetchone()[0]
            if have >= self.MAX_BOOKMARKS:
                return None
            cur = self._db.execute("INSERT INTO bookmarks (username, root, path, position, note, created) VALUES (?,?,?,?,?,?)",
                                   (username, root, path, position, note, time.time() if now is None else now))
            return cur.lastrowid

    def list_bookmarks(self, username: str, root: str, path: str) -> list:
        rows = self._all("SELECT id, position, note, created FROM bookmarks WHERE username=? AND root=? AND path=? ORDER BY position",
                         (username, root, path))
        return [{"id": r[0], "position": r[1], "note": r[2], "created": r[3]} for r in rows]

    def delete_bookmark(self, username: str, bookmark_id: int) -> int:
        return self._write("DELETE FROM bookmarks WHERE id=? AND username=?", (bookmark_id, username))

    # ---- KOReader progress sync ---------------------------------------------------------------------------------------------
    def put_kosync(self, username: str, document: str, progress: str, percentage: float, device: str, device_id: str, now=None) -> None:
        self._write("INSERT OR REPLACE INTO kosync VALUES (?,?,?,?,?,?,?)",
                    (username, document, progress, percentage, device, device_id, time.time() if now is None else now))

    def get_kosync(self, username: str, document: str):
        row = self._one("SELECT progress, percentage, device, device_id, timestamp FROM kosync WHERE username=? AND document=?",
                        (username, document))
        return None if row is None else {"progress": row[0], "percentage": row[1], "device": row[2], "device_id": row[3],
                                         "timestamp": int(row[4])}

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
        self.purge_old_devices(now=now)
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

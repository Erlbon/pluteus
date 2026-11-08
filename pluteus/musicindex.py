"""Which folders are albums and which are artists, for the music apps (Subsonic). Disposable: the folders are the truth.

A library is walked once in the background and the result kept in SQLite (`musicindex.sqlite3` beside the metadata cache), so music apps get
artist and album lists without a walk through the whole tree on every request. Nothing is written into the library.

The rules, the first one that knows wins:
  1. A note decides. A folder with `album.md` (or `album.nfo`) is an album, with its songs directly in it or in the folders inside it (CD1,
     CD2 ...). A folder with `artist.md` (or `artist.nfo`) is the artist of the albums at most three levels below it.
  2. Otherwise the folder shape decides. A folder that holds songs is an album (a disc folder such as `CD1` belongs to the album above it),
     and the folder above an album is its artist. Songs in a top folder of the library make that folder an album of its own artist.

So `0 - Bands/H/Handsome Family, The/[2016] - Unseen/01.mp3` is the album `[2016] - Unseen` by `Handsome Family, The`, at any depth.
Tags are not read here. The folders marked .adult, .private or .hidden on the way to an album are kept with it, so every user's list can be
checked against the same marker rules as the web pages.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass

from . import kinds
from .dbutil import open_database
from .fs import clamp_time, longpath

log = logging.getLogger("pluteus.musicindex")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS albums (root TEXT NOT NULL, path TEXT NOT NULL, artist TEXT NOT NULL, songs INTEGER NOT NULL, mtime REAL NOT NULL,
    guard TEXT NOT NULL, PRIMARY KEY (root, path));
CREATE TABLE IF NOT EXISTS scans (root TEXT PRIMARY KEY, top TEXT NOT NULL, finished REAL NOT NULL, seconds REAL NOT NULL);
"""
RADIO_FOLDER = "radio"             # a top-level folder of station files: never music
ALBUM_NOTES = ("album.md", "album.nfo")
ARTIST_NOTES = ("artist.md", "artist.nfo")
ARTIST_NOTE_REACH = 3              # an artist.md is the artist of the albums up to this many levels below it
MAX_DEPTH = 12
RESCAN_SECONDS = 1800              # a library is walked again after this long (or at once when an app asks for a scan)
DISC = re.compile(r"^(cd|disc|disk|dvd|vinyl|side)\s*[-_.]?\s*[0-9a-d]{1,2}\b", re.IGNORECASE)


@dataclass(frozen=True)
class Album:
    root: str
    parts: tuple            # the album folder below the library
    artist: tuple           # the artist folder (may be the album folder itself)
    songs: int
    mtime: float
    guard: tuple            # the folders on the way (the library's top included, as ()) that carry a marker file

    @property
    def name(self) -> str:
        return self.parts[-1]


def _join(parts) -> str:
    return "/".join(parts)


def _split(text: str) -> tuple:
    return tuple(text.split("/")) if text else ()


def scan_tree(lib, root: str, top: str) -> list:
    """[Album] of one library folder: the walk (one listing per folder, nothing else read), then the rules above."""
    cfg = lib.cfg
    markers = {cfg.adult_marker.lower(), cfg.private_marker.lower(), cfg.hidden_marker.lower()}
    notes = set(ALBUM_NOTES + ARTIST_NOTES)
    folders: dict = {}                      # parts -> (songs, subfolders, album note, artist note, marked)
    base = longpath(top)
    stack = [()]
    while stack:
        parts = stack.pop()
        songs, subs, found, marked = 0, [], set(), False
        try:
            with os.scandir(os.path.join(base, *parts)) as it:
                for e in it:
                    name = e.name
                    if lib.is_hidden_name(name):
                        marked = marked or name.lower() in markers
                        continue
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if not (not parts and name.lower() == RADIO_FOLDER):
                                subs.append(name)
                            continue
                    except OSError:
                        continue
                    low = name.lower()
                    if low in notes:
                        found.add(low)
                    elif kinds.kind_of(name) == "audio":
                        songs += 1
        except OSError:
            continue                        # gone or unreadable: no albums from here
        folders[parts] = (songs, tuple(subs), bool(found & set(ALBUM_NOTES)), bool(found & set(ARTIST_NOTES)), marked)
        if len(parts) < MAX_DEPTH:
            stack += [parts + (s,) for s in subs]

    def album_of(parts):
        """The album a folder with songs belongs to: the folder above when that one is an album by its note, or it is a disc folder."""
        parent = parts[:-1]
        if parts and parent in folders and (folders[parent][2] or (DISC.match(parts[-1]) and parent)):
            return parent
        return parts

    counts: dict = {}
    for parts, (songs, _subs, album_note, _artist_note, _marked) in folders.items():
        if album_note and parts:
            counts.setdefault(parts, 0)
        if songs and parts:
            home = album_of(parts)
            counts[home] = counts.get(home, 0) + songs
    albums_in = {}                                       # folder -> how many of its own folders are albums
    for parts in counts:
        if parts:
            albums_in[parts[:-1]] = albums_in.get(parts[:-1], 0) + 1

    def artist_level(parts) -> bool:
        """A folder with songs among folders that are artists (they hold albums): loose songs in an artist folder, not an album of the
        folder above (`W/Wig Wam` next to `W/We/[2008] - Tension`)."""
        parent = parts[:-1]
        return any(albums_in.get(parent + (s,)) for s in folders.get(parent, (0, ()))[1] if s != parts[-1])

    albums = []
    for parts, songs in counts.items():
        if not songs:
            continue
        artist = None
        for up in range(0, ARTIST_NOTE_REACH + 1):          # 1. a note decides
            above = parts[:len(parts) - up]
            if above and folders.get(above, (0, (), False, False))[3]:
                artist = above
                break
        if artist is None:                                   # 2. the folder shape
            if len(parts) == 1 or albums_in.get(parts) or artist_level(parts):
                artist = parts                               # a top folder, or an artist folder with loose songs
            else:
                artist = parts[:-1]
                while len(artist) > 1 and DISC.match(artist[-1]):
                    artist = artist[:-1]                     # never a disc folder (`Disc 3 - Live/[1965] - Album`)
        guard = tuple(parts[:i] for i in range(len(parts) + 1) if folders.get(parts[:i], (0, (), False, False, False))[4])
        try:
            mtime = clamp_time(os.stat(os.path.join(base, *parts)).st_mtime)
        except OSError:
            mtime = 0.0
        albums.append(Album(root, parts, artist, songs, mtime, guard))
    return albums


class MusicIndex:
    """The albums of every library music apps see, kept in memory and in SQLite (path '' = in memory only). All walks run in one
    background thread: at start (watch()), when a library is first asked about, every RESCAN_SECONDS, and at once after rescan(). A
    question about a library that has never been walked waits up to FIRST_WAIT seconds for it, then gets what there is (nothing yet):
    a big library over a slow disk can take minutes, and a music app must not hang that long."""

    FIRST_WAIT = 20.0

    def __init__(self, lib, path: str = "", wanted=None):
        """wanted: a callable giving the [(name, folder)] to keep walked now (the libraries ticked for music apps); without it, the ones
        asked about."""
        self.lib = lib
        self._source = wanted
        self._db = open_database(path or ":memory:", _SCHEMA, log=log)
        self._db_lock = threading.Lock()
        self._data: dict = {}               # root -> (top, finished, [Album], {parts: Album})
        self._root_locks: dict = {}
        self._locks_lock = threading.Lock()
        self._wanted: dict = {}             # root -> top: the libraries to keep walked
        self._walked: dict = {}             # (root, top) -> Event, set when a walk of it has ended
        self._wake = threading.Event()
        self._thread = None
        self.scanning = ""                  # the library being walked now, "" when none

    def watch(self, roots) -> None:
        """Keep these libraries [(name, folder)] walked, starting now (in the background); a copy on disk is used until then."""
        for root, top in roots:
            self._wanted[root] = top
            with self._lock_for(root):
                if self._data.get(root, ("",))[0] != top:
                    self._load(root, top)
        self._start()
        self._wake.set()

    # ---- reading -------------------------------------------------------------------------------------------------------
    def albums(self, root: str, top: str) -> list:
        return self._entry(root, top)[2]

    def album(self, root: str, top: str, parts) -> Album | None:
        return self._entry(root, top)[3].get(tuple(parts))

    def count(self) -> int:
        return sum(a.songs for entry in list(self._data.values()) for a in entry[2])

    def _entry(self, root: str, top: str):
        hit = self._data.get(root)
        if hit and hit[0] == top:
            return hit
        with self._lock_for(root):
            hit = self._data.get(root)
            if hit and hit[0] == top:
                return hit
            stored = self._load(root, top)
            if stored is not None:
                return stored
            walked = self._walked.setdefault((root, top), threading.Event())
        self._wanted[root] = top                             # never walked: the background thread does it, this question waits a little
        self._start()
        self._wake.set()
        walked.wait(self.FIRST_WAIT)
        hit = self._data.get(root)
        return hit if hit and hit[0] == top else (top, 0.0, [], {})

    def _lock_for(self, root: str):
        with self._locks_lock:
            return self._root_locks.setdefault(root, threading.Lock())

    # ---- walking -------------------------------------------------------------------------------------------------------
    def rescan(self) -> None:
        """Walk every library asked about again, in the background, now."""
        for root in list(self._data):
            top, _, albums, by_parts = self._data[root]
            self._data[root] = (top, 0.0, albums, by_parts)
        self._start()
        self._wake.set()

    def _scan(self, root: str, top: str):
        began = time.monotonic()
        self.scanning = root
        walked = self._walked.setdefault((root, top), threading.Event())
        try:
            found = scan_tree(self.lib, root, top)
        except BaseException:
            walked.set()                                     # waiters go on without lists
            raise
        finally:
            self.scanning = ""
        seconds = time.monotonic() - began
        entry = (top, time.time(), found, {a.parts: a for a in found})
        self._data = {**self._data, root: entry}
        walked.set()
        self._save(root, top, entry[1], seconds, found)
        log.info("music index: %s walked in %.1f s, %d albums", root, seconds, len(found))
        return entry

    def _start(self) -> None:
        if self._thread is None:
            with self._locks_lock:
                if self._thread is None:
                    self._thread = threading.Thread(target=self._loop, name="pluteus-musicindex", daemon=True)
                    self._thread.start()

    def _loop(self) -> None:
        while True:
            self._wake.wait(60)
            self._wake.clear()
            for root, top in (self._source() if self._source else list(self._wanted.items())):
                hit = self._data.get(root)
                if hit and hit[0] == top and time.time() - hit[1] < RESCAN_SECONDS:
                    continue
                try:
                    with self._lock_for(root):
                        self._scan(root, top)
                except Exception:                            # noqa: BLE001  a failed walk keeps the old lists; the next round tries again
                    log.exception("music index: walking %s failed", root)

    # ---- the SQLite copy, so a restart does not start from nothing -------------------------------------------------------
    def _load(self, root: str, top: str):
        try:
            with self._db_lock:
                scan = self._db.execute("SELECT top, finished FROM scans WHERE root=?", (root,)).fetchone()
                if scan is None or scan[0] != top:
                    return None
                rows = self._db.execute("SELECT path, artist, songs, mtime, guard FROM albums WHERE root=?", (root,)).fetchall()
            found = [Album(root, _split(p), _split(a), s, m, tuple(_split(g) for g in json.loads(guard))) for p, a, s, m, guard in rows]
        except (sqlite3.Error, ValueError, TypeError):
            return None
        entry = (top, scan[1], found, {a.parts: a for a in found})
        self._data = {**self._data, root: entry}
        return entry

    def _save(self, root: str, top: str, finished: float, seconds: float, found: list) -> None:
        try:
            with self._db_lock, self._db:
                self._db.execute("DELETE FROM albums WHERE root=?", (root,))
                self._db.executemany("INSERT INTO albums VALUES (?,?,?,?,?,?)",
                                     [(root, _join(a.parts), _join(a.artist), a.songs, a.mtime, json.dumps([_join(g) for g in a.guard]))
                                      for a in found])
                self._db.execute("INSERT OR REPLACE INTO scans VALUES (?,?,?,?)", (root, top, finished, seconds))
        except sqlite3.Error:
            log.warning("music index: the copy on disk could not be written; the lists are kept in memory")

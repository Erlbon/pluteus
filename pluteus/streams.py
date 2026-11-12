"""Folders full of station files (Radio/, IPTV/, or any folder the owner fills with .m3u / .pls / .url files).

Such a folder is shown differently from a library folder: one flat grid of logo tiles, with the country sub-folders as filters, a search box
and a random pick. This module only finds out which folders those are and lists what is in them; web.streams_page draws it.
Nothing here fetches an address: station files are small text files that are only read."""
from __future__ import annotations

import functools
import os
import threading
import time

from . import radio

MAX_DEPTH = 3                  # a country folder, and one more below it
MAX_STATIONS = 5000            # a larger collection is cut here (the page says so)
SAMPLE_FOLDERS = 3             # sub-folders looked into to decide that a folder of folders is a stream folder
MAX_SUBFOLDERS = 400
COUNT_SECONDS = 60.0           # how long count() keeps its answer for a folder
MAX_COUNTS = 2000              # folders remembered at most (two per user with an upload folder)
_counts: dict = {}             # folder -> (when counted, how many)
_counts_lock = threading.Lock()
_clock = time.monotonic


def _scan(path: str):
    try:
        with os.scandir(path) as it:
            return list(it)
    except OSError:
        return []


def _is_station(entry) -> bool:
    return entry.is_file(follow_symlinks=False) and radio.is_radio_file(entry.name)


def _split(entries) -> tuple:
    """(file names, folder names) of a listing, hidden names left out. The listing is either what os.scandir gave, or the page's own
    (fs.Entry, from Library.list_dir: is_dir is a plain value there, not a call)."""
    files, dirs = [], []
    for e in entries:
        if e.name.startswith("."):
            continue
        if isinstance(e.is_dir, bool):
            (dirs if e.is_dir else files).append(e.name)
        elif e.is_dir(follow_symlinks=False):
            dirs.append(e.name)
        elif e.is_file(follow_symlinks=False):
            files.append(e.name)
    return files, dirs


def is_stream_folder(path: str, entries=None, depth: int = 1) -> bool:
    """True when this folder holds station files, or only folders that do. A mixed folder (a few station files among many books, say)
    stays an ordinary one: stations must be at least half of its files.

    `entries`: the folder's listing when the caller has read it already (the folder page has: Library.list_dir with every file listed),
    so the folder is not listed a second time. A folder that holds only folders is decided by looking into a few of them, and no
    deeper than the station grid itself reaches (MAX_DEPTH): an ordinary library of folders in folders is not followed down."""
    files, dirs = _split(entries if entries is not None else _scan(path))
    stations = [name for name in files if radio.is_radio_file(name)]
    if stations:
        picture = {os.path.splitext(name)[0].lower() for name in stations}
        others = [name for name in files if not radio.is_radio_file(name)
                  and not (os.path.splitext(name)[1].lower() in dict(radio.LOGO_TYPES) and os.path.splitext(name)[0].lower() in picture)]
        return len(stations) >= len(others) and not (dirs and len(dirs) > len(stations))
    if files or not dirs or len(dirs) > MAX_SUBFOLDERS or depth >= MAX_DEPTH:
        return False
    for name in sorted(dirs)[:SAMPLE_FOLDERS]:                                   # a folder of country folders
        if not is_stream_folder(os.path.join(path, name), None, depth + 1):
            return False
    return True


@functools.lru_cache(maxsize=4096)
def _kind_cached(path: str, mtime: float) -> str:
    try:
        with open(path, "rb") as f:
            raw = f.read(8192)
    except OSError:
        return "radio"
    text = raw.decode("utf-8", "replace")
    return "tv" if radio.looks_like_tv(text, radio.parse(text)) else "radio"


def kind_of(path: str, entry=None) -> str:
    """'tv' or 'radio' for a station file (an HLS or transport-stream address, or the picker's own mark, means a channel). The file is
    read once and remembered until its time changes. `entry`: its directory entry when the caller is listing the folder anyway: the
    time is taken from there (on Windows the listing holds it, so 5,000 stations cost no call each)."""
    try:
        mtime = entry.stat(follow_symlinks=False).st_mtime if entry is not None else os.path.getmtime(path)
    except OSError:
        return "radio"
    return _kind_cached(path, mtime)


def collect(path: str) -> tuple:
    """(stations, countries, cut): every station file below the folder, flat. A station is a dict with name, parts (below the folder),
    path, country (the first folder it sits in, or '') and kind. countries is the sorted list of those folder names."""
    found, countries = [], set()

    def walk(folder: str, rel: tuple, depth: int) -> None:
        entries = _scan(folder)
        for e in sorted(entries, key=lambda e: e.name.lower()):
            if len(found) >= MAX_STATIONS:
                return
            if e.name.startswith("."):
                continue
            if _is_station(e):
                country = rel[0] if rel else ""
                if country:
                    countries.add(country)
                found.append({"name": os.path.splitext(e.name)[0], "parts": rel + (e.name,), "path": e.path,
                              "country": country, "kind": kind_of(e.path, e)})
            elif e.is_dir(follow_symlinks=False) and depth < MAX_DEPTH:
                walk(e.path, rel + (e.name,), depth + 1)
    walk(path, (), 1)
    return found, sorted(countries, key=str.lower), len(found) >= MAX_STATIONS


def forget_counts() -> None:
    """Drop the remembered counts (a save of stations does: the home page shows the new number at once)."""
    with _counts_lock:
        _counts.clear()


def count(path: str) -> int:
    """How many station files are in the folder and below it (the home page's "212 stations"); no file is opened. The answer is kept
    for a minute per folder: the home page asks on every visit, and counting lists the folder and the folders below it."""
    now = _clock()
    with _counts_lock:
        hit = _counts.get(path)
        if hit and 0 <= now - hit[0] < COUNT_SECONDS:
            return hit[1]
    total = _count(path)
    with _counts_lock:
        if len(_counts) >= MAX_COUNTS:
            _counts.clear()
        _counts[path] = (now, total)
    return total


def _count(path: str) -> int:
    total = 0

    def walk(folder: str, depth: int) -> None:
        nonlocal total
        for e in _scan(folder):
            if total >= MAX_STATIONS:
                return
            if e.name.startswith("."):
                continue
            if _is_station(e):
                total += 1
            elif e.is_dir(follow_symlinks=False) and depth < MAX_DEPTH:
                walk(e.path, depth + 1)
    walk(path, 1)
    return total

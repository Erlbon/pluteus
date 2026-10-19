"""Folders full of station files (Radio/, IPTV/, or any folder the owner fills with .m3u / .pls / .url files).

Such a folder is shown differently from a library folder: one flat grid of logo tiles, with the country sub-folders as filters, a search box
and a random pick. This module only finds out which folders those are and lists what is in them; web.streams_page draws it.
Nothing here fetches an address: station files are small text files that are only read."""
from __future__ import annotations

import functools
import os

from . import radio

MAX_DEPTH = 3                  # a country folder, and one more below it
MAX_STATIONS = 5000            # a larger collection is cut here (the page says so)
SAMPLE_FOLDERS = 3             # sub-folders looked into to decide that a folder of folders is a stream folder
MAX_SUBFOLDERS = 400


def _scan(path: str):
    try:
        with os.scandir(path) as it:
            return list(it)
    except OSError:
        return []


def _is_station(entry) -> bool:
    return entry.is_file(follow_symlinks=False) and radio.is_radio_file(entry.name)


def is_stream_folder(path: str, entries=None) -> bool:
    """True when this folder holds station files, or only folders that do. A mixed folder (a few station files among many books, say)
    stays an ordinary one: stations must be at least half of its files."""
    entries = entries if entries is not None else _scan(path)
    files = [e for e in entries if e.is_file(follow_symlinks=False) and not e.name.startswith(".")]
    stations = [e for e in files if _is_station(e)]
    dirs = [e for e in entries if e.is_dir(follow_symlinks=False) and not e.name.startswith(".")]
    if stations:
        picture = {os.path.splitext(e.name)[0].lower() for e in stations}
        others = [e for e in files if not _is_station(e)
                  and not (os.path.splitext(e.name)[1].lower() in dict(radio.LOGO_TYPES) and os.path.splitext(e.name)[0].lower() in picture)]
        return len(stations) >= len(others) and not (dirs and len(dirs) > len(stations))
    if files or not dirs or len(dirs) > MAX_SUBFOLDERS:
        return False
    for folder in sorted(dirs, key=lambda e: e.name)[:SAMPLE_FOLDERS]:           # a folder of country folders
        if not is_stream_folder(folder.path):
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


def kind_of(path: str) -> str:
    """'tv' or 'radio' for a station file (an HLS or transport-stream address, or the picker's own mark, means a channel)."""
    try:
        mtime = os.path.getmtime(path)
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
                              "country": country, "kind": kind_of(e.path)})
            elif e.is_dir(follow_symlinks=False) and depth < MAX_DEPTH:
                walk(e.path, rel + (e.name,), depth + 1)
    walk(path, (), 1)
    return found, sorted(countries, key=str.lower), len(found) >= MAX_STATIONS


def count(path: str) -> int:
    """How many station files are in the folder and below it (the home page's "212 stations"); no file is opened."""
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

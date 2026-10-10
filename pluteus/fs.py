"""Filesystem access: safe path resolution, folder listing and visibility.

The filesystem is the source of truth: a listing is a live os.scandir() of the
folder being opened, nothing is indexed up front. Every real path goes through
longpath() so folders whose names end in a dot or space (the Windows \\\\?\\
form) stay reachable.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

from .config import MIME_TYPES, Config, User


class PathError(ValueError):
    """A request path that is malformed or unsafe."""


class NotFound(LookupError):
    """Missing, excluded, hidden from this user or not servable. One error on
    purpose, so a hidden folder looks exactly like one that does not exist."""


_NT = os.name == "nt"
_SPLIT = re.compile(r"(\d+)")


def longpath(p: str) -> str:
    """Windows: the \\\\?\\ form (no name normalisation); other systems: unchanged."""
    if not _NT:
        return p
    if p.startswith("\\\\?\\"):
        return p
    # abspath() runs Windows' own name normalisation, which strips trailing dots and
    # spaces from every folder name, so absolute paths must NOT go through it.
    if not os.path.isabs(p):
        p = os.path.abspath(p)
    p = p.replace("/", "\\")
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def natural_key(name: str) -> list:
    """Sort key where digit runs compare as numbers: '3.10' after '3.9', case-insensitive."""
    return [int(t) if i % 2 else t.lower() for i, t in enumerate(_SPLIT.split(name))]


def check_segment(seg: str) -> None:
    if seg in ("", ".", "..") or "\x00" in seg or "/" in seg:
        raise PathError("bad path segment")
    if _NT and ("\\" in seg or ":" in seg):
        raise PathError("bad path segment")


@dataclass(frozen=True)
class Entry:
    name: str
    display: str
    is_dir: bool
    size: int
    mtime: float
    parts: tuple          # path below the root, ending with this entry's name
    path: str = ""        # real (extended-length) path, set for files


@dataclass(frozen=True)
class FileInfo:
    path: str
    name: str
    mime: str
    size: int
    mtime: float


class Library:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._strip = re.compile(cfg.display_strip) if cfg.display_strip else None
        self._real = {n: os.path.realpath(longpath(r.path)) for n, r in cfg.roots.items()}
        self._markers: dict = {}

    # ---- names -----------------------------------------------------------
    def display_name(self, name: str) -> str:
        if self._strip:
            shown = self._strip.sub("", name, count=1)
            if shown.strip():
                return shown
        return name

    def _excluded(self, name: str) -> bool:
        return name.startswith(".") or name.lower() in self.cfg.excludes

    # ---- visibility ------------------------------------------------------
    def _has_marker(self, dirpath: str) -> bool:
        now = time.monotonic()
        hit = self._markers.get(dirpath)
        if hit and hit[1] > now:
            return hit[0]
        found = os.path.exists(os.path.join(dirpath, self.cfg.adult_marker))
        if len(self._markers) > 20000:
            self._markers.clear()
        self._markers[dirpath] = (found, now + 60)
        return found

    def _root(self, user: User, root_name: str):
        if root_name not in self.cfg.roots or not user.may_use(root_name):
            raise NotFound(root_name)
        return self.cfg.roots[root_name]

    def _dir_path(self, user: User, root_name: str, parts: tuple) -> str:
        """Absolute folder path for a request, after the name, exclusion and marker checks."""
        root = self._root(user, root_name)
        for seg in parts:
            check_segment(seg)
        for seg in parts:
            if self._excluded(seg):
                raise NotFound(seg)
        path = longpath(root.path)
        if not user.adult and self._has_marker(path):
            raise NotFound(root_name)
        for seg in parts:
            path = os.path.join(path, seg)
            if not user.adult and self._has_marker(path):
                raise NotFound(seg)
        return path

    def _inside(self, root_name: str, path: str) -> None:
        """Refuse anything whose real location (symlinks, junctions) leaves the root."""
        real = os.path.normcase(os.path.realpath(path))
        top = os.path.normcase(self._real[root_name])
        if real != top and not real.startswith(top.rstrip(os.sep) + os.sep):
            raise NotFound("outside the root")

    # ---- public ----------------------------------------------------------
    def roots_for(self, user: User) -> list:
        return [r for r in self.cfg.roots.values() if user.may_use(r.name)]

    def list_dir(self, user: User, root_name: str, parts) -> list:
        """Folders first, then books, each in natural order."""
        parts = tuple(parts)
        path = self._dir_path(user, root_name, parts)
        if not os.path.isdir(path):
            raise NotFound("not a folder")
        self._inside(root_name, path)
        dirs, files = [], []
        try:
            with os.scandir(path) as it:
                for e in it:
                    name = e.name
                    if self._excluded(name):
                        continue
                    try:
                        is_dir = e.is_dir()
                        if is_dir:
                            if not user.adult and self._has_marker(e.path):
                                continue
                        elif os.path.splitext(name)[1].lower() not in self.cfg.extensions \
                                or not e.is_file():
                            continue
                        st = e.stat()
                    except OSError:
                        continue
                    entry = Entry(name, self.display_name(name) if is_dir else name, is_dir,
                                  0 if is_dir else st.st_size, st.st_mtime, parts + (name,),
                                  "" if is_dir else e.path)
                    (dirs if is_dir else files).append(entry)
        except OSError as err:
            raise NotFound(str(err))
        dirs.sort(key=lambda x: natural_key(x.name))
        files.sort(key=lambda x: natural_key(x.name))
        return dirs + files

    def file_info(self, user: User, root_name: str, parts) -> FileInfo:
        parts = tuple(parts)
        if not parts:
            raise NotFound("no file named")
        dir_path = self._dir_path(user, root_name, parts[:-1])
        name = parts[-1]
        check_segment(name)
        ext = os.path.splitext(name)[1].lower()
        if self._excluded(name) or ext not in self.cfg.extensions:
            raise NotFound(name)
        path = os.path.join(dir_path, name)
        if not os.path.isfile(path):
            raise NotFound(name)
        self._inside(root_name, path)
        st = os.stat(path)
        return FileInfo(path, name, MIME_TYPES.get(ext, "application/octet-stream"),
                        st.st_size, st.st_mtime)

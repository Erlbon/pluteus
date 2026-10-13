"""Filesystem access: safe path resolution, folder listing and visibility.

The filesystem is the source of truth: a listing is a live os.scandir() of the
folder being opened, nothing is indexed up front. Every real path goes through
longpath() so folders whose names end in a dot or space (the Windows \\\\?\\
form) stay reachable.
"""
from __future__ import annotations

import dataclasses
import errno
import mimetypes
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .config import MIME_TYPES, UPLOADS_ROOT, Config, Root, User


class PathError(ValueError):
    """A request path that is malformed or unsafe."""


class NotFound(LookupError):
    """Missing, excluded, hidden from this user or not servable. One error on
    purpose, so a hidden folder looks exactly like one that does not exist."""


class Unavailable(OSError):
    """The storage itself failed (share offline, permission denied): a server problem, not a missing path."""


_PROBES = ThreadPoolExecutor(max_workers=16, thread_name_prefix="probe")
_NT = os.name == "nt"
_SPLIT = re.compile(r"(\d+)")
_MAX_TIME = 2 ** 31 - 1          # year 2038; Windows time functions refuse values far outside 1970..3000


def clamp_time(t: float) -> float:
    """A modification time that the time functions can always format (files can carry absurd stamps)."""
    return t if 0 <= t <= _MAX_TIME else 0.0


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
    # Digit runs are cut at 18 digits: Python refuses int() on thousands of digits, and no real name needs more.
    return [int(t[:18]) if i % 2 else t.lower() for i, t in enumerate(_SPLIT.split(name))]


SORTS = ("name", "type", "size", "date")


def sort_entries(entries: list, sort: str = "name", desc: bool = False) -> list:
    """Re-order ONE folder listing (already read from disk; nothing is indexed). Folders stay above files and are
    ordered by name, or by date when sorting by date; files go by the chosen column with the name as tie-breaker."""
    dirs = [e for e in entries if e.is_dir]
    files = [e for e in entries if not e.is_dir]
    keys = {"name": lambda e: natural_key(e.name),
            "type": lambda e: (os.path.splitext(e.name)[1].lower(), natural_key(e.name)),
            "size": lambda e: (e.size, natural_key(e.name)),
            "date": lambda e: (e.mtime, natural_key(e.name))}
    sort = sort if sort in keys else "name"
    dirs.sort(key=keys["date"] if sort == "date" else keys["name"], reverse=desc)
    files.sort(key=keys[sort], reverse=desc)
    return dirs + files


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
    src: str = ""         # real path of the entry itself (files and folders), used to read its size and date later
    has_stat: bool = True  # False: size and mtime were not read yet (list_dir(stat=False)); see Library.with_stats


@dataclass(frozen=True)
class FileInfo:
    path: str
    name: str
    mime: str
    size: int
    mtime: float


@dataclass(frozen=True)
class Marks:
    """The marker files in one folder. private / hidden are None when the file is absent, else the set of user names
    (lower-cased) written in it: allowed for .private, still shown in the listing for .hidden."""
    adult: bool = False
    private: frozenset | None = None
    hidden: frozenset | None = None


class Library:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._strip = re.compile(cfg.display_strip) if cfg.display_strip else None
        self._real = {n: os.path.realpath(longpath(r.path)) for n, r in cfg.roots.items()}
        self._markers: dict = {}

    def set_roots(self, roots: dict) -> None:
        """Take a new set of libraries (the config file changed, or the admin page added one) without a restart."""
        real = {n: os.path.realpath(longpath(r.path)) for n, r in roots.items()}
        self._real = real
        self.cfg = dataclasses.replace(self.cfg, roots=dict(roots))

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
    @staticmethod
    def _names_in(path: str) -> frozenset:
        """The user names written in a marker file: one per line or comma separated, '#' starts a comment. An empty
        file names nobody; a file that cannot be read also names nobody (closed, never open by mistake)."""
        try:
            with open(path, "rb") as fh:
                text = fh.read(65536).decode("utf-8-sig", "replace")
        except OSError:
            return frozenset()
        names = set()
        for line in text.splitlines():
            for name in line.split("#", 1)[0].split(","):
                if name.strip():
                    names.add(name.strip().casefold())
        return frozenset(names)

    def _marks(self, dirpath: str) -> Marks:
        """Which marker files sit in this folder (cached for a minute): three existence checks, and a read only
        for the markers that exist."""
        now = time.monotonic()
        hit = self._markers.get(dirpath)
        if hit and hit[1] > now:
            return hit[0]
        cfg = self.cfg

        def names(marker: str):
            path = os.path.join(dirpath, marker)
            return self._names_in(path) if os.path.exists(path) else None

        marks = Marks(os.path.exists(os.path.join(dirpath, cfg.adult_marker)), names(cfg.private_marker), names(cfg.hidden_marker))
        if len(self._markers) > 20000:
            self._markers.clear()
        self._markers[dirpath] = (marks, now + 60)
        return marks

    def _marks_many(self, dirpaths: list) -> list:
        if len(dirpaths) < 8:
            return [self._marks(p) for p in dirpaths]
        return list(_PROBES.map(self._marks, dirpaths))

    @staticmethod
    def _closed_to(user: User, marks: Marks) -> bool:
        """The folder and everything below it is off limits to this user (.adult, .private)."""
        if marks.adult and not user.adult:
            return True
        return marks.private is not None and user.name.casefold() not in marks.private

    @classmethod
    def _unlisted_for(cls, user: User, marks: Marks) -> bool:
        """Left out of its parent's listing for this user: closed, or marked .hidden and the user is not named in it."""
        return cls._closed_to(user, marks) or (marks.hidden is not None and user.name.casefold() not in marks.hidden)

    # ---- the personal upload folder: a virtual root called "uploads", different for every user ------------------
    def uploads_enabled(self, user: User) -> bool:
        """Uploads are on, and this user has a folder: never the anonymous user (it is shared by everyone)."""
        return bool(self.cfg.uploads_dir and user.upload and user.name != self.cfg.anonymous_user)

    def upload_base(self, user: User) -> str:
        """The user's own upload folder (created on first use). Raises NotFound when uploads are not available."""
        if not self.uploads_enabled(user):
            raise NotFound(UPLOADS_ROOT)
        path = os.path.join(self.cfg.uploads_dir, user.name)
        try:
            os.makedirs(longpath(path), exist_ok=True)
        except OSError as err:
            raise Unavailable(err.errno or errno.EIO, "the upload folder cannot be created: %s" % err)
        return path

    def _root(self, user: User, root_name: str):
        if root_name == UPLOADS_ROOT:
            return Root(UPLOADS_ROOT, self.upload_base(user))
        if root_name not in self.cfg.roots or not user.may_use(root_name):
            raise NotFound(root_name)
        return self.cfg.roots[root_name]

    def _top(self, user: User, root_name: str) -> str:
        """Real (symlink-resolved) location of a root for this user."""
        if root_name == UPLOADS_ROOT:
            return os.path.realpath(longpath(self.upload_base(user)))
        return self._real[root_name]

    def has_root(self, user: User, root_name: str) -> bool:
        if root_name == UPLOADS_ROOT:
            return self.uploads_enabled(user)
        return root_name in self.cfg.roots and user.may_use(root_name)

    def _dir_path(self, user: User, root_name: str, parts: tuple) -> str:
        """Absolute folder path for a request, after the name, exclusion and marker checks."""
        root = self._root(user, root_name)
        if not os.path.isdir(longpath(root.path)):          # an offline share is a server problem, not a missing page
            raise Unavailable(errno.EIO, "root %r is not reachable" % root_name)
        for seg in parts:
            check_segment(seg)
        for seg in parts:
            if self._excluded(seg):
                raise NotFound(seg)
        path = longpath(root.path)
        if self._closed_to(user, self._marks(path)):
            raise NotFound(root_name)
        for seg in parts:
            path = os.path.join(path, seg)
            if self._closed_to(user, self._marks(path)):
                raise NotFound(seg)
        return path

    def _inside(self, user: User, root_name: str, path: str) -> None:
        """Refuse anything whose real location (symlinks, junctions) leaves the root."""
        real = os.path.normcase(os.path.realpath(path))
        top = os.path.normcase(self._top(user, root_name))
        if real != top and not real.startswith(top.rstrip(os.sep) + os.sep):
            raise NotFound("outside the root")
        # The request named the path; the filesystem may have resolved it differently (8.3 short names such as
        # _INCOM~1, links). Hidden and excluded names must be judged on what was really reached.
        for seg in real[len(top):].split(os.sep):
            if seg and self._excluded(seg):
                raise NotFound("excluded")

    def canonical_parts(self, user: User, root_name: str, info, requested: tuple) -> tuple:
        """The path below the root with the exact spelling on disk. Windows ignores letter case, so a request for
        Plain/BOOK.epub and one for plain/book.epub are one file and must share one progress record."""
        real = os.path.realpath(info.path)
        top = self._top(user, root_name)
        if os.path.normcase(real).startswith(os.path.normcase(top).rstrip(os.sep) + os.sep):
            rel = real[len(top.rstrip(os.sep)) + 1:]
            if rel:
                return tuple(rel.split(os.sep))
        return tuple(requested)

    # ---- public ----------------------------------------------------------
    def roots_for(self, user: User) -> list:
        roots = [r for r in self.cfg.roots.values() if user.may_use(r.name)]
        if self.uploads_enabled(user):
            roots.append(Root(UPLOADS_ROOT, os.path.join(self.cfg.uploads_dir, user.name)))
        return roots

    def list_dir(self, user: User, root_name: str, parts, all_files: bool = False, stat: bool = True) -> list:
        """Folders first, then files, each in natural order. By default only the configured book types are listed
        (what OPDS feeds want); all_files=True lists every regular file (the web listing and the viewers).

        stat=False does not read each entry's size and date (entries come back with has_stat=False and zeros). Reading
        them costs one system call per file, which is cheap on a local disk but about 0.7 ms each across a Docker bind
        mount, so a page that shows 50 of 900 files should read only those 50: call with_stats() on the page."""
        parts = tuple(parts)
        path = self._dir_path(user, root_name, parts)
        if not os.path.isdir(path):
            raise NotFound("not a folder")
        self._inside(user, root_name, path)
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
                            pass                                    # the marker check is done for all folders at once, below
                        elif (not all_files and os.path.splitext(name)[1].lower() not in self.cfg.extensions) \
                                or not e.is_file():
                            continue
                        st = e.stat() if stat else None
                    except OSError:
                        continue
                    entry = Entry(name, self.display_name(name) if is_dir else name, is_dir,
                                  0 if (is_dir or st is None) else st.st_size,
                                  clamp_time(st.st_mtime) if st is not None else 0.0, parts + (name,),
                                  "" if is_dir else e.path, e.path, st is not None)
                    (dirs if is_dir else files).append(entry)
        except OSError as err:
            if err.errno in (errno.ENOENT, errno.ENOTDIR):
                raise NotFound(str(err))
            raise Unavailable(err.errno, str(err))
        if dirs:
            # Each subfolder needs a "which marker files are inside it" probe. Over SMB every probe is a network round
            # trip, so a folder with hundreds of subfolders is probed in parallel (results are cached for a minute).
            marks = self._marks_many([os.path.join(path, d.name) for d in dirs])
            dirs = [d for d, m in zip(dirs, marks) if not self._unlisted_for(user, m)]
        dirs.sort(key=lambda x: natural_key(x.name))
        files.sort(key=lambda x: natural_key(x.name))
        return dirs + files

    def with_stats(self, entries: list) -> list:
        """The same entries with their size and date read (in parallel when there are many). Entries that have them
        already pass through untouched; one that cannot be read any more (it vanished) is left out, as list_dir would."""
        todo = [e for e in entries if not e.has_stat]
        if not todo:
            return list(entries)

        def read(e: Entry):
            try:
                st = os.stat(e.src)
            except OSError:
                return None
            return dataclasses.replace(e, size=0 if e.is_dir else st.st_size, mtime=clamp_time(st.st_mtime), has_stat=True)

        done = [read(e) for e in todo] if len(todo) < 8 else list(_PROBES.map(read, todo))
        filled = {e.name: new for e, new in zip(todo, done)}
        out = []
        for e in entries:
            if e.has_stat:
                out.append(e)
            elif filled.get(e.name) is not None:
                out.append(filled[e.name])
        return out

    def file_info(self, user: User, root_name: str, parts, any_type: bool = False) -> FileInfo:
        """One servable file. any_type=True allows every regular file, not just the configured book types."""
        parts = tuple(parts)
        if not parts:
            raise NotFound("no file named")
        dir_path = self._dir_path(user, root_name, parts[:-1])
        name = parts[-1]
        check_segment(name)
        ext = os.path.splitext(name)[1].lower()
        if self._excluded(name) or (ext not in self.cfg.extensions and not any_type):
            raise NotFound(name)
        path = os.path.join(dir_path, name)
        if not os.path.isfile(path):
            raise NotFound(name)
        self._inside(user, root_name, path)
        st = os.stat(path)
        return FileInfo(path, name, MIME_TYPES.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream",
                        st.st_size, clamp_time(st.st_mtime))

    # ---- write access: only ever inside the user's own upload folder ---------------------------------------------
    def upload_dir(self, user: User, parts) -> str:
        """An existing folder inside this user's upload folder, ready to receive files. NotFound for anything else:
        other roots, other users' folders, excluded names, links that lead out."""
        parts = tuple(parts)
        path = self._dir_path(user, UPLOADS_ROOT, parts)
        if not os.path.isdir(path):
            raise NotFound("not a folder")
        self._inside(user, UPLOADS_ROOT, path)
        return path

    def upload_entry(self, user: User, parts) -> tuple:
        """(folder path, entry name) for an existing file or folder in the user's upload folder, to rename or delete."""
        parts = tuple(parts)
        if not parts:
            raise NotFound("the upload folder itself cannot be changed")
        folder = self.upload_dir(user, parts[:-1])
        name = parts[-1]
        check_segment(name)
        if self._excluded(name) or not os.path.lexists(os.path.join(folder, name)):
            raise NotFound(name)
        return folder, name

    def is_hidden_name(self, name: str) -> bool:
        """Names pluteus would hide or treat as a marker: a new file or folder must never get one."""
        cfg = self.cfg
        return self._excluded(name) or name.lower() in (cfg.adult_marker.lower(), cfg.private_marker.lower(), cfg.hidden_marker.lower())

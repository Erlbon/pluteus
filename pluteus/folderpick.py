"""The admin page's folder picker: list the sub-folders of a folder on the computer pluteus runs on.

Only folders are ever listed (never files), and only for an administrator. The picker works for a browser on the SAME computer
(the Windows and Linux programs, where the admin page listens on 127.0.0.1) or, when `library_bases` is set, for any admin but only
below those folders (Docker: the folders mounted into the container).
"""
from __future__ import annotations

import os
import string

from .fs import natural_key

MAX_ENTRIES = 1500
_SKIP = {"$recycle.bin", "system volume information", "$winreagent", "recovery", "config.msi"}


def allowed(client_host: str, bases: tuple) -> bool:
    """May this request use the picker? With bases: anyone who reached the admin page (the listing is confined to the bases).
    Without: only a browser on this same computer."""
    if bases:
        return True
    return client_host in ("127.0.0.1", "::1", "localhost")


def _inside(path: str, base: str) -> bool:
    path, base = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(base))
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def _starts(bases: tuple) -> list:
    if bases:
        return [{"name": b, "path": b} for b in bases if os.path.isdir(b)]
    if os.name == "nt":
        drives = list(os.listdrives()) if hasattr(os, "listdrives") else [c + ":" + os.sep for c in string.ascii_uppercase
                                                                           if os.path.isdir(c + ":" + os.sep)]
        return [{"name": d, "path": d} for d in drives]
    start = [{"name": "/", "path": "/"}]
    home = os.path.expanduser("~")
    if os.path.isdir(home) and home != "/":
        start.insert(0, {"name": home, "path": home})
    return start


def browse(path: str, bases: tuple = ()) -> dict:
    """{'path': the folder, 'parent': the folder above it ('' = back to the start list), 'dirs': [{'name', 'path'}]}.
    An empty path lists the starting points (drives, or the bases). PermissionError for a folder outside the bases,
    FileNotFoundError for one that is not there."""
    if not path:
        return {"path": "", "parent": None, "dirs": _starts(bases)}
    real = os.path.realpath(path)
    if bases and not any(_inside(real, b) for b in bases):
        raise PermissionError("outside the folders this picker may show")
    if not os.path.isdir(real):
        raise FileNotFoundError(path)
    dirs = []
    try:
        with os.scandir(real) as it:
            for e in it:
                if len(dirs) >= MAX_ENTRIES:
                    break
                try:
                    if e.is_dir() and not e.name.startswith(".") and e.name.lower() not in _SKIP:
                        dirs.append({"name": e.name, "path": os.path.join(real, e.name)})
                except OSError:
                    continue
    except OSError as err:
        raise PermissionError(err.strerror or str(err))
    dirs.sort(key=lambda d: natural_key(d["name"]))
    parent = os.path.dirname(real.rstrip(os.sep)) if real.rstrip(os.sep) else ""
    if not parent or parent == real or (bases and not any(_inside(parent, b) for b in bases)):
        parent = ""
    return {"path": real, "parent": parent, "dirs": dirs}

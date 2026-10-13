"""Add and remove [[roots]] (the libraries) in config.toml, the same careful text-level way users are edited: everything else in the
file, comments included, stays as it was; every edit is parsed and validated BEFORE anything is written; the old file is kept as
config.toml.bak. Used by the admin page's Libraries tab.
"""
from __future__ import annotations

import os
import re

from .config import Config
from .useredit import EditError, _join, _NAME_LINE, _parse, _q, _TABLE, apply_edit, read_config_text, validate

NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _root_blocks(lines: list) -> list:
    heads = []
    for i, line in enumerate(lines):
        m = _TABLE.match(line)
        if m:
            heads.append((i, m.group(1) == "[[" and m.group(2).strip() == "roots"))
    blocks = []
    for k, (start, is_roots) in enumerate(heads):
        if not is_roots:
            continue
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        name = None
        for j in range(start + 1, end):
            m = _NAME_LINE.match(lines[j])
            if m:
                name = m.group(1) if m.group(1) is not None else m.group(2)
                break
        blocks.append({"start": start, "end": end, "name": name})
    return blocks


def _is_filler(line: str) -> bool:
    return not line.strip() or line.lstrip().startswith("#")


def _toml_string(value: str) -> str:
    """A literal string ('...') keeps Windows backslashes as they are; a basic string with escapes is the fallback."""
    if "'" in value or any(ord(c) < 32 for c in value):
        return _q(value)
    return "'%s'" % value


def check_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise EditError("a library name may only use letters, digits, - and _ (it becomes part of the address)")
    return name


def add_root_text(text: str, name: str, folder: str) -> str:
    name = check_name(name)
    folder = (folder or "").strip()
    if not folder or any(ord(c) < 32 for c in folder):
        raise EditError("give the folder of the library")
    lines, nl, trailing = _parse(text)
    blocks = _root_blocks(lines)
    if any(b["name"] == name for b in blocks):
        raise EditError("there is already a library named %r" % name)
    new = ["[[roots]]", "name = %s" % _q(name), "path = %s" % _toml_string(folder)]
    if blocks:
        last = blocks[-1]
        at = last["end"]
        while at > last["start"] + 1 and _is_filler(lines[at - 1]):      # keep comments that belong to what follows
            at -= 1
        lines[at:at] = [""] + new
    else:
        lines += ([""] if lines and lines[-1].strip() else []) + new
    return _join(lines, nl, trailing or True)


def remove_root_text(text: str, name: str) -> str:
    lines, nl, trailing = _parse(text)
    for b in _root_blocks(lines):
        if b["name"] == name:
            end = b["end"]
            while end > b["start"] + 1 and _is_filler(lines[end - 1]):   # a comment above the next block stays with it
                end -= 1
            start = b["start"]
            while start > 0 and not lines[start - 1].strip():             # and the blank line above this block goes with it
                start -= 1
            del lines[start:end]
            return _join(lines, nl, trailing)
    raise EditError("no library named %r" % name)


def check_folder(folder: str) -> str:
    """The folder must exist and be listable by this program (in Docker: as the container sees it)."""
    folder = (folder or "").strip()
    if not folder:
        raise EditError("give the folder of the library")
    if not os.path.isdir(folder):
        raise EditError("%s is not a folder this program can see (in Docker, use the path inside the container, such as /library/music)" % folder)
    try:
        with os.scandir(folder):
            pass
    except OSError as e:
        raise EditError("%s cannot be read: %s" % (folder, e.strerror or e))
    return folder


def add_root(path: str, name: str, folder: str) -> Config:
    name, folder = check_name(name), check_folder(folder)
    new = add_root_text(read_config_text(path), name, folder)
    return apply_edit(path, new, lambda c: name in c.roots and c.roots[name].path == folder)


def remove_root(path: str, name: str) -> Config:
    new = remove_root_text(read_config_text(path), name)
    try:
        return apply_edit(path, new, lambda c: name not in c.roots)
    except EditError as e:
        if "unknown root" in str(e):
            raise EditError("some users are limited to this library. Change their folders on the Users tab first (%s)" % e)
        raise

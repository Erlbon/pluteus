"""Add, change and remove [[users]] in config.toml without touching anything else in the file.

Text-level editing on purpose: a TOML writer would throw away the comments. Every edit is
re-parsed and validated (and checked to have done what was asked) BEFORE anything is written;
the file is then replaced atomically and the previous version kept as config.toml.bak.
Comments sitting above a removed [[users]] header are left in place.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import tomllib

from .config import ConfigError, Config, parse_config


class EditError(Exception):
    """The edit was refused; nothing was written."""


_TABLE = re.compile(r"^\s*(\[\[?)\s*([A-Za-z0-9_.\"' -]+?)\s*\]\]?\s*(#.*)?$")
_NAME_LINE = re.compile(r"""^\s*name\s*=\s*(?:"((?:[^"\\]|\\.)*)"|'([^']*)')\s*(?:#.*)?$""")
UNSET = object()


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _roots_line(roots) -> str:
    return "roots = [%s]" % ", ".join(_q(r) for r in roots)


def _parse(text: str):
    nl = "\r\n" if "\r\n" in text else "\n"
    return text.splitlines(), nl, text.endswith(("\n", "\r"))


def _join(lines: list, nl: str, trailing: bool) -> str:
    return nl.join(lines) + (nl if trailing and lines else "")


def _user_blocks(lines: list) -> list:
    heads = []
    for i, line in enumerate(lines):
        m = _TABLE.match(line)
        if m:
            heads.append((i, m.group(1) == "[[" and m.group(2).strip() == "users"))
    blocks = []
    for k, (start, is_users) in enumerate(heads):
        if not is_users:
            continue
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        name = None
        for j in range(start + 1, end):
            m = _NAME_LINE.match(lines[j])
            if m:
                try:
                    name = json.loads('"%s"' % m.group(1)) if m.group(1) is not None else m.group(2)
                except ValueError:
                    name = m.group(1)
                break
        blocks.append({"start": start, "end": end, "name": name})
    return blocks


def _find(lines: list, name: str) -> dict:
    for b in _user_blocks(lines):
        if b["name"] == name:
            return b
    raise EditError("no user named %r" % name)


def _value_end(body: list, i: int) -> int:
    """Index of the last line of the value that starts on body[i] (arrays may span lines)."""
    depth, j, first = 0, i, True
    while j < len(body):
        s = body[j]
        if first:
            s, first = s.split("=", 1)[1], False
        quote, k = None, 0
        while k < len(s):
            c = s[k]
            if quote:
                if c == "\\" and quote == '"':
                    k += 1
                elif c == quote:
                    quote = None
            elif c in "\"'":
                quote = c
            elif c == "#":
                break
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
            k += 1
        if depth <= 0:
            return j
        j += 1
    return len(body) - 1


def _set_key(body: list, key: str, new_line) -> list:
    """Replace, insert or (new_line None) delete one key inside a [[users]] block body."""
    pattern = re.compile(r"^\s*%s\s*=" % re.escape(key))
    for i, line in enumerate(body):
        if pattern.match(line):
            end = _value_end(body, i)
            return body[:i] + ([new_line] if new_line is not None else []) + body[end + 1:]
    if new_line is None:
        return body
    last = max((i for i, line in enumerate(body) if line.strip() and not line.lstrip().startswith("#")),
               default=-1)
    return body[:last + 1] + [new_line] + body[last + 1:]


# ---- text -> text operations ---------------------------------------------------
def add_user_text(text: str, name: str, password_hash: str, roots=(), adult: bool = False, upload: bool = True) -> str:
    lines, nl, trailing = _parse(text)
    if any(b["name"] == name for b in _user_blocks(lines)):
        raise EditError("user %r already exists" % name)
    block = ["[[users]]", "name = %s" % _q(name), "password_hash = %s" % _q(password_hash)]
    if roots:
        block.append(_roots_line(roots))
    if adult:
        block.append("adult = true")
    if not upload:
        block.append("upload = false")
    while lines and not lines[-1].strip():
        lines.pop()
    return _join(lines + ([""] if lines else []) + block, nl, True)


def set_user_text(text: str, name: str, *, password_hash=None, roots=UNSET, adult=None, upload=None) -> str:
    lines, nl, trailing = _parse(text)
    b = _find(lines, name)
    body = lines[b["start"] + 1:b["end"]]
    if password_hash is not None:
        body = _set_key(body, "password_hash", "password_hash = %s" % _q(password_hash))
    if roots is not UNSET:
        body = _set_key(body, "roots", _roots_line(roots) if roots else None)
    if adult is not None:
        body = _set_key(body, "adult", "adult = true" if adult else None)
    if upload is not None:                      # uploads are on by default, so only "off" is written
        body = _set_key(body, "upload", None if upload else "upload = false")
    return _join(lines[:b["start"] + 1] + body + lines[b["end"]:], nl, trailing)


def remove_user_text(text: str, name: str) -> str:
    lines, nl, trailing = _parse(text)
    b = _find(lines, name)
    rest = lines[:b["start"]] + lines[b["end"]:]
    i = b["start"]
    if 0 < i <= len(rest) and not rest[i - 1].strip() and (i == len(rest) or not rest[i].strip()):
        del rest[i - 1]
    return _join(rest, nl, trailing)


# ---- validation and saving -----------------------------------------------------
def validate(text: str) -> Config:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise EditError("the edit would produce invalid TOML (%s); nothing written" % e)
    try:
        return parse_config(data, check_roots=False)
    except ConfigError as e:
        raise EditError("%s; nothing written" % e)


def read_config_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            return f.read()
    except OSError as e:
        raise EditError("cannot read %s: %s" % (path, e))


def _save(path: str, text: str) -> None:
    # copyfile, not copy2: the backup needs the content only, and copying timestamps fails (EPERM) for a
    # non-root user on some bind mounts (found with Docker Desktop on Windows).
    shutil.copyfile(path, path + ".bak")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        try:
            shutil.copymode(path, tmp)      # mkstemp makes the file owner-only; keep the original's permissions
        except OSError:
            pass
        for attempt in range(8):            # Windows refuses to replace a file another process has open for a moment
            try:                            # (virus scanner, backup tool, the running server's reload poll)
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.15)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def apply_edit(path: str, new_text: str, check) -> Config:
    """Validate new_text, confirm check(cfg) holds (the edit really did what was asked), then save."""
    cfg = validate(new_text)
    if not check(cfg):
        raise EditError("internal check failed: the edit did not produce the expected user; nothing written")
    _save(path, new_text)
    return cfg


def add_user(path: str, name: str, password_hash: str, roots=(), adult: bool = False, upload: bool = True) -> Config:
    new = add_user_text(read_config_text(path), name, password_hash, tuple(roots), adult, upload)
    return apply_edit(path, new, lambda c: name in c.users and c.users[name].roots == tuple(roots)
                      and c.users[name].adult == adult and c.users[name].upload == upload
                      and c.users[name].password_hash == password_hash)


def set_user(path: str, name: str, *, password_hash=None, roots=UNSET, adult=None, upload=None) -> Config:
    text = read_config_text(path)
    before = validate(text).users.get(name)
    if before is None:
        raise EditError("no user named %r" % name)
    want_roots = before.roots if roots is UNSET else tuple(roots or ())
    want_adult = before.adult if adult is None else adult
    want_upload = before.upload if upload is None else upload
    want_hash = before.password_hash if password_hash is None else password_hash
    new = set_user_text(text, name, password_hash=password_hash, roots=roots, adult=adult, upload=upload)
    return apply_edit(path, new, lambda c: name in c.users and c.users[name].roots == want_roots
                      and c.users[name].adult == want_adult and c.users[name].upload == want_upload
                      and c.users[name].password_hash == want_hash)


def remove_user(path: str, name: str) -> Config:
    new = remove_user_text(read_config_text(path), name)
    return apply_edit(path, new, lambda c: name not in c.users)

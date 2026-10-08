"""Server configuration, loaded from a TOML file (see config.example.toml)."""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass

DEFAULT_EXCLUDES = ("$recycle.bin", "system volume information", "@eadir", "lost+found",
                    "thumbs.db", "desktop.ini")
DEFAULT_EXTENSIONS = (".epub", ".cbz", ".pdf")
# Leading lineage/DDC number, either "3.23 - " or "0. ": hidden in the display name only.
DEFAULT_DISPLAY_STRIP = r"^\d+(?:\.\d+)*(?: - |\.\s+)"

MIME_TYPES = {
    ".epub": "application/epub+zip",
    ".cbz": "application/vnd.comicbook+zip",
    ".pdf": "application/pdf",
}

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Root:
    name: str
    path: str


@dataclass(frozen=True)
class User:
    name: str
    password_hash: str
    roots: tuple = ()      # empty = every root
    adult: bool = False    # may see folders carrying the adult marker file

    def may_use(self, root_name: str) -> bool:
        return not self.roots or root_name in self.roots


@dataclass(frozen=True)
class Config:
    roots: dict
    users: dict
    host: str = "127.0.0.1"
    port: int = 8080
    page_size: int = 50
    base_path: str = ""
    display_strip: str = DEFAULT_DISPLAY_STRIP
    extensions: tuple = DEFAULT_EXTENSIONS
    excludes: frozenset = frozenset(DEFAULT_EXCLUDES)
    adult_marker: str = ".adult"
    realm: str = "bookshelf"
    auth_cache_seconds: int = 300
    anonymous_user: str = ""   # requests with NO credentials act as this user (off by default)
    metadata: bool = True      # read title/author/series from inside EPUBs
    cache_dir: str = ""        # folder for the SQLite metadata cache; "" = in memory only


def parse_config(data: dict, check_roots: bool = True) -> Config:
    from .fs import longpath

    server = data.get("server", {})
    roots = {}
    for r in data.get("roots", []):
        name, path = r.get("name", ""), r.get("path", "")
        if not _NAME_RE.match(name):
            raise ConfigError("root name %r must use only letters, digits, - and _" % name)
        if name in roots:
            raise ConfigError("duplicate root name %r" % name)
        if check_roots and not os.path.isdir(longpath(path)):
            raise ConfigError("root %r: %r is not a folder" % (name, path))
        roots[name] = Root(name, path)
    if not roots:
        raise ConfigError("no [[roots]] defined")

    users = {}
    for u in data.get("users", []):
        name = u.get("name", "")
        if not name or ":" in name:
            raise ConfigError("user name %r must be non-empty and contain no colon" % name)
        if name in users:
            raise ConfigError("duplicate user %r" % name)
        if not str(u.get("password_hash", "")).startswith("scrypt$"):
            raise ConfigError("user %r needs a password_hash (run: python -m bookshelf hash-password)" % name)
        allowed = tuple(u.get("roots", ()))
        for ar in allowed:
            if ar not in roots:
                raise ConfigError("user %r refers to unknown root %r" % (name, ar))
        users[name] = User(name, u["password_hash"], allowed, bool(u.get("adult", False)))
    if not users:
        raise ConfigError("no [[users]] defined")

    anonymous_user = str(server.get("anonymous_user", ""))
    if anonymous_user and anonymous_user not in users:
        raise ConfigError("anonymous_user %r is not a defined user" % anonymous_user)
    base_path = str(server.get("base_path", "")).rstrip("/")
    if base_path and not base_path.startswith("/"):
        raise ConfigError("base_path must start with /")
    extensions = tuple(e.lower() if e.startswith(".") else "." + e.lower()
                       for e in data.get("extensions", DEFAULT_EXTENSIONS))
    excludes = frozenset(n.lower() for n in (*DEFAULT_EXCLUDES, *data.get("exclude", ())))
    strip = data.get("display_strip", DEFAULT_DISPLAY_STRIP)
    try:
        re.compile(strip)
    except re.error as e:
        raise ConfigError("display_strip is not a valid regex: %s" % e)
    return Config(
        roots=roots, users=users,
        host=str(server.get("host", "127.0.0.1")), port=int(server.get("port", 8080)),
        page_size=int(server.get("page_size", 50)), base_path=base_path,
        display_strip=strip, extensions=extensions, excludes=excludes,
        adult_marker=str(data.get("adult_marker", ".adult")),
        realm=str(server.get("realm", "bookshelf")),
        auth_cache_seconds=int(server.get("auth_cache_seconds", 300)),
        anonymous_user=anonymous_user,
        metadata=bool(data.get("metadata", True)),
        cache_dir=str(data.get("cache_dir", "")),
    )


def load_config(path: str) -> Config:
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        raise ConfigError("cannot read %s: %s" % (path, e))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError("%s is not valid TOML: %s" % (path, e))
    return parse_config(data)

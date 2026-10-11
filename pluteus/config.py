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
    realm: str = "pluteus"
    auth_cache_seconds: int = 300
    anonymous_user: str = ""   # requests with NO credentials act as this user (off by default)
    metadata: bool = True      # read title/author/series from inside EPUBs
    cache_dir: str = ""        # folder for the SQLite metadata cache; "" = data_dir, else in memory only
    data_dir: str = ""         # folder for state.sqlite3 (sessions, reading progress); "" = in memory only
    themes_dir: str = ""       # folder holding user themes (themes_dir/<name>/theme.css)
    theme: str = "default"     # theme used until a user picks another
    session_days: int = 30     # login sessions last this long, renewed while in use
    cookie_secure: str = "auto"   # "auto": Secure cookie when the request is HTTPS; or "always" / "never"
    trust_proxy: bool = False  # believe X-Forwarded-For / X-Forwarded-Proto (only behind your own proxy)
    opds_pse: bool = True      # OPDS-PSE page streaming links for comics (reads each CBZ's page list once)


# TOML puts every key written after a [table] header INTO that table, so a setting typed under
# [[roots]] by mistake would silently do nothing. Every table's keys are therefore checked.
_SERVER_KEYS = {"host", "port", "page_size", "base_path", "realm", "auth_cache_seconds", "anonymous_user",
                "session_days", "cookie_secure", "trust_proxy", "opds_pse"}
_GLOBAL_KEYS = {"exclude", "extensions", "display_strip", "adult_marker", "metadata", "cache_dir",
                "data_dir", "themes_dir", "theme"}
_ROOT_KEYS = {"name", "path"}
_USER_KEYS = {"name", "password_hash", "roots", "adult"}


def _check_keys(table: dict, allowed: set, where: str) -> None:
    for key in table:
        if key not in allowed:
            hint = ""
            if key in _SERVER_KEYS | _GLOBAL_KEYS:
                hint = " (it is a setting: put it in [server], or above the first [table] header)"
            raise ConfigError("unknown key %r in %s%s" % (key, where, hint))


def parse_config(data: dict, check_roots: bool = True) -> Config:
    from .fs import longpath

    _check_keys(data, {"server", "roots", "users"} | _GLOBAL_KEYS, "the top level")
    server = data.get("server", {})
    _check_keys(server, _SERVER_KEYS | _GLOBAL_KEYS, "[server]")
    # Settings may live in [server] or above the first table header; [server] wins.
    opts = {**{k: data[k] for k in _GLOBAL_KEYS if k in data}, **server}
    roots = {}
    for r in data.get("roots", []):
        _check_keys(r, _ROOT_KEYS, "[[roots]] %r" % r.get("name", "?"))
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
        _check_keys(u, _USER_KEYS, "[[users]] %r" % u.get("name", "?"))
        name = u.get("name", "")
        if not name or ":" in name:
            raise ConfigError("user name %r must be non-empty and contain no colon" % name)
        if name in users:
            raise ConfigError("duplicate user %r" % name)
        if not str(u.get("password_hash", "")).startswith("scrypt$"):
            raise ConfigError("user %r needs a password_hash (run: python -m pluteus hash-password)" % name)
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
                       for e in opts.get("extensions", DEFAULT_EXTENSIONS))
    excludes = frozenset(n.lower() for n in (*DEFAULT_EXCLUDES, *opts.get("exclude", ())))
    strip = opts.get("display_strip", DEFAULT_DISPLAY_STRIP)
    try:
        re.compile(strip)
    except re.error as e:
        raise ConfigError("display_strip is not a valid regex: %s" % e)
    theme = str(opts.get("theme", "default"))
    if not re.match(r"^[A-Za-z0-9_-]{1,40}$", theme):
        raise ConfigError("theme %r must use only letters, digits, - and _" % theme)
    session_days = int(server.get("session_days", 30))
    if session_days < 1:
        raise ConfigError("session_days must be at least 1")
    secure = server.get("cookie_secure", "auto")
    secure = {True: "always", False: "never"}.get(secure, secure)
    if secure not in ("auto", "always", "never"):
        raise ConfigError('cookie_secure must be "auto", "always" or "never" (or true/false)')
    return Config(
        roots=roots, users=users,
        data_dir=str(opts.get("data_dir", "")), themes_dir=str(opts.get("themes_dir", "")), theme=theme,
        session_days=session_days, cookie_secure=secure, trust_proxy=bool(server.get("trust_proxy", False)),
        opds_pse=bool(server.get("opds_pse", True)),
        host=str(server.get("host", "127.0.0.1")), port=int(server.get("port", 8080)),
        page_size=int(server.get("page_size", 50)), base_path=base_path,
        display_strip=strip, extensions=extensions, excludes=excludes,
        adult_marker=str(opts.get("adult_marker", ".adult")),
        realm=str(server.get("realm", "pluteus")),
        auth_cache_seconds=int(server.get("auth_cache_seconds", 300)),
        anonymous_user=anonymous_user,
        metadata=bool(opts.get("metadata", True)),
        cache_dir=str(opts.get("cache_dir", "")),
    )


def load_config(path: str, check_roots: bool = True) -> Config:
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        raise ConfigError("cannot read %s: %s" % (path, e))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError("%s is not valid TOML: %s" % (path, e))
    return parse_config(data, check_roots)

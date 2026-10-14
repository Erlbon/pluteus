"""Server configuration, loaded from a TOML file (see config.example.toml)."""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass

DEFAULT_EXCLUDES = ("$recycle.bin", "system volume information", "@eadir", "lost+found",
                    "thumbs.db", "desktop.ini")
DEFAULT_EXTENSIONS = (".epub", ".cbz", ".cbr", ".cb7", ".cbt", ".pdf")
# Leading lineage/DDC number, either "3.23 - " or "0. ": hidden in the display name only.
DEFAULT_DISPLAY_STRIP = r"^\d+(?:\.\d+)*(?: - |\.\s+)"

MIME_TYPES = {
    ".epub": "application/epub+zip",
    ".cbz": "application/vnd.comicbook+zip",
    ".cbr": "application/vnd.comicbook-rar",
    ".cb7": "application/x-cb7",
    ".cbt": "application/x-cbt",
    ".pdf": "application/pdf",
}

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ConfigError(Exception):
    pass


ALL_ROOTS = "*"      # in a user's roots list: every root, including ones added later


@dataclass(frozen=True)
class Root:
    name: str
    path: str
    notes: str = ""        # "edit": users with the notes right may write a folder's note file (artist.md ...) into this library, for everyone


@dataclass(frozen=True)
class User:
    name: str
    password_hash: str
    roots: tuple = ()      # the roots this user may open; ("*",) = every root; empty = none (a new user starts with none)
    adult: bool = False    # may see folders carrying the adult marker file
    upload: bool = True    # may use the personal upload folder (when uploads_dir is set)
    admin: bool = False    # may sign in to the admin page (admin_port); never given by default
    read_only: bool = False  # set only on a login made with the user's app key: reading and listing, nothing else (see auth.py)
    notes: bool = False    # may write shared notes into the libraries that allow it (notes = "edit" on the root)

    def may_use(self, root_name: str) -> bool:
        return ALL_ROOTS in self.roots or root_name in self.roots


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
    private_marker: str = ".private"   # folder (and below) only for the users named inside the file
    hidden_marker: str = ".hidden"     # folder left out of listings and feeds (a direct link still works)
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
    allowed_hosts: tuple = ()  # extra host names a browser may use (CSRF check), e.g. when a proxy rewrites Host
    opds_pse: bool = True      # OPDS-PSE page streaming links for comics (reads each CBZ's page list once)
    show_all_files: bool = True    # list and open every file; False = only the types in `extensions` (book server)
    text_view_kb: int = 1024       # the text viewer shows at most this much of a file (the rest is a download)
    admin_port: int = 0            # the admin page listens here (0 = off). NEVER route or forward this port to the internet
    admin_host: str = "127.0.0.1"  # the address it listens on ("0.0.0.0" inside Docker, then publish the port on the LAN only)
    library_bases: tuple = ()      # the admin page's folder picker lists folders only below these (empty: anywhere, but only for a browser on this same computer)
    admin_url: str = ""            # where the admin page is, for the Admin link administrators see even behind a proxy (e.g. http://pluteus.lan:8101/)
    join_token: str = ""           # set only by `pluteus share --private`: /join/<token> signs a visitor in as join_user
    join_user: str = ""
    share_target: bool = True      # the installed app appears in the Android Share menu (needs uploads_dir); false = leave it out of the manifest
    webdav: bool = False           # read-only WebDAV under /dav (mount the library in Explorer, Finder, file managers)
    thumbnails: bool = False       # cover thumbnails (/thumb): in the OPDS feeds and the web cover view. Off until switched on
    kosync: bool = False           # KOReader progress sync under /kosync
    subsonic: bool = False       # answer the Subsonic / OpenSubsonic API under /rest (music apps, Android Auto through them)
    relay: bool = False            # the stream relay under /relay: radio stations a browser cannot play directly (plain http on an https site, no CORS) go through the server
    relay_streams_user: int = 2    # most streams one user may have open through the relay at once
    relay_streams_total: int = 6   # most streams open through the relay at once, all users together
    relay_key: str = ""            # the key that encrypts stored logins and headers for protected streams; "" = pluteus makes one in the data folder (relay.key)
    update_check: bool = False     # the admin page asks GitHub once a day for a newer version and security advisories (opt-in)
    update_repo: str = "Erlbon/pluteus"   # owner/name of the repository to ask
    site_port: int = 0             # port of the main site as seen from the LAN when it differs from `port` (Docker: 8100); 0 = port
    uploads_dir: str = ""          # one subfolder per user lives here; "" = uploads switched off
    upload_max_mb: int = 4096      # largest single upload
    upload_quota_mb: int = 0       # most a user may keep in their upload folder; 0 = no limit


# TOML puts every key written after a [table] header INTO that table, so a setting typed under
# [[roots]] by mistake would silently do nothing. Every table's keys are therefore checked.
_SERVER_KEYS = {"host", "port", "page_size", "base_path", "realm", "auth_cache_seconds", "anonymous_user",
                "session_days", "cookie_secure", "trust_proxy", "allowed_hosts", "opds_pse", "show_all_files", "text_view_kb",
                "admin_port", "admin_host", "site_port", "update_check", "update_repo", "share_target", "join_token", "join_user", "admin_url", "library_bases", "subsonic", "webdav", "kosync", "thumbnails", "relay", "relay_streams_user", "relay_streams_total", "relay_key"}
_GLOBAL_KEYS = {"exclude", "extensions", "display_strip", "adult_marker", "private_marker", "hidden_marker", "metadata", "cache_dir",
                "data_dir", "themes_dir", "theme", "uploads_dir", "upload_max_mb", "upload_quota_mb"}
_ROOT_KEYS = {"name", "path", "notes"}
_USER_KEYS = {"name", "password_hash", "roots", "adult", "upload", "admin", "notes"}
UPLOADS_ROOT = "uploads"          # the virtual root every user sees for their own upload folder
_FOLDER_NAME = re.compile(r"^[A-Za-z0-9_-](?:[A-Za-z0-9 _.-]*[A-Za-z0-9_-])?$")
_DEVICE_NAMES = {"con", "prn", "aux", "nul", *("com%d" % i for i in range(1, 10)), *("lpt%d" % i for i in range(1, 10))}


def _is_inside(path: str, root: str) -> bool:
    p = os.path.normcase(os.path.abspath(path))
    r = os.path.normcase(os.path.abspath(root))
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def _in_range(table: dict, key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(table.get(key, default))
    except (TypeError, ValueError):
        raise ConfigError("%s must be a whole number" % key)
    if not low <= value <= high:
        raise ConfigError("%s must be between %d and %d" % (key, low, high))
    return value


def _check_uploads(uploads_dir: str, roots: dict, users: dict, opts: dict) -> None:
    """Every user's upload folder is uploads_dir/<user name>, so the folder must be separate from everything else
    pluteus serves or keeps, and user names must be usable (and distinct) as Windows folder names."""
    for r in roots.values():
        if _is_inside(uploads_dir, r.path) or _is_inside(r.path, uploads_dir):
            raise ConfigError("uploads_dir %r and root %r overlap: uploads must live in their own folder, outside "
                              "the library" % (uploads_dir, r.name))
    for label in ("data_dir", "cache_dir", "themes_dir"):
        value = str(opts.get(label, ""))
        if value and _is_inside(value, uploads_dir):
            raise ConfigError("%s %r is inside uploads_dir %r: users could then list pluteus's own files"
                              % (label, value, uploads_dir))
    seen = {}
    for name, user in users.items():
        if not user.upload:
            continue
        if not _FOLDER_NAME.match(name) or name.split(".")[0].lower() in _DEVICE_NAMES:
            raise ConfigError("user name %r cannot be used with uploads_dir: it becomes a folder name, so use letters, "
                              "digits, space, - _ . only (no leading/trailing dot or space, no CON/NUL/COM1...). "
                              "Rename the user, or set upload = false for them" % name)
        if name.lower() in seen:
            raise ConfigError("users %r and %r would share one upload folder (Windows ignores letter case)"
                              % (seen[name.lower()], name))
        seen[name.lower()] = name


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
        if name == UPLOADS_ROOT:
            raise ConfigError("the root name %r is reserved for the personal upload folders" % name)
        if check_roots and not os.path.isdir(longpath(path)):
            raise ConfigError("root %r: %r is not a folder" % (name, path))
        notes = str(r.get("notes", ""))
        if notes not in ("", "edit"):
            raise ConfigError("root %r: notes must be \"edit\" or left out" % name)
        roots[name] = Root(name, path, notes)
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
        if ALL_ROOTS in allowed and len(allowed) > 1:
            raise ConfigError("user %r: \"*\" (every root) cannot be combined with root names" % name)
        for ar in allowed:
            if ar != ALL_ROOTS and ar not in roots:
                raise ConfigError("user %r refers to unknown root %r" % (name, ar))
        users[name] = User(name, u["password_hash"], allowed, bool(u.get("adult", False)), bool(u.get("upload", True)),
                           bool(u.get("admin", False)), False, bool(u.get("notes", False)))
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
    if bool(server.get("show_all_files", True)):
        # Every file under a root is listed and downloadable in this mode, so pluteus's own files (the sessions in
        # state.sqlite3!) must never sit inside one.
        for label in ("data_dir", "cache_dir", "themes_dir"):
            value = str(opts.get(label, ""))
            for r in roots.values():
                if value and _is_inside(value, r.path):
                    raise ConfigError("%s %r is inside root %r: with show_all_files its files would be listed and "
                                      "downloadable. Move it outside the library (or set show_all_files = false)"
                                      % (label, value, r.name))
    admin_port = _in_range(server, "admin_port", 0, 0, 65535)
    if admin_port:
        if not any(u.admin for u in users.values()):
            raise ConfigError("admin_port is set but no user has admin = true: nobody could sign in to the admin page")
        if admin_port == _in_range(server, "port", 8080, 1, 65535):
            raise ConfigError("admin_port must differ from port")
    uploads_dir = str(opts.get("uploads_dir", ""))
    if uploads_dir:
        _check_uploads(uploads_dir, roots, users, opts)
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
        allowed_hosts=tuple(str(h).strip().lower() for h in server.get("allowed_hosts", ()) if str(h).strip()),
        opds_pse=bool(server.get("opds_pse", True)),
        show_all_files=bool(server.get("show_all_files", True)),
        text_view_kb=max(16, int(server.get("text_view_kb", 1024))),
        admin_port=admin_port, admin_host=str(server.get("admin_host", "127.0.0.1")),
        site_port=_in_range(server, "site_port", 0, 0, 65535),
        relay=bool(server.get("relay", False)), relay_streams_user=_in_range(server, "relay_streams_user", 2, 1, 50),
        relay_streams_total=_in_range(server, "relay_streams_total", 6, 1, 500), relay_key=str(server.get("relay_key", "")),
        library_bases=tuple(str(b) for b in server.get("library_bases", ())), admin_url=_admin_url(server.get("admin_url", "")), join_token=str(server.get("join_token", "")), join_user=str(server.get("join_user", "")),
        share_target=bool(server.get("share_target", True)), subsonic=bool(server.get("subsonic", False)), webdav=bool(server.get("webdav", False)), kosync=bool(server.get("kosync", False)), thumbnails=bool(server.get("thumbnails", False)), update_check=bool(server.get("update_check", False)), update_repo=_repo_name(server.get("update_repo", "Erlbon/pluteus")),
        host=str(server.get("host", "127.0.0.1")), port=_in_range(server, "port", 8080, 1, 65535),
        page_size=_in_range(server, "page_size", 50, 1, 1000), base_path=base_path,
        display_strip=strip, extensions=extensions, excludes=excludes,
        adult_marker=str(opts.get("adult_marker", ".adult")),
        private_marker=str(opts.get("private_marker", ".private")),
        hidden_marker=str(opts.get("hidden_marker", ".hidden")),
        realm=str(server.get("realm", "pluteus")),
        auth_cache_seconds=int(server.get("auth_cache_seconds", 300)),
        anonymous_user=anonymous_user,
        metadata=bool(opts.get("metadata", True)),
        cache_dir=str(opts.get("cache_dir", "")),
        uploads_dir=uploads_dir,
        upload_max_mb=_in_range(opts, "upload_max_mb", 4096, 1, 10_000_000),
        upload_quota_mb=_in_range(opts, "upload_quota_mb", 0, 0, 100_000_000),
    )


def _repo_name(value) -> str:
    from .updatecheck import REPO_RE
    if not isinstance(value, str) or not REPO_RE.fullmatch(value):
        raise ConfigError("update_repo must look like owner/name, got %r" % (value,))
    return value


def _admin_url(value) -> str:
    value = str(value or "").strip()
    if value and not re.fullmatch(r"https?://[A-Za-z0-9.\[\]:-]+(/[A-Za-z0-9._~/-]*)?", value):
        raise ConfigError("admin_url must look like http://host:8101/ (letters, digits, dots, colons and dashes), got %r" % value)
    return value


def users_without_folders(cfg: Config) -> list:
    """Names of users who can sign in but may open no folder (no roots list): usually a config written for the older
    rule where 'no list' meant every folder. Reported at start-up and by check-config."""
    return [u.name for u in cfg.users.values() if not u.roots]


def load_config(path: str, check_roots: bool = True) -> Config:
    try:
        with open(path, "rb") as f:
            raw = f.read()
        data = tomllib.loads(raw.decode("utf-8-sig"))     # -sig: Windows editors and PowerShell often add a BOM
    except OSError as e:
        raise ConfigError("cannot read %s: %s" % (path, e))
    except UnicodeDecodeError as e:
        raise ConfigError("%s is not UTF-8 text: %s" % (path, e))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError("%s is not valid TOML: %s" % (path, e))
    cfg = parse_config(data, check_roots)
    if cfg.uploads_dir and _is_inside(path, cfg.uploads_dir):
        raise ConfigError("the config file %r is inside uploads_dir: users could list it (it holds password hashes)" % path)
    if cfg.show_all_files:
        for r in cfg.roots.values():
            if _is_inside(path, r.path):
                raise ConfigError("the config file %r is inside root %r: with show_all_files it would be listed and "
                                  "downloadable (it holds password hashes). Move it out of the library" % (path, r.name))
    return cfg

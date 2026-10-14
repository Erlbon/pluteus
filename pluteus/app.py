"""The Starlette application: routes, identity (session cookie or HTTP Basic), error mapping.

Who is asking is decided in this order: a valid session cookie (browser), HTTP Basic credentials (reader
apps), then the optional anonymous user. Browsers without a login are redirected to /login; reader apps and
API calls get a plain 401 so they can show their own login prompt.
"""
from __future__ import annotations

import contextvars
import functools
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import threading
import time
from types import SimpleNamespace
from pathlib import Path
from html import escape as escape_attr
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from python_multipart.exceptions import MultipartParseError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse,
                                 RedirectResponse, Response, StreamingResponse)
from starlette.routing import Route

from . import cbz, kinds, markdown, mediainfo, opds, opds2, radio, stationsave, subtitles, tabular, thumbs, textview, uploads, web, ziputil
from .auth import Authenticator
from .bodies import read_limited
from .cbz import CbzError
from .config import MIME_TYPES, UPLOADS_ROOT, Config
from .fs import SORTS, Library, NotFound, PathError, clamp_time, sort_entries
from .meta import MetaCache, MetaService
from .ratelimit import FailureLimiter
from .reload import UsersReloader
from .sessions import COOKIE, DEVICE_COOKIE, DEVICE_DAYS, SessionManager
from .state import StateDB
from .themes import DEFAULT, Themes
from .urls import Urls, with_page

log = logging.getLogger("pluteus")
STATIC_DIR = Path(__file__).resolve().parent / "static"
_PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded", "via")
_PLAIN_HOST = re.compile(r"[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\]")       # a host name or address, nothing that could break out of a link
current_admin_link: contextvars.ContextVar = contextvars.ContextVar("current_admin_link", default="")   # set per request
# What the Android share sheet may hand over. Explicit types and extensions, not "*/*": some Chrome versions refuse to build the
# installed app (WebAPK) from a share target that accepts everything.
SHARE_ACCEPT = ["image/*", "video/*", "audio/*", "text/*", "application/*", ".epub", ".cbz", ".cbr", ".cb7", ".cbt", ".m4b"]
SERVICE_WORKER = chr(10).join([
    "// pluteus: does nothing but make the site installable as an app. No caching; every request goes to the network as usual.",
    "self.addEventListener('install', function () { self.skipWaiting(); });",
    "self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });",
    "self.addEventListener('fetch', function () { /* no respondWith: the browser handles the request itself */ });",
    ""])
STATIC_ALLOWED ={".css", ".js", ".mjs", ".svg", ".png", ".ico", ".woff2", ".webmanifest", ".pfb", ".ttf", ".wasm"}
# Browsers refuse an ES module that is not served as JavaScript, and WebAssembly only compiles when it is served as
# application/wasm; mimetypes knows neither everywhere.
STATIC_TYPES = {".mjs": "text/javascript", ".pfb": "application/octet-stream", ".wasm": "application/wasm"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "PROPFIND"}       # PROPFIND (WebDAV) only reads
CSP = ("default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline' blob:; img-src 'self' data: blob:; "
       "font-src 'self' data: blob:; connect-src 'self'; frame-src 'self' blob:; object-src 'none'; "
       "base-uri 'self'; form-action 'self'; frame-ancestors 'self'")
# the radio page plays streams from other servers (the addresses come from files in the library), so only it may load media from anywhere
RADIO_CSP = CSP.replace("default-src 'self';", "default-src 'self'; media-src 'self' http: https:;", 1)
# the live-channel page also fetches the stream itself (hls.js, no worker) and plays it from a media source
IPTV_CSP = (RADIO_CSP.replace("media-src 'self' http: https:;", "media-src 'self' blob: http: https:;", 1)
            .replace("connect-src 'self';", "connect-src 'self' http: https:;", 1))
NO_CACHE = {"Cache-Control": "no-cache"}


_SECRET_QUERY = {"p", "apikey", "t", "s", "password", "token", "key", "secret"}
access_log = logging.getLogger("pluteus.access")


def _logged_target(scope) -> str:
    """The request line for the access log with every secret removed: Subsonic apps send p= (the key, or even the password),
    apiKey=, t= and s= in the address, and /join/TOKEN is a one-time access link."""
    path = scope.get("path", "")
    if "/join/" in path:
        path = path.split("/join/")[0] + "/join/***"
    query = scope.get("query_string", b"").decode("latin-1")
    if not query:
        return path
    shown = []
    for pair in query.split("&"):
        name, sep, value = pair.partition("=")
        shown.append(name + sep + ("***" if sep and name.lower() in _SECRET_QUERY else value))
    return path + "?" + "&".join(shown)


class AccessLog:
    """One line per request (client, request, status) with secrets removed; replaces uvicorn's own access log, which prints the
    full query string."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        status = [0]

        async def wrapped(message):
            if message["type"] == "http.response.start":
                status[0] = message["status"]
            await send(message)
        try:
            await self.app(scope, receive, wrapped)
        finally:
            client = scope.get("client")
            access_log.info('%s - "%s %s" %s', client[0] if client else "-", scope.get("method", "-"), _logged_target(scope), status[0])


def csp_for_page() -> str:
    """The content policy of a page. An administrator who is shown the Admin link also lets the page ask that one address whether it
    is reachable (adminlink.js); nothing else may be fetched from another origin."""
    link = current_admin_link.get()
    if link:
        parts = urlsplit(link)
        if parts.scheme in ("http", "https") and parts.netloc:
            return CSP.replace("connect-src 'self';", "connect-src 'self' %s://%s;" % (parts.scheme, parts.netloc), 1)
    return CSP


class SecurityHeaders:
    """Adds the same hardening headers to every response without buffering it (downloads stream)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def wrapped(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "same-origin")
                headers.setdefault("X-Frame-Options", "SAMEORIGIN")
                if headers.get("content-type", "").startswith("text/html"):
                    headers.setdefault("Content-Security-Policy", csp_for_page())
            await send(message)

        await self.app(scope, receive, wrapped)


class StripBase:
    """Serve under a URL prefix whether the reverse proxy removes it or not: a request that still carries the
    prefix has it taken off, so routes always see plain paths and every link we emit carries the prefix."""

    def __init__(self, app, base: str):
        self.app, self.base = app, base
        self.state = app.state

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and self.base:
            path = scope.get("path", "")
            if path == self.base or path.startswith(self.base + "/"):
                scope = dict(scope)
                scope["path"] = path[len(self.base):] or "/"
                raw = scope.get("raw_path")
                if raw and raw.startswith(self.base.encode()):
                    scope["raw_path"] = raw[len(self.base):] or b"/"
        await self.app(scope, receive, send)


def _parts(path: str) -> tuple:
    path = path.rstrip("/")
    return tuple(path.split("/")) if path else ()


def _page_number(request: Request) -> int:
    try:
        return int(request.query_params.get("page", "1"))
    except ValueError:
        return 1


def _safe_next(target: str, base: str, fallback: str) -> str:
    """Only a path on this server may be used as a post-login destination (no open redirects)."""
    if (not target or not target.startswith("/") or target.startswith("//") or "\\" in target
            or "\r" in target or "\n" in target):
        return fallback
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return fallback
    if base and not (target == base or target.startswith(base + "/")):
        return fallback
    return target


def too_big(request: Request, limit: int) -> bool:
    """True when the client announces a body above `limit` bytes, before any of it is read into memory."""
    try:
        return int(request.headers.get("content-length", "0")) > limit
    except ValueError:
        return True


def _form(body: bytes) -> dict:
    """application/x-www-form-urlencoded -> {name: first value}. Bounded; no multipart dependency."""
    if len(body) > 16384:
        return {}
    return {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items()}


def create_app(cfg: Config, config_path: str | None = None, reload_interval: float = 2.0) -> Starlette:
    lib = Library(cfg)
    meta = MetaService(MetaCache(cfg.cache_dir or cfg.data_dir), enabled=cfg.metadata)
    state = StateDB(cfg.data_dir)
    auth = Authenticator(cfg.users, cfg.auth_cache_seconds, appkeys=state.get_appkey)
    sessions = SessionManager(state, cfg.session_days)
    limiter = FailureLimiter()
    themes = Themes(cfg.themes_dir)
    reloader = UsersReloader(config_path, auth, reload_interval, on_config=lambda c: lib.set_roots(c.roots)) if config_path else None
    urls = Urls(cfg.base_path)
    thumb_cache = thumbs.ThumbCache(cfg.cache_dir or cfg.data_dir)
    thumb_gate = threading.BoundedSemaphore(3)
    backup_dir = os.path.join(cfg.data_dir, "backups") if cfg.data_dir else ""
    housekeeping_due = [0.0]
    challenge = {"WWW-Authenticate": 'Basic realm="%s", charset="UTF-8"' % cfg.realm}
    cookie_path = cfg.base_path or "/"

    # ---- identity ---------------------------------------------------------------
    def client_ip(request: Request) -> str:
        if cfg.trust_proxy:
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded:
                # The LAST entry is the one our own proxy appended; earlier ones are whatever the client claimed.
                return forwarded.split(",")[-1].strip()
        return request.client.host if request.client else "?"

    def limiter_ip(request: Request) -> str:
        """The address the login throttle counts against. Behind a proxy that hides the real client (Docker Desktop
        makes every visitor look like one private address), counting by address would let a few bad guesses from
        anywhere lock EVERYONE out, so a private address from a trusted proxy is not counted; the per-name limit
        still protects every account."""
        ip = client_ip(request)
        if cfg.trust_proxy:
            try:
                if not ipaddress.ip_address(ip).is_global:
                    return ""
            except ValueError:
                return ""
        return ip

    def cookie_secure(request: Request) -> bool:
        if cfg.cookie_secure != "auto":
            return cfg.cookie_secure == "always"
        scheme = request.url.scheme
        if cfg.trust_proxy:
            scheme = request.headers.get("x-forwarded-proto", scheme).split(",")[0].strip()
        return scheme == "https"

    def csrf_ok(request: Request) -> bool:
        """State-changing requests must come from this site (Origin, else Referer, must match Host)."""
        if request.method in SAFE_METHODS:
            return True
        origin = request.headers.get("origin") or request.headers.get("referer") or ""
        if not origin:
            return False
        accepted = {request.headers.get("host", "").lower(), *cfg.allowed_hosts}
        if cfg.trust_proxy:                       # a proxy that rewrote Host says what the browser really used
            accepted.add(request.headers.get("x-forwarded-host", "").split(",")[0].strip().lower())
        accepted.discard("")
        return urlsplit(origin).netloc.lower() in accepted

    def device_state(request: Request, username: str):
        """(known, device id, last address): has this browser logged in as `username` before (it still has its device
        cookie and the password has not changed since)? Known devices are not stopped by a lock on the account."""
        found = sessions.device_for(request.cookies.get(DEVICE_COOKIE), username, auth.users)
        return (True, found[0], found[1]) if found else (False, "", "")

    def note_failure(name: str, ip: str, device_id: str, known: bool) -> None:
        seconds, number = limiter.failure(name, ip, device=device_id, known=known)
        if seconds:                                    # one line per new lock, so a guessing attack is visible in the log
            log.warning("login for %r is locked for %d s after repeated wrong passwords (lock number %d)",
                        (name or "")[:40], seconds, number)

    async def resolve(request: Request):
        """(user or None, seconds to wait when throttled). Password hashing and the hourly housekeeping run on
        worker threads, never on the event loop."""
        if reloader:
            reloader.maybe_reload()
        now = time.monotonic()
        if now >= housekeeping_due[0]:
            housekeeping_due[0] = now + 3600
            await run_in_threadpool(state.housekeeping, backup_dir)
            await run_in_threadpool(state.purge_appkeys, set(auth.users))
        user = sessions.user_for(request.cookies.get(COOKIE), auth.users)
        if user:
            return user, 0
        header = request.headers.get("authorization")
        if header:
            user = auth.cached_user(header)
            if user:
                return user, 0
            if auth.known_bad(header):                  # the same wrong credentials again: no hashing, no new strike
                return None, 0
            creds = auth.parse_basic(header)
            name, ip = (creds[0] if creds else ""), limiter_ip(request)
            known, device_id, _ = device_state(request, name)
            wait = limiter.wait_seconds(name, ip, device=device_id, known=known)
            if wait:
                # Locked for strangers. Credentials that were verified earlier (a reader app that has logged in before)
                # still work: only someone who already knows the password can send them.
                trusted = auth.trusted_user(header)
                return (trusted, 0) if trusted else (None, wait)
            user = await run_in_threadpool(auth.authenticate, header)
            if user:
                limiter.success(name, device_id)
                return user, 0
            note_failure(name, ip, device_id, known)
            return None, 0
        if cfg.anonymous_user:
            return auth.users.get(cfg.anonymous_user), 0
        return None, 0

    def theme_for(username: str) -> str:
        choice = state.get_prefs(username).get("theme") if username else None
        for name in (choice, cfg.theme):
            if name and themes.exists(name):
                return name
        return DEFAULT

    def admin_link_for(request: Request, user) -> str:
        """Where the admin page is, for an administrator who opened the site directly on the local network (same host name
        or address, the admin port). Empty for everyone else, and always empty through a proxy: the admin page is not
        reachable from outside, so the public site does not even hint at it."""
        if user.admin and cfg.admin_url:                    # the owner said where it is: shown to administrators wherever they come from
            return cfg.admin_url
        if not (cfg.admin_port and user.admin) or any(h in request.headers for h in _PROXY_HEADERS):
            return ""
        host = request.headers.get("host", "")
        host = host.rsplit(":", 1)[0] if not host.endswith("]") else host            # drop the port (a bracketed IPv6 has none after it)
        if not _PLAIN_HOST.fullmatch(host):
            return ""
        try:
            if not (request.client and ipaddress.ip_address(request.client.host).is_private):
                return ""
        except ValueError:
            return ""
        return "http://%s:%d/" % (host, cfg.admin_port)

    def icons_for(username: str) -> str:
        choice = state.get_prefs(username).get("icons") if username else None
        return choice if choice in ("gold", "aurora", "color") else "gold"

    def view_for(username: str) -> str:
        return "grid" if state.get_prefs(username).get("view") == "grid" else "list"

    def ctx_for(user, view: str = "") -> web.Ctx:
        return web.Ctx(urls, user.name, theme_for(user.name), readers=True, admin_link=current_admin_link.get(),
                       icons=icons_for(user.name), view=(view or view_for(user.name)) if cfg.thumbnails else "list",
                       thumbs=cfg.thumbnails,
                       extras=state.get_prefs(user.name).get("player_extras", True) is not False)

    def unauthorized(request: Request, mode: str, wait: int) -> Response:
        if wait:
            return PlainTextResponse("Too many failed attempts. Try again in %d seconds." % wait, 429,
                                     headers={"Retry-After": str(wait)})
        if mode in ("api", "media"):
            # 'media' is for files that reader apps fetch (page streaming, inline media): challenge-driven apps only
            # send credentials after a Basic challenge. 'api' (progress) is for our own scripts and never challenges,
            # and a browser that still holds a session cookie never gets a pop-up either.
            return JSONResponse({"error": "login required"}, 401,
                                headers=challenge if mode == "media" and not request.cookies.get(COOKIE) else None)
        if mode == "web" and "text/html" in request.headers.get("accept", ""):
            here = cfg.base_path + request.url.path + ("?" + request.url.query if request.url.query else "")
            return RedirectResponse(urls.login(here), 303)
        return PlainTextResponse("Authentication required", 401, headers=challenge)

    def guarded(mode: str, from_browser_navigation: bool = False):
        """mode: 'web' (browser pages), 'api' (JSON), 'basic' (reader apps and downloads).
        from_browser_navigation: the POST may come from the browser itself, not a page of ours (the Android share
        sheet), which sends no Origin; Sec-Fetch-Site then vouches that no other website started it."""
        def decorate(fn):
            @functools.wraps(fn)
            async def wrapper(request: Request):
                user, wait = await resolve(request)
                if user is None:
                    return unauthorized(request, mode, wait)
                if not csrf_ok(request) and not (
                        from_browser_navigation and request.headers.get("sec-fetch-site") in ("same-origin", "none")):
                    log.warning("refused %s %s as cross-site: origin=%r host=%r sec-fetch-site=%r", request.method,
                                request.url.path, request.headers.get("origin"), request.headers.get("host"),
                                request.headers.get("sec-fetch-site"))
                    return PlainTextResponse("Cross-site request refused", 403)
                if user.read_only and request.method not in SAFE_METHODS:
                    return PlainTextResponse("This login (an app key) can only read", 403)
                current_admin_link.set(admin_link_for(request, user))
                try:
                    return await fn(request, user)
                except PathError:
                    return PlainTextResponse("Bad path", 400)
                except NotFound:
                    return PlainTextResponse("Not found", 404)
                except FileNotFoundError:               # the file vanished between the check and the read
                    return PlainTextResponse("Not found", 404)
                except OSError:                         # share offline, permission denied, ...: not the caller's fault
                    return PlainTextResponse("The library storage is not available right now.", 503,
                                             headers={"Retry-After": "30"})
            return wrapper
        return decorate

    # ---- public: health, assets, login -----------------------------------------
    async def healthz(request: Request):
        return PlainTextResponse("ok")

    def _file(base: Path, rel: str):
        parts = rel.split("/")
        if any(p in ("", ".", "..") or "\\" in p or ":" in p for p in parts):
            return None
        if os.path.splitext(parts[-1])[1].lower() not in STATIC_ALLOWED:
            return None
        real = base.joinpath(*parts).resolve()
        return real if real.is_file() and base.resolve() in real.parents else None

    def revalidated(request: Request, path: Path) -> Response:
        """Serve a small asset with no-cache + ETag: always revalidated, and answered 304 when unchanged,
        so an edited theme file shows on the next reload yet unchanged ones cost no download."""
        st = path.stat()
        etag = '"%s"' % hashlib.md5(("%s-%s" % (st.st_mtime_ns, st.st_size)).encode(), usedforsecurity=False).hexdigest()
        sent = {t.strip().removeprefix("W/") for t in request.headers.get("if-none-match", "").split(",")}
        if etag in sent:
            return Response(status_code=304, headers={"ETag": etag, **NO_CACHE})
        return FileResponse(path, media_type=STATIC_TYPES.get(path.suffix.lower()), headers={"ETag": etag, **NO_CACHE})

    async def static(request: Request):
        path = _file(STATIC_DIR, request.path_params["path"])
        if path is None or request.path_params["path"].startswith("themes/"):
            return PlainTextResponse("Not found", 404)
        return revalidated(request, path)

    async def favicon(request: Request):
        # Some clients ask for /favicon.ico whatever the page says.
        return revalidated(request, STATIC_DIR / "icon" / "favicon.ico")

    async def theme_file(request: Request):
        name = request.path_params["name"]
        if name == "_":
            # "_" means: the theme this browser's user has chosen (so icon rules need no theme name in them)
            who = sessions.user_for(request.cookies.get(COOKIE), auth.users)
            name = theme_for(who.name) if who else (cfg.theme if themes.exists(cfg.theme) else DEFAULT)
        path = themes.resolve(name, request.path_params["path"])
        if path is None:
            return PlainTextResponse("Not found", 404)
        response = revalidated(request, path)
        if request.path_params["name"] == "_":
            response.headers["Vary"] = "Cookie"
        return response

    def login_ctx() -> web.Ctx:
        return web.Ctx(urls, "", cfg.theme if themes.exists(cfg.theme) else DEFAULT)

    async def login_form(request: Request):
        # Only a real session counts here, so an anonymous_user setup can still reach the login form.
        user = sessions.user_for(request.cookies.get(COOKIE), auth.users)
        next_url = _safe_next(request.query_params.get("next", ""), cfg.base_path, urls.home())
        if user:
            return RedirectResponse(next_url, 303)
        return HTMLResponse(web.login_page(login_ctx(), next_url))

    async def login_submit(request: Request):
        if not csrf_ok(request):
            return PlainTextResponse("Cross-site request refused", 403)
        raw_form = await read_limited(request, 16384)
        if raw_form is None:
            return PlainTextResponse("Request too large", 413)
        form = _form(raw_form)
        username, password = form.get("username", ""), form.get("password", "")
        next_url = _safe_next(form.get("next", ""), cfg.base_path, urls.home())
        ip = limiter_ip(request)
        known, device_id, last_ip = device_state(request, username)
        wait = limiter.wait_seconds(username, ip, device=device_id, known=known)
        if wait:
            return HTMLResponse(web.login_page(login_ctx(), next_url,
                                                "Too many failed attempts. Try again in %d seconds." % wait,
                                                username), 429, headers={"Retry-After": str(wait)})
        user = await run_in_threadpool(auth.check, username, password)
        if user is None:
            note_failure(username, ip, device_id, known)
            return HTMLResponse(web.login_page(login_ctx(), next_url, "Wrong user name or password.",
                                               username), 401)
        limiter.success(username, device_id)
        token = sessions.start(user)
        real_ip = client_ip(request)
        device_token, _ = sessions.remember_device(request.cookies.get(DEVICE_COOKIE), user, real_ip)
        if known and last_ip and real_ip != last_ip:
            log.info("a known device of %r signed in from a new address", user.name)
        response = RedirectResponse(next_url, 303)
        secure = cookie_secure(request)
        response.set_cookie(COOKIE, token, max_age=cfg.session_days * 86400, path=cookie_path,
                            httponly=True, samesite="lax", secure=secure)
        response.set_cookie(DEVICE_COOKIE, device_token, max_age=DEVICE_DAYS * 86400, path=cookie_path,
                            httponly=True, samesite="lax", secure=secure)
        return response

    async def logout(request: Request):
        if not csrf_ok(request):
            return PlainTextResponse("Cross-site request refused", 403)
        sessions.end(request.cookies.get(COOKIE))
        response = RedirectResponse(urls.login(), 303)
        response.delete_cookie(COOKIE, path=cookie_path)
        return response

    # ---- browser pages ------------------------------------------------------------
    def continue_reading(user) -> list:
        """The files this user left unfinished, newest first: link, title, fraction, kind (for the icon) and where it
        lives (the library and the folder it is in, e.g. 'comics / Ablaze')."""
        out = []
        for r in state.recent_progress(user.name, 40):
            kind = kinds.kind_of(r["path"])
            if lib.has_root(user, r["root"]) and r["percent"] < 0.98 \
                    and kind in kinds.KINDS_WITH_PROGRESS:
                parts = tuple(r["path"].split("/"))
                try:                                   # skip books that are now hidden, excluded or gone
                    lib.file_info(user, r["root"], parts, cfg.show_all_files)
                except (NotFound, PathError, OSError):
                    continue
                link = urls.read(r["root"], parts) if kind in ("epub", "comic") else urls.view(r["root"], parts)
                parent = lib.display_name(parts[-2]) if len(parts) > 1 else ""
                out.append({"href": link, "label": os.path.splitext(parts[-1])[0], "percent": r["percent"], "kind": kind,
                            "where": r["root"] + (" / " + parent if parent else ""),
                            "dismiss": urls.dismiss(r["root"], parts)})
        return out[:6]

    def shelf_entries(user, shelf: str, limit: int = 60) -> list:
        """[(row, parts, FileInfo or None)] of a shelf that this user can still open (moved, deleted or hidden ones are skipped,
        not removed). None for a folder. Stops after `limit` usable entries: a long shelf costs no more than a short one."""
        out = []
        for row in state.shelf_list(user.name, shelf):
            if len(out) >= limit:
                break
            parts = tuple(row["path"].split("/"))
            if not lib.has_root(user, row["root"]):
                continue
            try:
                if row["is_dir"]:
                    lib.folder_path(user, row["root"], parts)
                    out.append((row, parts, None))
                else:
                    out.append((row, parts, lib.file_info(user, row["root"], parts, cfg.show_all_files)))
            except (NotFound, PathError, OSError):
                continue
        return out

    def shelf_items(user, shelf: str) -> list:
        """The shelf as the home page shows it."""
        out = []
        for row, parts, _ in shelf_entries(user, shelf):
            kind = "folder" if row["is_dir"] else kinds.kind_of(parts[-1])
            if row["is_dir"]:
                href = urls.browse(row["root"], parts)
            else:
                href = urls.read(row["root"], parts) if kind in ("epub", "comic") else urls.view(row["root"], parts)
            parent = lib.display_name(parts[-2]) if len(parts) > 1 else ""
            out.append({"href": href, "label": lib.display_name(parts[-1]) if row["is_dir"] else os.path.splitext(parts[-1])[0],
                        "kind": kind, "where": row["root"] + (" / " + parent if parent else ""),
                        "api": urls.shelf(shelf, row["root"], parts)})
        return out[:60]

    @guarded("web")
    async def home(request: Request, user):
        recent = await run_in_threadpool(continue_reading, user)
        read_next = await run_in_threadpool(shelf_items, user, "next")
        favourites = await run_in_threadpool(shelf_items, user, "favourite")
        return HTMLResponse(web.roots_page(lib.roots_for(user), ctx_for(user), recent, read_next, favourites))

    @guarded("api")
    async def shelf(request: Request, user):
        """Put a file or folder on a shelf (POST; ?move=up|down reorders the "next" list) or take it off (DELETE)."""
        if user.read_only:
            return JSONResponse({"error": "this login can only read"}, 403)
        name = request.path_params["shelf"]
        root = request.path_params["root"]
        parts = _parts(request.path_params["path"])
        if name not in state.SHELVES or not parts:
            raise NotFound("no such shelf")
        path = "/".join(parts)
        if request.method == "DELETE":
            await run_in_threadpool(state.shelf_remove, user.name, name, root, path)
            return JSONResponse({"ok": True})
        move = request.query_params.get("move", "")
        if move:
            if move not in ("up", "down") or name != "next":
                return JSONResponse({"error": "cannot move"}, 400)
            return JSONResponse({"ok": await run_in_threadpool(state.shelf_move, user.name, name, root, path, move == "up")})

        def check():
            try:
                lib.file_info(user, root, parts, cfg.show_all_files)
                return False
            except NotFound:
                lib.folder_path(user, root, parts)                  # not a file: it must be a folder this user may open
                return True
        is_dir = await run_in_threadpool(check)
        if not await run_in_threadpool(state.shelf_add, user.name, name, root, path, is_dir):
            return JSONResponse({"error": "this shelf is full"}, 400)
        return JSONResponse({"ok": True}, 201)

    @guarded("web")
    async def dismiss(request: Request, user):
        """Take a file off the home page list. No file is touched: only this user's list changes, and the saved position
        stays (the file is listed again when it is next opened)."""
        root = request.path_params["root"]
        parts = _parts(request.path_params["path"])
        if not parts or not lib.has_root(user, root):
            raise NotFound("no such entry")
        await run_in_threadpool(state.dismiss_progress, user.name, root, "/".join(parts))
        return RedirectResponse(urls.home(), 303)

    @guarded("web")
    async def browse(request: Request, user):
        root = request.path_params["root"]
        parts = _parts(request.path_params.get("path", ""))
        sort = request.query_params.get("sort", "name")
        sort = sort if sort in SORTS else "name"
        desc = request.query_params.get("dir") == "desc"
        # Size and date of every file are only read when the sort needs them; otherwise only for the page that is shown
        # (one system call per file: about 0.7 ms each across a Docker bind mount, so 900 files would cost 0.6 s).
        entries = await run_in_threadpool(lib.list_dir, user, root, parts, cfg.show_all_files, sort in ("size", "date"))
        if sort != "name" or desc:
            entries = sort_entries(entries, sort, desc)
        shown, page, pages = opds.paginate(entries, _page_number(request), cfg.page_size)
        shown = await run_in_threadpool(lib.with_stats, shown)
        metas = await run_in_threadpool(meta.lookup_many, shown)
        book_paths = ["/".join(e.parts) for e in shown if not e.is_dir]
        progress = await run_in_threadpool(state.progress_for, user.name, root, book_paths)
        flags = await run_in_threadpool(state.shelf_flags, user.name, root, ["/".join(e.parts) for e in shown])
        def folder_picture():
            try:
                return mediainfo.folder_cover(lib.folder_path(user, root, parts))
            except (NotFound, PathError, OSError):
                return ""
        has_cover = await run_in_threadpool(folder_picture)
        asked = request.query_params.get("view", "")
        asked = asked if asked in ("list", "grid") else ""
        return HTMLResponse(web.listing_page(
            root, parts, shown, page, pages, len(entries), ctx_for(user, asked), metas, progress, sort=sort, desc=desc,
            writable=root == UPLOADS_ROOT, flash=web.flash_text(request.query_params),
            cover_url=urls.cover(root, parts) if (has_cover and parts) else "", view_asked=asked, shelves=flags))

    @guarded("web")
    async def settings(request: Request, user):
        if request.method == "POST":
            raw_form = await read_limited(request, 16384)
            if raw_form is None:
                return PlainTextResponse("Request too large", 413)
            form = _form(raw_form)
            choice, icons = form.get("theme", ""), form.get("icons", "")
            prefs = dict(state.get_prefs(user.name))
            if themes.exists(choice):
                prefs["theme"] = choice
            if icons in ("gold", "aurora", "color"):
                prefs["icons"] = icons
            if form.get("view") in ("list", "grid"):
                prefs["view"] = form["view"]
            if form.get("extras") in ("show", "hide"):
                prefs["player_extras"] = form["extras"] == "show"
            state.set_prefs(user.name, prefs)
            return RedirectResponse(urls.settings() + "?saved=1", 303)
        return HTMLResponse(web.settings_page(ctx_for(user), themes.names(), "saved" in request.query_params,
                                              stations=lib.uploads_enabled(user)))

    # ---- personal upload folder: upload, new folder, rename, delete -----------------------------------------------
    def flash_url(parts: tuple, **query) -> str:
        return urls.browse(UPLOADS_ROOT, parts) + "?" + urlencode({k: v for k, v in query.items() if v not in ("", None)})

    async def accept_upload(request: Request, user, parts: tuple) -> Response:
        """Stream the files of a multipart POST into the folder `parts` of the user's own upload area."""
        folder = await run_in_threadpool(lib.upload_dir, user, parts)
        max_bytes = cfg.upload_max_mb * 1024 * 1024
        quota_left = None
        if cfg.upload_quota_mb:
            used = await run_in_threadpool(uploads.tree_size, lib.upload_base(user))
            quota_left = max(0, cfg.upload_quota_mb * 1024 * 1024 - used)
        if too_big(request, max_bytes * uploads.MAX_FILES):
            return PlainTextResponse("Upload too large", 413)
        wants_json = "application/json" in request.headers.get("accept", "")
        try:
            intake = await uploads.receive(request.stream(), request.headers.get("content-type", ""), folder,
                                           max_bytes=max_bytes, quota_left=quota_left, is_hidden=lib.is_hidden_name)
        except uploads.UploadError:
            return PlainTextResponse("Send the files as multipart/form-data", 415)
        except (MultipartParseError, ValueError):
            return PlainTextResponse("The upload could not be read", 400)
        first_bad = intake.failed[0][1] if intake.failed else ""
        if wants_json:
            return JSONResponse({
                "saved": [{"name": n, "size": s} for n, s in intake.saved],
                "failed": [{"name": n, "code": c, "message": web.UPLOAD_MESSAGES.get(c, "")} for n, c in intake.failed]})
        return RedirectResponse(flash_url(parts, saved=len(intake.saved) or "", bad=first_bad), 303)

    @guarded("web")
    async def upload(request: Request, user):
        if request.path_params["root"] != UPLOADS_ROOT:
            raise NotFound("uploads go to the upload folder only")
        return await accept_upload(request, user, _parts(request.path_params.get("path", "")))

    @guarded("web", from_browser_navigation=True)
    async def share(request: Request, user):
        """Android 'Share to pluteus' (the PWA share target): files land in the top of the user's upload folder."""
        return await accept_upload(request, user, ())

    @guarded("web")
    async def file_op(request: Request, user):
        op = request.path_params["op"]
        if request.path_params["root"] != UPLOADS_ROOT or op not in ("mkdir", "rename", "delete"):
            raise NotFound("no such operation")
        parts = _parts(request.path_params.get("path", ""))
        raw_form = await read_limited(request, 16384)
        if raw_form is None:
            return PlainTextResponse("Request too large", 413)
        form = _form(raw_form)
        try:
            if op == "mkdir":
                folder = await run_in_threadpool(lib.upload_dir, user, parts)
                await run_in_threadpool(uploads.make_folder, folder, form.get("name", ""), lib.is_hidden_name)
                return RedirectResponse(flash_url(parts, ok="folder"), 303)
            folder, name = await run_in_threadpool(lib.upload_entry, user, parts)
            if op == "rename":
                await run_in_threadpool(uploads.rename_entry, folder, name, form.get("name", ""), lib.is_hidden_name)
                return RedirectResponse(flash_url(parts[:-1], ok="renamed"), 303)
            await run_in_threadpool(uploads.delete_entry, folder, name)
            return RedirectResponse(flash_url(parts[:-1], ok="deleted"), 303)
        except uploads.UploadError as e:
            return RedirectResponse(flash_url(parts if op == "mkdir" else parts[:-1], bad=e.code), 303)

    async def manifest(request: Request):
        data = {"id": urls.home(), "name": "pluteus", "short_name": "pluteus", "description": "Filesystem-first book and comic server",
                "start_url": urls.home(), "scope": urls.home(), "display": "standalone",
                "background_color": "#14171f", "theme_color": "#14171f",
                "icons": [{"src": urls.static("icon/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
                          {"src": urls.static("icon/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
                          {"src": urls.static("icon/icon-192-maskable.png"), "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
                          {"src": urls.static("icon/icon-512-maskable.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]}
        if cfg.uploads_dir and cfg.share_target:           # installed on Android, pluteus then appears in the Share menu
            data["share_target"] = {"action": urls.share(), "method": "POST", "enctype": "multipart/form-data",
                                    "params": {"files": [{"name": "files", "accept": SHARE_ACCEPT}]}}
        return JSONResponse(data, media_type="application/manifest+json", headers=NO_CACHE)

    async def join(request: Request):
        """`pluteus share --private`: the QR code carries /join/<token>. The right token signs the visitor in as the share's guest
        user and moves on to the home page (so the token does not stay in the address bar). Anything else looks like a missing page."""
        user = auth.users.get(cfg.join_user) if cfg.join_token else None
        if user is None or not hmac.compare_digest(request.path_params["token"].encode(), cfg.join_token.encode()):
            return PlainTextResponse("Not found", 404)
        response = RedirectResponse(urls.home(), 303)
        response.set_cookie(COOKIE, sessions.start(user), max_age=cfg.session_days * 86400, path=cookie_path,
                            httponly=True, samesite="lax", secure=cookie_secure(request))
        return response

    async def service_worker(request: Request):
        """A service worker that does nothing: it caches nothing and answers no request itself. It exists only so that Chrome
        on Android offers 'Install app' (and then the Share target). Served from the site's own prefix so its scope is the site."""
        return Response(SERVICE_WORKER, media_type="text/javascript", headers=NO_CACHE)

    @guarded("web")
    async def read(request: Request, user):
        root = request.path_params["root"]
        parts = _parts(request.path_params["path"])
        info = await run_in_threadpool(lib.file_info, user, root, parts, cfg.show_all_files)   # exists, allowed, not hidden
        key = hashlib.sha1(("%s-%s" % (info.size, info.mtime)).encode()).hexdigest()[:12]
        if kinds.kind_of(info.name) == "comic":
            try:
                pages = await run_in_threadpool(meta.pages_for, info, root != UPLOADS_ROOT)
            except CbzError as e:
                return PlainTextResponse("This comic cannot be opened (%s). You can still download it." % e, 422)
            return HTMLResponse(web.comic_reader_page(
                ctx_for(user), os.path.splitext(info.name)[0], pages_url=urls.cbz(root, parts), count=len(pages),
                progress_url=urls.api("progress", root, parts), back_url=urls.browse(root, parts[:-1]), key=key))
        if not info.name.lower().endswith(".epub"):
            return PlainTextResponse("There is no in-browser reader for this file type.", 404)
        book = await run_in_threadpool(meta.for_file, info)
        title = book.get("title") or os.path.splitext(info.name)[0]
        return HTMLResponse(web.epub_reader_page(
            ctx_for(user), title, ", ".join(book.get("authors", ())),
            book_url=urls.download(root, parts), progress_url=urls.api("progress", root, parts),
            back_url=urls.browse(root, parts[:-1]), content_css_url=urls.theme("_", "reader-content.css"), key=key))

    # ---- the universal viewer: /view picks the viewer, /raw serves the few types that are safe inline ----------------
    def neighbours(user, root: str, parts: tuple, kind: str):
        """(previous entry, next entry, 1-based position, total) among the same kind of file in this folder."""
        entries = lib.list_dir(user, root, parts[:-1], True, False)                         # names and kinds only
        same = [e for e in entries if not e.is_dir and kinds.kind_of(e.name) == kind]
        names = [e.name for e in same]
        i = names.index(parts[-1]) if parts[-1] in names else 0
        return (same[i - 1] if i > 0 else None, same[i + 1] if i + 1 < len(same) else None, i + 1, len(same))

    def text_page(user, root: str, parts: tuple, info, forced: bool):
        raw, truncated = textview.read_head(info.path, cfg.text_view_kb * 1024)
        binary = textview.looks_binary(raw)
        if binary and not forced:
            return None
        text, encoding = textview.decode(raw, truncated, force=binary or forced)
        return web.text_viewer_page(ctx_for(user), info.name, text=text, encoding=encoding, size=info.size,
                                    shown=len(raw), truncated=truncated, download_url=urls.download(root, parts),
                                    back_url=urls.browse(root, parts[:-1]), forced=binary or forced)

    def decoded_head(info):
        """(text, size shown, truncated) for a text-like file, or None when it looks binary."""
        raw, truncated = textview.read_head(info.path, cfg.text_view_kb * 1024)
        if textview.looks_binary(raw):
            return None
        return textview.decode(raw, truncated)[0], len(raw), truncated

    def markdown_view(user, root: str, parts: tuple, info):
        head = decoded_head(info)
        if head is None:
            return None
        text, shown, truncated = head
        try:
            html = markdown.render(text, urls, root, parts[:-1])
        except markdown.TooLarge:
            return None                                       # too big to render: the plain text viewer takes over
        return web.markdown_page(ctx_for(user), info.name, html=html, size=info.size, shown=shown, truncated=truncated,
                                 download_url=urls.download(root, parts), back_url=urls.browse(root, parts[:-1]),
                                 source_url=urls.view(root, parts) + "?as=text")

    def table_view(user, root: str, parts: tuple, info):
        head = decoded_head(info)
        if head is None:
            return None
        text, shown, truncated = head
        try:
            table = tabular.parse(text, kinds.extension(info.name))
        except tabular.TableError:
            return None                                       # not a table after all: shown as text
        return web.table_page(ctx_for(user), info.name, table=table, size=info.size, shown=shown, truncated=truncated,
                              download_url=urls.download(root, parts), back_url=urls.browse(root, parts[:-1]),
                              source_url=urls.view(root, parts) + "?as=text")

    def zip_view(user, root: str, parts: tuple, info):
        ctx, back, download = ctx_for(user), urls.browse(root, parts[:-1]), urls.download(root, parts)
        try:
            entries, total, comment = ziputil.list_entries(info.path)
        except ziputil.ZipError as e:
            rows = [("Name", info.name), ("Size", web._size(info.size))]
            return web.info_page(ctx, info.name, rows=rows, download_url=download, back_url=back,
                                 note="This zip file cannot be opened: %s." % e, text_url=urls.view(root, parts) + "?as=text")
        return web.zip_page(ctx, info.name, root=root, parts=parts, entries=entries, total=total, comment=comment,
                            size=info.size, download_url=download, back_url=back)

    def radio_view(user, root: str, parts: tuple, info):
        """The radio player for a .m3u / .pls / .url file that holds at least one http(s) stream; None for any other file."""
        head = decoded_head(info)
        stations = radio.parse(head[0]) if head else []
        if not stations:
            return None
        page = web.iptv_page if radio.looks_like_tv(head[0], stations) else web.radio_page
        html = page(ctx_for(user), info.name, stations=stations, download_url=urls.download(root, parts),
                    text_url=urls.view(root, parts) + "?as=text", back_url=urls.browse(root, parts[:-1]))
        return html, (IPTV_CSP if page is web.iptv_page else RADIO_CSP)

    def view_page(user, root: str, parts: tuple, info, kind: str, as_text: bool):
        ctx, back, download = ctx_for(user), urls.browse(root, parts[:-1]), urls.download(root, parts)
        if not as_text and kind == "markdown":
            page = markdown_view(user, root, parts, info)
            if page is not None:
                return page
        if not as_text and kind == "table":
            page = table_view(user, root, parts, info)
            if page is not None:
                return page
        if not as_text and kind == "archive":
            return zip_view(user, root, parts, info)
        if not as_text and kind in ("image", "audio", "video"):
            prev, nxt, position, total = neighbours(user, root, parts, kind)
            prev_url = urls.view(root, prev.parts) if prev else None
            next_url = urls.view(root, nxt.parts) if nxt else None
            if kind == "image":
                return web.image_viewer_page(
                    ctx, info.name, raw_url=urls.raw(root, parts), download_url=download, back_url=back,
                    prev_url=prev_url, next_url=next_url, next_raw=urls.raw(root, nxt.parts) if nxt else None,
                    position=position, total=total)
            tags = mediainfo.read_info(info.path, with_cover=True)
            lyrics = mediainfo.find_lrc(info.path)
            if not lyrics and len(mediainfo.parse_lrc(tags.get("lyrics", ""))) >= 3:      # timed lyrics stored inside the tag
                lyrics = mediainfo.parse_lrc(tags["lyrics"])
            has_cover = bool(tags.get("has_cover")) or (kind == "audio" and bool(mediainfo.folder_cover(os.path.dirname(info.path))))
            chapters = mediainfo.read_chapters(info.path)
            tracks = [(urls.subs(root, parts, i), label) for i, (_, label) in enumerate(subtitles.find(info.path))] if kind == "video" else []
            return web.media_viewer_page(
                ctx, info.name, kind=kind, raw_url=urls.raw(root, parts), mime=kinds.INLINE_MIME[kinds.extension(info.name)],
                download_url=download, back_url=back, progress_url=urls.api("progress", root, parts),
                prev_url=prev_url, next_url=next_url, position=position, total=total,
                album=lib.display_name(parts[-2]) if len(parts) > 1 else root,
                tags=tags, cover_url=urls.cover(root, parts) if has_cover else "", synced=lyrics, chapters=chapters,
                bookmarks_url=urls.api("bookmarks", root, parts), extras=ctx.extras, subtitles=tracks,
                queue_url=urls.api("queue", root, parts[:-1]) if kind == "audio" else "")
        if not as_text and kind == "pdf":
            return web.pdf_viewer_page(ctx, info.name, raw_url=urls.raw(root, parts), download_url=download, back_url=back,
                                       progress_url=urls.api("progress", root, parts))
        # text: known text types, anything that looks like text, or whatever the user asked to see as text
        page = text_page(user, root, parts, info, as_text) if (
            as_text or kind in ("text", "other", "markdown", "table")) else None
        if page is not None:
            return page
        rows = [("Name", info.name), ("Type", info.mime), ("Size", web._size(info.size)),
                ("Modified", time.strftime("%Y-%m-%d %H:%M", time.localtime(clamp_time(info.mtime))))]
        note = "This looks like a binary file." if kind in ("text", "other", "markdown", "table") else ""
        return web.info_page(ctx, info.name, rows=rows, download_url=download, back_url=back, note=note,
                             text_url=urls.view(root, parts) + "?as=text")

    @guarded("web")
    async def view(request: Request, user):
        root = request.path_params["root"]
        parts = _parts(request.path_params["path"])
        info = await run_in_threadpool(lib.file_info, user, root, parts, cfg.show_all_files)
        kind = kinds.kind_of(info.name)
        if kind in ("epub", "comic"):
            return RedirectResponse(urls.read(root, parts), 303)
        as_text = request.query_params.get("as") == "text"
        if not as_text and kind == "text" and radio.is_radio_file(info.name):         # a station file opens in the radio player
            found = await run_in_threadpool(radio_view, user, root, parts, info)
            if found is not None:
                return HTMLResponse(found[0], headers={"Content-Security-Policy": found[1]})
        page = await run_in_threadpool(view_page, user, root, parts, info, kind, as_text)
        return HTMLResponse(page)

    @guarded("media")
    async def raw(request: Request, user):
        """Inline display for the types we chose to allow, each with the type WE state; never anything else."""
        root = request.path_params["root"]
        parts = _parts(request.path_params["path"])
        info = await run_in_threadpool(lib.file_info, user, root, parts, cfg.show_all_files)
        ext = kinds.extension(info.name)
        mime = kinds.INLINE_MIME.get(ext)
        if mime is None:
            raise NotFound("not shown inline")
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"}
        if ext in kinds.SANDBOXED:
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
        return FileResponse(info.path, media_type=mime, headers=headers)       # no filename: displayed, not downloaded

    @guarded("web")
    async def stations_page(request: Request, user):
        """The radio picker or the TV picker (static/pickers): finds stations in a public catalogue and saves them into the user's
        upload folder. Served here (not as a static file) so its addresses can be filled in and its own content policy set."""
        kind = request.path_params["kind"]
        if kind not in stationsave.KINDS or not lib.uploads_enabled(user):
            raise NotFound("no such page")
        text = (STATIC_DIR / "pickers" / (kind + "-picker.html")).read_text(encoding="utf-8")
        for key, value in (("__STATIC__", urls.static("pickers/")), ("__VENDOR__", urls.static("vendor/")), ("__SAVE__", urls.stations_save()),
                           ("__BROWSE__", urls.browse(UPLOADS_ROOT, (stationsave.KINDS[kind],))), ("__BACK__", urls.settings())):
            text = text.replace(key, escape_attr(value))
        csp = IPTV_CSP if kind == "tv" else RADIO_CSP.replace("connect-src 'self';", "connect-src 'self' https://*.api.radio-browser.info;", 1)
        return HTMLResponse(text, headers={"Content-Security-Policy": csp})

    @guarded("web")
    async def stations_save(request: Request, user):
        """Write the ticked stations as .m3u files into the user's upload folder (Radio/ or IPTV/)."""
        if not lib.uploads_enabled(user):
            raise NotFound("no upload folder")
        raw = await read_limited(request, 400_000)
        if raw is None:
            return JSONResponse({"error": "That is too many at once."}, 413)
        try:
            data = json.loads(raw.decode("utf-8"))
            result = await run_in_threadpool(stationsave.save, lib, cfg.upload_quota_mb, user, data)
        except (ValueError, UnicodeDecodeError):
            return JSONResponse({"error": "The request was not understood."}, 400)
        except uploads.UploadError as e:
            return JSONResponse({"error": "Not saved: %s." % web.UPLOAD_MESSAGES.get(e.code, "something went wrong")}, 400)
        return JSONResponse(result)

    @guarded("web")
    async def shell(request: Request, user):
        """The outer page with the player and the site in a frame. ?go= is where the frame starts: a path on this site only."""
        go = request.query_params.get("go", "")
        ok = (go.startswith(urls.base + "/") and not go.startswith(urls.base + "//") and not go.startswith(urls.shell())
              and chr(92) not in go and all(c >= " " for c in go))
        return HTMLResponse(web.shell_page(ctx_for(user), go if ok else urls.home()))

    @guarded("web")
    async def queue(request: Request, user):
        """Every audio file of a folder and the folders below it, in play order, for the player in the shell."""
        root = request.path_params["root"]
        parts = _parts(request.path_params.get("path", ""))

        def build():
            found, visited = [], [0]

            def walk(here: tuple, depth: int):
                if depth > 6 or visited[0] >= 600 or len(found) >= 2000:
                    return
                visited[0] += 1
                entries = lib.list_dir(user, root, here, True, False)
                for e in entries:
                    if not e.is_dir and kinds.kind_of(e.name) == "audio" and len(found) < 2000:
                        found.append({"title": os.path.splitext(e.name)[0], "url": urls.raw(root, e.parts),
                                      "view": urls.view(root, e.parts), "folder": lib.display_name(here[-1]) if here else root})
                for e in entries:
                    if e.is_dir:
                        walk(e.parts, depth + 1)
            walk(parts, 0)
            return found, len(found) >= 2000 or visited[0] >= 600
        found, truncated = await run_in_threadpool(build)
        return JSONResponse({"items": found, "truncated": truncated})

    @guarded("media")
    async def subs(request: Request, user):
        """Subtitle number i of a video (a .srt or .vtt next to it), as WebVTT. Chosen by number, never by a name."""
        root = request.path_params["root"]
        parts = _parts(request.path_params["path"])
        info = await run_in_threadpool(lib.file_info, user, root, parts, cfg.show_all_files)
        if kinds.kind_of(info.name) != "video":
            raise NotFound("not a video")
        try:
            index = int(request.query_params.get("i", "0"))
        except ValueError:
            raise NotFound("no such subtitle")
        found = await run_in_threadpool(subtitles.find, info.path)
        if not 0 <= index < len(found):
            raise NotFound("no such subtitle")
        text = await run_in_threadpool(subtitles.to_vtt, found[index][0])
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300",
                   "Content-Security-Policy": "sandbox; default-src 'none'"}
        return Response(text, media_type="text/vtt; charset=utf-8", headers=headers)

    @guarded("media")
    async def cover(request: Request, user):
        """The picture of an audio or video file: the one inside it, else cover.jpg / folder.jpg of its folder."""
        root = request.path_params["root"]
        parts = _parts(request.path_params["path"])
        def find():
            try:                                                # a file: its own picture, else its folder's
                info = lib.file_info(user, root, parts, cfg.show_all_files)
                return mediainfo.read_cover(info.path) if kinds.kind_of(info.name) in ("audio", "video") else None
            except NotFound:                                    # a folder: folder.jpg, cover.jpg ...
                return mediainfo.read_folder_cover(lib.folder_path(user, root, parts))
        found = await run_in_threadpool(find)
        if found is None:
            raise NotFound("no cover")
        data, mime = found
        etag = '"%s"' % hashlib.sha1(data).hexdigest()[:20]
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400", "ETag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(data, media_type=mime, headers=headers)

    def attachment(name: str) -> str:
        """Content-Disposition for a download of something named by an archive: a safe local name, never the entry's own."""
        safe = uploads.sanitize_name(name) or "file"
        ascii_name = safe.encode("ascii", "replace").decode("ascii").replace("?", "_").replace('"', "_")
        return 'attachment; filename="%s"; filename*=UTF-8\'\'%s' % (ascii_name, quote(safe))

    @guarded("web")
    async def zip_entry(request: Request, user):
        """One entry of a zip file: as=dl downloads it, as=raw shows an image, as=text opens the text viewer.
        The entry is chosen by its number (i), never by a name, and everything is size-checked while it is read."""
        root = request.path_params["root"]
        parts = _parts(request.path_params["path"])
        info = await run_in_threadpool(lib.file_info, user, root, parts, cfg.show_all_files)
        if kinds.kind_of(info.name) != "archive":
            raise NotFound("not a zip file")
        try:
            index = int(request.query_params.get("i", ""))
        except ValueError:
            raise PathError("i must be a number")
        mode = request.query_params.get("as", "dl")
        try:
            facts = await run_in_threadpool(ziputil.entry_facts, info.path, index)
            base = facts["name"].replace(chr(92), "/").rsplit("/", 1)[-1]
            if mode == "dl":
                if facts["size"] > ziputil.MAX_DOWNLOAD_BYTES:
                    return PlainTextResponse("That entry is too large to download through the page.", 413)
                return StreamingResponse(ziputil.stream_entry(info.path, index), media_type="application/octet-stream",
                                         headers={"Content-Disposition": attachment(base),
                                                  "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
            if mode == "raw":
                ext = kinds.extension(base)
                if ext not in kinds.IMAGE:
                    raise NotFound("not an image")
                data = await run_in_threadpool(ziputil.read_entry, info.path, index, ziputil.MAX_IMAGE_BYTES)
                headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"}
                if ext in kinds.SANDBOXED:
                    headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
                return Response(data, media_type=kinds.IMAGE[ext], headers=headers)
            if mode == "text":
                data, cut = await run_in_threadpool(ziputil.read_head, info.path, index, cfg.text_view_kb * 1024)
                if textview.looks_binary(data):
                    return PlainTextResponse("That entry looks like a binary file: download it instead.", 415)
                text, encoding = textview.decode(data, cut)
                return HTMLResponse(web.text_viewer_page(
                    ctx_for(user), base, text=text, encoding=encoding, size=facts["size"], shown=len(data), truncated=cut,
                    download_url=urls.zip_entry(root, parts, index, "dl"), back_url=urls.view(root, parts), forced=False))
            raise PathError("unknown mode")
        except ziputil.ZipError as e:
            return PlainTextResponse(str(e).capitalize() + ".", 422)

    @guarded("media")
    async def cbz_page(request: Request, user):
        """One page of a comic, read straight out of the archive. 0-based: ?page=0 is the first page."""
        root = request.path_params["root"]
        parts = _parts(request.path_params["path"])
        try:
            number = int(request.query_params.get("page", "0"))
        except ValueError:
            raise PathError("page must be a number")
        info = await run_in_threadpool(lib.file_info, user, root, parts, cfg.show_all_files)
        if kinds.kind_of(info.name) != "comic":
            raise NotFound("not a comic archive")
        external = root != UPLOADS_ROOT
        try:
            pages = await run_in_threadpool(meta.pages_for, info, external)
            if not 0 <= number < len(pages):
                raise NotFound("no such page")
            etag = '"%s"' % hashlib.md5(("%s-%s-%d" % (info.size, info.mtime, number)).encode(),
                                        usedforsecurity=False).hexdigest()
            headers = {"ETag": etag, "Cache-Control": "private, max-age=86400"}     # a page never changes
            if etag in {t.strip() for t in request.headers.get("if-none-match", "").split(",")}:
                return Response(status_code=304, headers=headers)
            data = await run_in_threadpool(cbz.read_page, info.path, pages[number], cbz.MAX_PAGE_BYTES, external)
        except CbzError as e:
            return PlainTextResponse("Cannot read this page (%s)" % e, 422)
        return Response(data, media_type=cbz.mime_for(pages[number]), headers=headers)

    def cover_source(info, root: str):
        """(bytes, mime) of the picture that stands for a book: the cover of an EPUB, the first page of a comic, the picture of a song."""
        kind = kinds.kind_of(info.name)
        if kind == "image":
            return thumbs.image_file(info.path)
        if kind == "epub":
            return thumbs.epub_cover(info.path)
        if kind == "comic":
            external = root != UPLOADS_ROOT
            pages = meta.pages_for(info, external)
            if not pages:
                return None
            data = cbz.read_page(info.path, pages[0], cbz.MAX_PAGE_BYTES, external)
            mime = mediainfo.sniff_image(data)
            return (data, mime) if mime else None
        if kind in ("audio", "video"):
            return mediainfo.read_cover(info.path)
        return None

    def make_thumb(info, root: str, width: int):
        key = "%s|%s|%s|%d" % (info.path, info.size, info.mtime, width)
        hit = thumb_cache.get(key)
        if hit is not None:
            return hit, "image/jpeg"
        with thumb_gate:                                        # a few at a time: each one decodes a whole picture in memory
            hit = thumb_cache.get(key)                          # another request may have made it while this one waited
            if hit is not None:
                return hit, "image/jpeg"
            try:
                found = cover_source(info, root)
            except (CbzError, OSError):
                found = None
            if found is None:
                return None
            small = thumbs.scaled(found[0], width)
        if small is None:                                       # no Pillow: the original picture, as it is
            return found if len(found[0]) <= mediainfo.MAX_COVER else None
        if small is False:                                      # unreadable or too big to decode safely: no cover
            return None
        thumb_cache.put(key, small[0])
        return small

    @guarded("media")
    async def thumb(request: Request, user):
        """A small JPEG of a book's cover (?w= the width in pixels), for the OPDS feeds."""
        if not cfg.thumbnails:
            raise NotFound("thumbnails are switched off")
        root = request.path_params["root"]
        parts = _parts(request.path_params["path"])
        info = await run_in_threadpool(lib.file_info, user, root, parts, cfg.show_all_files)
        width = thumbs.clamp_width(request.query_params.get("w", ""))
        found = await run_in_threadpool(make_thumb, info, root, width)
        if found is None:
            raise NotFound("no cover")
        data, mime = found
        etag = '"%s"' % hashlib.sha1(("%s|%s|%d" % (info.size, info.mtime, width)).encode()).hexdigest()[:20]
        headers = {"ETag": etag, "Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(data, media_type=mime, headers=headers)

    def covers_for(root: str, entry):
        """The two cover links (small, larger) of a book entry in a feed, for the kinds that have a picture."""
        if not cfg.thumbnails or kinds.kind_of(entry.name) not in ("epub", "comic", "audio"):
            return None
        base = urls.thumb(root, entry.parts)
        return base + "?w=160", base + "?w=600"

    @guarded("api")
    async def bookmarks(request: Request, user):
        """Bookmarks in one audio or video file, per user: GET lists, POST {position, note} adds, DELETE ?id= removes."""
        root = request.path_params["root"]
        parts = _parts(request.path_params["path"])
        info = await run_in_threadpool(lib.file_info, user, root, parts, cfg.show_all_files)
        if kinds.kind_of(info.name) not in ("audio", "video"):
            raise NotFound("not audio or video")
        path = "/".join(parts)
        if request.method == "POST":
            raw_body = await read_limited(request, 2048)
            try:
                body = json.loads(raw_body if raw_body is not None else b"")
                position, note = float(body["position"]), str(body.get("note", ""))[:200]
            except (ValueError, KeyError, TypeError):
                return JSONResponse({"error": "invalid bookmark"}, 400)
            if not 0 <= position < 10_000_000:
                return JSONResponse({"error": "invalid position"}, 400)
            made = await run_in_threadpool(state.add_bookmark, user.name, root, path, position, note)
            if made is None:
                return JSONResponse({"error": "too many bookmarks in this file"}, 400)
            return JSONResponse({"id": made}, 201)
        if request.method == "DELETE":
            try:
                wanted = int(request.query_params.get("id", ""))
            except ValueError:
                return JSONResponse({"error": "id must be a number"}, 400)
            await run_in_threadpool(state.delete_bookmark, user.name, wanted)
            return JSONResponse({"ok": True})
        return JSONResponse({"bookmarks": await run_in_threadpool(state.list_bookmarks, user.name, root, path)})

    @guarded("api")
    async def progress(request: Request, user):
        root = request.path_params["root"]
        parts = _parts(request.path_params["path"])
        info = await run_in_threadpool(lib.file_info, user, root, parts, cfg.show_all_files)   # none for files you cannot see
        path = "/".join(await run_in_threadpool(lib.canonical_parts, user, root, info, parts))
        if request.method == "PUT":
            if "application/json" not in request.headers.get("content-type", ""):
                return JSONResponse({"error": "a JSON body is required"}, 415)
            raw = await read_limited(request, 8192)
            if raw is None:
                return JSONResponse({"error": "body too large"}, 413)
            try:
                data = json.loads(raw)
                locator, percent = data["locator"], float(data["percent"])
            except (ValueError, KeyError, TypeError):
                return JSONResponse({"error": "expected {locator, percent}"}, 400)
            if not isinstance(locator, str) or not locator or len(locator) > 2048 or percent != percent:
                return JSONResponse({"error": "invalid locator or percent"}, 400)
            state.put_progress(user.name, root, path, locator, percent)
            if percent >= 0.98:                                   # finished: it leaves the "Read next" list by itself
                state.shelf_remove(user.name, "next", root, path)
            return JSONResponse({"ok": True})
        return JSONResponse(state.get_progress(user.name, root, path) or {})

    # ---- OPDS and downloads (reader apps) ---------------------------------------------
    SHELF_TITLES = {"favourite": "Favourites", "next": "Read next"}

    @guarded("basic")
    async def opds_root(request: Request, user):
        now = time.time()
        shelves_with_items = [s for s in ("next", "favourite") if await run_in_threadpool(state.shelf_list, user.name, s)]
        if opds2.wants(request):
            nav = [opds2.nav_link(SHELF_TITLES[s], urls.opds_shelf(s)) for s in shelves_with_items]
            nav += [opds2.nav_link(r.name, urls.opds(r.name)) for r in lib.roots_for(user)]
            body = opds2.feed(title="Library", self_href=urls.opds(), start_href=urls.opds(), navigation=nav, publications=[])
            return Response(body, media_type=opds2.TYPE, headers={"Vary": "Accept"})
        entries = [opds.nav_entry(SHELF_TITLES[s], "urn:pluteus:shelf:" + s, urls.opds_shelf(s), now) for s in shelves_with_items]
        entries += [opds.nav_entry(r.name, "urn:pluteus:" + r.name, urls.opds(r.name), now)
                    for r in lib.roots_for(user)]
        body = opds.build_feed(title="Library", feed_id="urn:pluteus:root", self_href=urls.opds(),
                               start_href=urls.opds(), updated=now, entries=entries)
        return Response(body, media_type=opds.NAV_TYPE)

    def page_streams(user, root: str, shown: list) -> dict:
        """OPDS-PSE data for the comics on this feed page: {path: link details}. Comics whose page list cannot be
        read simply get no stream link (they stay downloadable)."""
        pages_by_path = meta.lookup_pages(shown, root != UPLOADS_ROOT)
        if not pages_by_path:
            return {}
        comics = [e for e in shown if e.path in pages_by_path]
        rows = state.progress_rows(user.name, root, ["/".join(e.parts) for e in comics])
        out = {}
        for e in comics:
            pages = pages_by_path[e.path]
            info = {"href": urls.cbz(root, e.parts) + "?page={pageNumber}", "count": len(pages),
                    "mime": cbz.mime_for(pages[0])}
            row = rows.get("/".join(e.parts))
            found = re.fullmatch(r"page:(\d+)", row["locator"]) if row else None
            if found:                                              # our 0-based page index -> the spec's 1-based lastRead
                info["last_read"] = min(len(pages), int(found.group(1)) + 1)
                info["last_read_date"] = row["updated"]
            out[e.path] = info
        return out

    def shelf_feed(user, name: str, as_json: bool) -> Response:
        """One shelf as an OPDS feed: books to download (or page-stream), folders to open."""
        found = shelf_entries(user, name)
        files = [SimpleNamespace(name=i.name, path=i.path, size=i.size, mtime=i.mtime, parts=parts, is_dir=False, root=row["root"])
                 for row, parts, i in found if i is not None]
        metas = meta.lookup_many(files)
        streams: dict = {}
        if cfg.opds_pse:
            for root in {f.root for f in files}:
                streams.update(page_streams(user, root, [f for f in files if f.root == root]))
        by_path = {f.path: f for f in files}
        title, here = SHELF_TITLES[name], urls.opds_shelf(name)
        nav, books, atom = [], [], []
        for row, parts, i in found[:60]:
            root = row["root"]
            eid = "urn:pluteus:" + root + ":" + "/".join(parts)
            if i is None:
                label = lib.display_name(parts[-1])
                nav.append(opds2.nav_link(label, urls.opds(root, parts)))
                atom.append(opds.nav_entry(label, eid, urls.opds(root, parts), time.time()))
                continue
            f = by_path[i.path]
            mime = MIME_TYPES.get(os.path.splitext(f.name)[1].lower(), "application/octet-stream")
            label, cover = f.name.rsplit(".", 1)[0], covers_for(root, f)
            books.append(opds2.publication(label, eid, urls.download(root, parts), mime, clamp_time(f.mtime), metas.get(f.path),
                                           streams.get(f.path), cover))
            atom.append(opds.book_entry(label, eid, urls.download(root, parts), mime, f.mtime, f.size, metas.get(f.path),
                                        streams.get(f.path), cover))
        if as_json:
            body = opds2.feed(title=title, self_href=here, start_href=urls.opds(), up_href=urls.opds(), navigation=nav,
                              publications=books)
            return Response(body, media_type=opds2.TYPE, headers={"Vary": "Accept"})
        body = opds.build_feed(title=title, feed_id="urn:pluteus:shelf:" + name, self_href=here, start_href=urls.opds(),
                               updated=time.time(), entries=atom, up_href=urls.opds())
        kind = opds.ACQ_TYPE if books else opds.NAV_TYPE
        return Response(body, media_type=kind, headers={"Vary": "Accept"})

    @guarded("basic")
    async def opds_shelf(request: Request, user):
        name = request.path_params["shelf"]
        if name not in SHELF_TITLES:
            raise NotFound("no such shelf")
        return await run_in_threadpool(shelf_feed, user, name, opds2.wants(request))

    @guarded("basic")
    async def opds_folder(request: Request, user):
        root = request.path_params["root"]
        parts = _parts(request.path_params.get("path", ""))
        listing = await run_in_threadpool(lib.list_dir, user, root, parts, False, False)    # names only: see with_stats
        shown, page, pages = opds.paginate(listing, _page_number(request), cfg.page_size)
        shown = await run_in_threadpool(lib.with_stats, shown)                              # size and date of this page only
        here = urls.opds(root, parts)
        metas = await run_in_threadpool(meta.lookup_many, shown)
        streams = await run_in_threadpool(page_streams, user, root, shown) if cfg.opds_pse else {}
        if opds2.wants(request):
            nav, books = [], []
            for e in shown:
                if e.is_dir:
                    nav.append(opds2.nav_link(e.display, urls.opds(root, e.parts)))
                else:
                    mime = MIME_TYPES.get(os.path.splitext(e.name)[1].lower(), "application/octet-stream")
                    books.append(opds2.publication(e.name.rsplit(".", 1)[0], "urn:pluteus:" + root + ":" + "/".join(e.parts),
                                                   urls.download(root, e.parts), mime, clamp_time(e.mtime), metas.get(e.path),
                                                   streams.get(e.path), covers_for(root, e)))
            body = opds2.feed(title=lib.display_name(parts[-1]) if parts else root, self_href=with_page(here, page),
                              start_href=urls.opds(), navigation=nav, publications=books,
                              up_href=urls.opds(root, parts[:-1]) if parts else urls.opds(),
                              next_href=with_page(here, page + 1) if page < pages else None,
                              prev_href=with_page(here, page - 1) if page > 1 else None,
                              page=page, per_page=cfg.page_size, total=len(listing))
            return Response(body, media_type=opds2.TYPE, headers={"Vary": "Accept"})
        entries = []
        for e in shown:
            eid = "urn:pluteus:" + root + ":" + "/".join(e.parts)
            if e.is_dir:
                entries.append(opds.nav_entry(e.display, eid, urls.opds(root, e.parts), e.mtime))
            else:
                mime = MIME_TYPES.get(os.path.splitext(e.name)[1].lower(), "application/octet-stream")
                entries.append(opds.book_entry(e.name.rsplit(".", 1)[0], eid,
                                               urls.download(root, e.parts), mime, e.mtime, e.size,
                                               metas.get(e.path), streams.get(e.path), covers_for(root, e)))
        up = urls.opds(root, parts[:-1]) if parts else urls.opds()
        updated = max((clamp_time(e.mtime) for e in shown), default=time.time())            # the newest entry on this page
        body = opds.build_feed(
            title=lib.display_name(parts[-1]) if parts else root,
            feed_id="urn:pluteus:" + root + ":" + "/".join(parts),
            self_href=with_page(here, page), start_href=urls.opds(), updated=updated,
            entries=entries, up_href=up,
            next_href=with_page(here, page + 1) if page < pages else None,
            prev_href=with_page(here, page - 1) if page > 1 else None)
        kind = opds.ACQ_TYPE if any(not e.is_dir for e in shown) else opds.NAV_TYPE
        return Response(body, media_type=kind)

    @guarded("basic")
    async def download(request: Request, user):
        root = request.path_params["root"]
        parts = _parts(request.path_params["path"])
        info = await run_in_threadpool(lib.file_info, user, root, parts, cfg.show_all_files)
        # Always a download, never displayed in our origin: unknown and active types (html, svg, ...) cannot run here.
        return FileResponse(info.path, media_type=info.mime, filename=info.name)

    routes = [
        Route("/healthz", healthz),
        Route("/static/{path:path}", static),
        Route("/favicon.ico", favicon),
        Route("/theme/{name}/{path:path}", theme_file),
        Route("/login", login_form, methods=["GET"]),
        Route("/login", login_submit, methods=["POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/", home),
        Route("/dismiss/{root}/{path:path}", dismiss, methods=["POST"]),
        Route("/settings", settings, methods=["GET", "POST"]),
        Route("/browse/{root}", browse),
        Route("/browse/{root}/{path:path}", browse),
        Route("/read/{root}/{path:path}", read),
        Route("/view/{root}/{path:path}", view),
        Route("/raw/{root}/{path:path}", raw),
        Route("/cover/{root}/{path:path}", cover),
        Route("/subs/{root}/{path:path}", subs),
        Route("/shell", shell),
        Route("/stations/{kind}", stations_page),
        Route("/api/stations", stations_save, methods=["POST"]),
        Route("/api/queue/{root}/{path:path}", queue),
        Route("/api/queue/{root}", queue),
        Route("/zip/{root}/{path:path}", zip_entry),
        Route("/cbz/{root}/{path:path}", cbz_page),
        Route("/thumb/{root}/{path:path}", thumb),
        Route("/manifest.webmanifest", manifest),
        Route("/sw.js", service_worker),
        Route("/join/{token}", join),
        Route("/share", share, methods=["POST"]),
        Route("/upload/{root}", upload, methods=["POST"]),
        Route("/upload/{root}/{path:path}", upload, methods=["POST"]),
        Route("/files/{op}/{root}", file_op, methods=["POST"]),
        Route("/files/{op}/{root}/{path:path}", file_op, methods=["POST"]),
        Route("/api/progress/{root}/{path:path}", progress, methods=["GET", "PUT"]),
        Route("/api/shelf/{shelf}/{root}/{path:path}", shelf, methods=["POST", "DELETE"]),
        Route("/api/bookmarks/{root}/{path:path}", bookmarks, methods=["GET", "POST", "DELETE"]),
        Route("/opds-shelves/{shelf}", opds_shelf),
        Route("/opds", opds_root),
        Route("/opds/", opds_root),
        Route("/opds/{root}", opds_folder),
        Route("/opds/{root}/{path:path}", opds_folder),
        Route("/dl/{root}/{path:path}", download),
    ]
    if cfg.webdav:
        from .webdav import Dav
        dav = Dav(cfg, lib, urls)

        @guarded("basic")
        async def dav_handler(request: Request, user):
            return await run_in_threadpool(dav.handle, request, user)
        every = ["OPTIONS", "HEAD", "GET", "PROPFIND", "PROPPATCH", "PUT", "POST", "DELETE", "MKCOL", "COPY", "MOVE", "LOCK", "UNLOCK"]
        routes += [Route("/dav", dav_handler, methods=every), Route("/dav/", dav_handler, methods=every),
                   Route("/dav/{path:path}", dav_handler, methods=every)]

    class Throttle:
        """The Subsonic and KOReader logins share the web login's failure counting (same names, same locks)."""
        def wait(self, request, name):
            return limiter.wait_seconds(name, limiter_ip(request))

        def wait_key(self, request):
            return limiter.wait_seconds("", limiter_ip(request))

        def fail(self, request, name):
            note_failure(name, limiter_ip(request), "", False)

        def fail_key(self, request):
            limiter.failure_ip(limiter_ip(request))              # a wrong KEY counts against the address only

        def ok(self, request, name):
            limiter.success(name)
    if cfg.subsonic:
        from .subsonic import Subsonic
        api = Subsonic(cfg, lib, state, auth, meta, Throttle())
        routes.append(Route("/rest/{name}", api.handle, methods=["GET", "POST"]))
    if cfg.kosync:
        from .kosync import KoSync
        routes.append(Route("/kosync/{route:path}", KoSync(auth, state, Throttle()).handle, methods=["GET", "PUT", "POST"]))
    app = Starlette(routes=routes, middleware=[Middleware(AccessLog), Middleware(SecurityHeaders)])
    app.state.library = lib
    app.state.state = state
    app.state.sessions = sessions
    app.state.themes = themes
    app.state.limiter = limiter
    app.state.auth = auth
    app.state.guarded = guarded          # lets the reader modules register routes with the same identity rules
    # What the admin page (adminapp.py, on its own port) shares with this app: the same state, throttle and users, the
    # same configuration, and the public routes for the stylesheets, icons and themes.
    app.state.cfg = cfg
    app.state.config_path = config_path
    app.state.started = time.time()
    app.state.asset_routes = [r for r in routes if r.path in ("/static/{path:path}", "/favicon.ico", "/theme/{name}/{path:path}")]
    return StripBase(app, cfg.base_path) if cfg.base_path else app

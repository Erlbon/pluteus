"""The Starlette application: routes, identity (session cookie or HTTP Basic), error mapping.

Who is asking is decided in this order: a valid session cookie (browser), HTTP Basic credentials (reader
apps), then the optional anonymous user. Browsers without a login are redirected to /login; reader apps and
API calls get a plain 401 so they can show their own login prompt.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse,
                                 RedirectResponse, Response)
from starlette.routing import Route

from . import cbz, kinds, opds, textview, web
from .auth import Authenticator
from .cbz import CbzError
from .config import MIME_TYPES, Config
from .fs import SORTS, Library, NotFound, PathError, clamp_time, sort_entries
from .meta import MetaCache, MetaService
from .ratelimit import FailureLimiter
from .reload import UsersReloader
from .sessions import COOKIE, SessionManager
from .state import StateDB
from .themes import DEFAULT, Themes
from .urls import Urls, with_page

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_ALLOWED = {".css", ".js", ".mjs", ".svg", ".png", ".ico", ".woff2", ".webmanifest", ".pfb", ".ttf"}
# Browsers refuse an ES module that is not served as JavaScript, and mimetypes does not know .mjs everywhere.
STATIC_TYPES = {".mjs": "text/javascript", ".pfb": "application/octet-stream"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' blob:; img-src 'self' data: blob:; "
       "font-src 'self' data: blob:; connect-src 'self'; frame-src 'self' blob:; object-src 'none'; "
       "base-uri 'self'; form-action 'self'; frame-ancestors 'self'")
NO_CACHE = {"Cache-Control": "no-cache"}


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
                    headers.setdefault("Content-Security-Policy", CSP)
            await send(message)

        await self.app(scope, receive, wrapped)


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
    auth = Authenticator(cfg.users, cfg.auth_cache_seconds)
    sessions = SessionManager(state, cfg.session_days)
    limiter = FailureLimiter()
    themes = Themes(cfg.themes_dir)
    reloader = UsersReloader(config_path, auth, reload_interval) if config_path else None
    urls = Urls(cfg.base_path)
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
        host = request.headers.get("host", "")
        return bool(origin) and bool(host) and urlsplit(origin).netloc.lower() == host.lower()

    async def resolve(request: Request):
        """(user or None, seconds to wait when throttled). Password hashing and the hourly housekeeping run on
        worker threads, never on the event loop."""
        if reloader:
            reloader.maybe_reload()
        now = time.monotonic()
        if now >= housekeeping_due[0]:
            housekeeping_due[0] = now + 3600
            await run_in_threadpool(state.housekeeping, backup_dir)
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
            name, ip = (creds[0] if creds else ""), client_ip(request)
            wait = limiter.wait_seconds(name, ip)
            if wait:
                return None, wait
            user = await run_in_threadpool(auth.authenticate, header)
            if user:
                limiter.success(name)
                return user, 0
            limiter.failure(name, ip)
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

    def ctx_for(user) -> web.Ctx:
        return web.Ctx(urls, user.name, theme_for(user.name), readers=True)

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
            here = request.url.path + ("?" + request.url.query if request.url.query else "")
            return RedirectResponse(urls.login(here), 303)
        return PlainTextResponse("Authentication required", 401, headers=challenge)

    def guarded(mode: str):
        """mode: 'web' (browser pages), 'api' (JSON), 'basic' (reader apps and downloads)."""
        def decorate(fn):
            @functools.wraps(fn)
            async def wrapper(request: Request):
                user, wait = await resolve(request)
                if user is None:
                    return unauthorized(request, mode, wait)
                if not csrf_ok(request):
                    return PlainTextResponse("Cross-site request refused", 403)
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
        if too_big(request, 16384):
            return PlainTextResponse("Request too large", 413)
        form = _form(await request.body())
        username, password = form.get("username", ""), form.get("password", "")
        next_url = _safe_next(form.get("next", ""), cfg.base_path, urls.home())
        ip = client_ip(request)
        wait = limiter.wait_seconds(username, ip)
        if wait:
            return HTMLResponse(web.login_page(login_ctx(), next_url,
                                                "Too many failed attempts. Try again in %d seconds." % wait,
                                                username), 429, headers={"Retry-After": str(wait)})
        user = await run_in_threadpool(auth.check, username, password)
        if user is None:
            limiter.failure(username, ip)
            return HTMLResponse(web.login_page(login_ctx(), next_url, "Wrong user name or password.",
                                               username), 401)
        limiter.success(username)
        token = sessions.start(user)
        response = RedirectResponse(next_url, 303)
        response.set_cookie(COOKIE, token, max_age=cfg.session_days * 86400, path=cookie_path,
                            httponly=True, samesite="lax", secure=cookie_secure(request))
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
        """(link, label, fraction) for the books this user left unfinished, newest first."""
        out = []
        for r in state.recent_progress(user.name, 40):
            kind = kinds.kind_of(r["path"])
            if r["root"] in cfg.roots and user.may_use(r["root"]) and r["percent"] < 0.98 \
                    and kind in kinds.KINDS_WITH_PROGRESS:
                parts = tuple(r["path"].split("/"))
                try:                                   # skip books that are now hidden, excluded or gone
                    lib.file_info(user, r["root"], parts, cfg.show_all_files)
                except (NotFound, PathError, OSError):
                    continue
                link = urls.read(r["root"], parts) if kind in ("epub", "comic") else urls.view(r["root"], parts)
                out.append((link, os.path.splitext(parts[-1])[0], r["percent"]))
        return out[:6]

    @guarded("web")
    async def home(request: Request, user):
        recent = await run_in_threadpool(continue_reading, user)
        return HTMLResponse(web.roots_page(lib.roots_for(user), ctx_for(user), recent))

    @guarded("web")
    async def browse(request: Request, user):
        root = request.path_params["root"]
        parts = _parts(request.path_params.get("path", ""))
        entries = await run_in_threadpool(lib.list_dir, user, root, parts, cfg.show_all_files)
        sort = request.query_params.get("sort", "name")
        sort = sort if sort in SORTS else "name"
        desc = request.query_params.get("dir") == "desc"
        if sort != "name" or desc:
            entries = sort_entries(entries, sort, desc)
        shown, page, pages = opds.paginate(entries, _page_number(request), cfg.page_size)
        metas = await run_in_threadpool(meta.lookup_many, shown)
        book_paths = ["/".join(e.parts) for e in shown if not e.is_dir]
        progress = await run_in_threadpool(state.progress_for, user.name, root, book_paths)
        return HTMLResponse(web.listing_page(root, parts, shown, page, pages, len(entries), ctx_for(user),
                                             metas, progress, sort=sort, desc=desc))

    @guarded("web")
    async def settings(request: Request, user):
        if request.method == "POST":
            choice = _form(await request.body()).get("theme", "")
            if themes.exists(choice):
                state.set_prefs(user.name, {**state.get_prefs(user.name), "theme": choice})
            return RedirectResponse(urls.settings() + "?saved=1", 303)
        return HTMLResponse(web.settings_page(ctx_for(user), themes.names(), "saved" in request.query_params))

    @guarded("web")
    async def read(request: Request, user):
        root = request.path_params["root"]
        parts = _parts(request.path_params["path"])
        info = await run_in_threadpool(lib.file_info, user, root, parts, cfg.show_all_files)   # exists, allowed, not hidden
        key = hashlib.sha1(("%s-%s" % (info.size, info.mtime)).encode()).hexdigest()[:12]
        if info.name.lower().endswith(".cbz"):
            try:
                pages = await run_in_threadpool(meta.pages_for, info)
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
        entries = lib.list_dir(user, root, parts[:-1], True)
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

    def view_page(user, root: str, parts: tuple, info, kind: str, as_text: bool):
        ctx, back, download = ctx_for(user), urls.browse(root, parts[:-1]), urls.download(root, parts)
        if not as_text and kind in ("image", "audio", "video"):
            prev, nxt, position, total = neighbours(user, root, parts, kind)
            prev_url = urls.view(root, prev.parts) if prev else None
            next_url = urls.view(root, nxt.parts) if nxt else None
            if kind == "image":
                return web.image_viewer_page(
                    ctx, info.name, raw_url=urls.raw(root, parts), download_url=download, back_url=back,
                    prev_url=prev_url, next_url=next_url, next_raw=urls.raw(root, nxt.parts) if nxt else None,
                    position=position, total=total)
            return web.media_viewer_page(
                ctx, info.name, kind=kind, raw_url=urls.raw(root, parts), mime=kinds.INLINE_MIME[kinds.extension(info.name)],
                download_url=download, back_url=back, progress_url=urls.api("progress", root, parts),
                prev_url=prev_url, next_url=next_url, position=position, total=total)
        if not as_text and kind == "pdf":
            return web.pdf_viewer_page(ctx, info.name, raw_url=urls.raw(root, parts), download_url=download, back_url=back,
                                       progress_url=urls.api("progress", root, parts))
        # text: known text types, anything that looks like text, or whatever the user asked to see as text
        page = text_page(user, root, parts, info, as_text) if (as_text or kind in ("text", "other")) else None
        if page is not None:
            return page
        rows = [("Name", info.name), ("Type", info.mime), ("Size", web._size(info.size)),
                ("Modified", time.strftime("%Y-%m-%d %H:%M", time.localtime(clamp_time(info.mtime))))]
        note = "This looks like a binary file." if kind in ("text", "other") else ""
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
        if not info.name.lower().endswith(".cbz"):
            raise NotFound("not a comic archive")
        try:
            pages = await run_in_threadpool(meta.pages_for, info)
            if not 0 <= number < len(pages):
                raise NotFound("no such page")
            etag = '"%s"' % hashlib.md5(("%s-%s-%d" % (info.size, info.mtime, number)).encode(),
                                        usedforsecurity=False).hexdigest()
            headers = {"ETag": etag, "Cache-Control": "private, max-age=86400"}     # a page never changes
            if etag in {t.strip() for t in request.headers.get("if-none-match", "").split(",")}:
                return Response(status_code=304, headers=headers)
            data = await run_in_threadpool(cbz.read_page, info.path, pages[number])
        except CbzError as e:
            return PlainTextResponse("Cannot read this page (%s)" % e, 422)
        return Response(data, media_type=cbz.mime_for(pages[number]), headers=headers)

    @guarded("api")
    async def progress(request: Request, user):
        root = request.path_params["root"]
        parts = _parts(request.path_params["path"])
        await run_in_threadpool(lib.file_info, user, root, parts, cfg.show_all_files)   # none for files you cannot see
        path = "/".join(parts)
        if request.method == "PUT":
            if "application/json" not in request.headers.get("content-type", ""):
                return JSONResponse({"error": "a JSON body is required"}, 415)
            raw = b"" if too_big(request, 8192) else await request.body()
            if too_big(request, 8192) or len(raw) > 8192:
                return JSONResponse({"error": "body too large"}, 413)
            try:
                data = json.loads(raw)
                locator, percent = data["locator"], float(data["percent"])
            except (ValueError, KeyError, TypeError):
                return JSONResponse({"error": "expected {locator, percent}"}, 400)
            if not isinstance(locator, str) or not locator or len(locator) > 2048 or percent != percent:
                return JSONResponse({"error": "invalid locator or percent"}, 400)
            state.put_progress(user.name, root, path, locator, percent)
            return JSONResponse({"ok": True})
        return JSONResponse(state.get_progress(user.name, root, path) or {})

    # ---- OPDS and downloads (reader apps) ---------------------------------------------
    @guarded("basic")
    async def opds_root(request: Request, user):
        now = time.time()
        entries = [opds.nav_entry(r.name, "urn:pluteus:" + r.name, urls.opds(r.name), now)
                   for r in lib.roots_for(user)]
        body = opds.build_feed(title="Library", feed_id="urn:pluteus:root", self_href=urls.opds(),
                               start_href=urls.opds(), updated=now, entries=entries)
        return Response(body, media_type=opds.NAV_TYPE)

    def page_streams(user, root: str, shown: list) -> dict:
        """OPDS-PSE data for the comics on this feed page: {path: link details}. Comics whose page list cannot be
        read simply get no stream link (they stay downloadable)."""
        pages_by_path = meta.lookup_pages(shown)
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

    @guarded("basic")
    async def opds_folder(request: Request, user):
        root = request.path_params["root"]
        parts = _parts(request.path_params.get("path", ""))
        listing = await run_in_threadpool(lib.list_dir, user, root, parts)
        shown, page, pages = opds.paginate(listing, _page_number(request), cfg.page_size)
        here = urls.opds(root, parts)
        metas = await run_in_threadpool(meta.lookup_many, shown)
        streams = await run_in_threadpool(page_streams, user, root, shown) if cfg.opds_pse else {}
        entries = []
        for e in shown:
            eid = "urn:pluteus:" + root + ":" + "/".join(e.parts)
            if e.is_dir:
                entries.append(opds.nav_entry(e.display, eid, urls.opds(root, e.parts), e.mtime))
            else:
                mime = MIME_TYPES.get(os.path.splitext(e.name)[1].lower(), "application/octet-stream")
                entries.append(opds.book_entry(e.name.rsplit(".", 1)[0], eid,
                                               urls.download(root, e.parts), mime, e.mtime, e.size,
                                               metas.get(e.path), streams.get(e.path)))
        up = urls.opds(root, parts[:-1]) if parts else urls.opds()
        updated = max((clamp_time(e.mtime) for e in listing), default=time.time())
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
        Route("/settings", settings, methods=["GET", "POST"]),
        Route("/browse/{root}", browse),
        Route("/browse/{root}/{path:path}", browse),
        Route("/read/{root}/{path:path}", read),
        Route("/view/{root}/{path:path}", view),
        Route("/raw/{root}/{path:path}", raw),
        Route("/cbz/{root}/{path:path}", cbz_page),
        Route("/api/progress/{root}/{path:path}", progress, methods=["GET", "PUT"]),
        Route("/opds", opds_root),
        Route("/opds/", opds_root),
        Route("/opds/{root}", opds_folder),
        Route("/opds/{root}/{path:path}", opds_folder),
        Route("/dl/{root}/{path:path}", download),
    ]
    app = Starlette(routes=routes, middleware=[Middleware(SecurityHeaders)])
    app.state.library = lib
    app.state.state = state
    app.state.sessions = sessions
    app.state.themes = themes
    app.state.limiter = limiter
    app.state.auth = auth
    app.state.guarded = guarded          # lets the reader modules register routes with the same identity rules
    return app

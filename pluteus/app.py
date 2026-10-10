"""The Starlette application: routes, identity (session cookie or HTTP Basic), error mapping.

Who is asking is decided in this order: a valid session cookie (browser), HTTP Basic credentials (reader
apps), then the optional anonymous user. Browsers without a login are redirected to /login; reader apps and
API calls get a plain 401 so they can show their own login prompt.
"""
from __future__ import annotations

import functools
import hashlib
import os
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

from . import opds, web
from .auth import Authenticator
from .config import MIME_TYPES, Config
from .fs import Library, NotFound, PathError
from .meta import MetaCache, MetaService
from .ratelimit import FailureLimiter
from .reload import UsersReloader
from .sessions import COOKIE, SessionManager
from .state import StateDB
from .themes import DEFAULT, Themes
from .urls import Urls, with_page

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_ALLOWED = {".css", ".js", ".svg", ".png", ".ico", ".woff2", ".map"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
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
                return forwarded.split(",")[0].strip()
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

    def resolve(request: Request):
        """(user or None, seconds to wait when throttled)."""
        if reloader:
            reloader.maybe_reload()
        now = time.monotonic()
        if now >= housekeeping_due[0]:
            housekeeping_due[0] = now + 3600
            state.housekeeping(backup_dir)
        user = sessions.user_for(request.cookies.get(COOKIE), auth.users)
        if user:
            return user, 0
        header = request.headers.get("authorization")
        if header:
            user = auth.cached_user(header)
            if user:
                return user, 0
            creds = auth.parse_basic(header)
            name, ip = (creds[0] if creds else ""), client_ip(request)
            wait = limiter.wait_seconds(name, ip)
            if wait:
                return None, wait
            user = auth.authenticate(header)
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
        return web.Ctx(urls, user.name, theme_for(user.name), readers=False)

    def unauthorized(request: Request, mode: str, wait: int) -> Response:
        if wait:
            return PlainTextResponse("Too many failed attempts. Try again in %d seconds." % wait, 429,
                                     headers={"Retry-After": str(wait)})
        if mode == "api":
            return JSONResponse({"error": "login required"}, 401)
        if mode == "web" and "text/html" in request.headers.get("accept", ""):
            here = request.url.path + ("?" + request.url.query if request.url.query else "")
            return RedirectResponse(urls.login(here), 303)
        return PlainTextResponse("Authentication required", 401, headers=challenge)

    def guarded(mode: str):
        """mode: 'web' (browser pages), 'api' (JSON), 'basic' (reader apps and downloads)."""
        def decorate(fn):
            @functools.wraps(fn)
            async def wrapper(request: Request):
                user, wait = resolve(request)
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
        return FileResponse(path, headers={"ETag": etag, **NO_CACHE})

    async def static(request: Request):
        path = _file(STATIC_DIR, request.path_params["path"])
        if path is None or request.path_params["path"].startswith("themes/"):
            return PlainTextResponse("Not found", 404)
        return revalidated(request, path)

    async def theme_file(request: Request):
        path = themes.resolve(request.path_params["name"], request.path_params["path"])
        if path is None:
            return PlainTextResponse("Not found", 404)
        return revalidated(request, path)

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
    @guarded("web")
    async def home(request: Request, user):
        return HTMLResponse(web.roots_page(lib.roots_for(user), ctx_for(user)))

    @guarded("web")
    async def browse(request: Request, user):
        root = request.path_params["root"]
        parts = _parts(request.path_params.get("path", ""))
        entries = await run_in_threadpool(lib.list_dir, user, root, parts)
        shown, page, pages = opds.paginate(entries, _page_number(request), cfg.page_size)
        metas = await run_in_threadpool(meta.lookup_many, shown)
        book_paths = ["/".join(e.parts) for e in shown if not e.is_dir]
        progress = await run_in_threadpool(state.progress_for, user.name, root, book_paths)
        return HTMLResponse(web.listing_page(root, parts, shown, page, pages, len(entries), ctx_for(user),
                                             metas, progress))

    @guarded("web")
    async def settings(request: Request, user):
        if request.method == "POST":
            choice = _form(await request.body()).get("theme", "")
            if themes.exists(choice):
                state.set_prefs(user.name, {**state.get_prefs(user.name), "theme": choice})
            return RedirectResponse(urls.settings() + "?saved=1", 303)
        return HTMLResponse(web.settings_page(ctx_for(user), themes.names(), "saved" in request.query_params))

    # ---- OPDS and downloads (reader apps) ---------------------------------------------
    @guarded("basic")
    async def opds_root(request: Request, user):
        now = time.time()
        entries = [opds.nav_entry(r.name, "urn:pluteus:" + r.name, urls.opds(r.name), now)
                   for r in lib.roots_for(user)]
        body = opds.build_feed(title="Library", feed_id="urn:pluteus:root", self_href=urls.opds(),
                               start_href=urls.opds(), updated=now, entries=entries)
        return Response(body, media_type=opds.NAV_TYPE)

    @guarded("basic")
    async def opds_folder(request: Request, user):
        root = request.path_params["root"]
        parts = _parts(request.path_params.get("path", ""))
        listing = await run_in_threadpool(lib.list_dir, user, root, parts)
        shown, page, pages = opds.paginate(listing, _page_number(request), cfg.page_size)
        here = urls.opds(root, parts)
        metas = await run_in_threadpool(meta.lookup_many, shown)
        entries = []
        for e in shown:
            eid = "urn:pluteus:" + root + ":" + "/".join(e.parts)
            if e.is_dir:
                entries.append(opds.nav_entry(e.display, eid, urls.opds(root, e.parts), e.mtime))
            else:
                mime = MIME_TYPES.get(os.path.splitext(e.name)[1].lower(), "application/octet-stream")
                entries.append(opds.book_entry(e.name.rsplit(".", 1)[0], eid,
                                               urls.download(root, e.parts), mime, e.mtime, e.size,
                                               metas.get(e.path)))
        up = urls.opds(root, parts[:-1]) if parts else urls.opds()
        updated = max((e.mtime for e in listing), default=time.time())
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
        info = await run_in_threadpool(lib.file_info, user, root, parts)
        return FileResponse(info.path, media_type=info.mime, filename=info.name)

    routes = [
        Route("/healthz", healthz),
        Route("/static/{path:path}", static),
        Route("/theme/{name}/{path:path}", theme_file),
        Route("/login", login_form, methods=["GET"]),
        Route("/login", login_submit, methods=["POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/", home),
        Route("/settings", settings, methods=["GET", "POST"]),
        Route("/browse/{root}", browse),
        Route("/browse/{root}/{path:path}", browse),
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

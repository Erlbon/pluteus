"""The admin page: status, security (locks, known devices, sessions) and users, on its OWN port.

It is meant for the local network only. It is a separate listener (admin_port), so a reverse proxy such as Caddy that
only routes the main port never exposes it, and nothing here is reachable through the public address. Only users with
`admin = true` may sign in (HTTP Basic, the browser's own prompt), wrong passwords are counted by the same throttle as the
main site, every change needs a same-site request, and the page does nothing unless admin_port is set.

It shares the running server's state (users, sessions, the login throttle) with the main app, so "clear this lock" or
"sign this user out" act on the live server at once. User changes go to config.toml through the same checked editor as the
command line and the desktop app; the running server notices them within a few seconds.
"""
from __future__ import annotations

import functools
import logging
import os
import platform
import secrets
import shutil
import threading
import time
from urllib.parse import parse_qs, urlsplit

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Route

from . import __version__, adminweb, cbz, folderpick, uploads
from .admin import AdminModel
from .bodies import read_limited
from .app import _PLAIN_HOST as PLAIN_HOST
from .app import SecurityHeaders
from .config import ConfigError
from .fs import longpath
from .themes import DEFAULT
from .updatecheck import UpdateChecker
from .urls import Urls
from .useredit import EditError
from .web import Ctx

log = logging.getLogger("pluteus.admin")
BODY_LIMIT = 16384


def _first(form: dict, key: str) -> str:
    values = form.get(key) or [""]
    return values[0]


def create_admin_app(main_app) -> Starlette:
    st = main_app.state
    cfg, state, auth, limiter, themes = st.cfg, st.state, st.auth, st.limiter, st.themes
    config_path = st.config_path
    updates = UpdateChecker(cfg.update_repo) if cfg.update_check else None
    ctx = Ctx(Urls(""), "", cfg.theme if themes.exists(cfg.theme) else DEFAULT)
    challenge = {"WWW-Authenticate": 'Basic realm="pluteus admin", charset="UTF-8"'}

    # ---- who is asking ----------------------------------------------------------------------------------------------
    def csrf_ok(request) -> bool:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        origin = request.headers.get("origin") or request.headers.get("referer") or ""
        host = request.headers.get("host", "").lower()
        return bool(origin and host and urlsplit(origin).netloc.lower() == host)

    async def sign_in(request):
        """(user or None, seconds to wait). Same rules as the main site; no per-address limit here (a LAN page, and Docker
        hides the real addresses anyway)."""
        header = request.headers.get("authorization")
        if not header:
            return None, 0
        user = auth.cached_user(header)
        if user:
            return user, 0
        if auth.known_bad(header):
            return None, 0
        creds = auth.parse_basic(header)
        name = creds[0] if creds else ""
        wait = limiter.wait_seconds(name, "")
        if wait:
            trusted = auth.trusted_user(header)
            return (trusted, 0) if trusted else (None, wait)
        user = await run_in_threadpool(auth.authenticate, header)
        if user:
            limiter.success(name)
            return user, 0
        seconds, number = limiter.failure(name, "")
        if seconds:
            log.warning("admin page: login for %r is locked for %d s (lock number %d)", name[:40], seconds, number)
        return None, 0

    def site_link(request) -> str:
        """The normal pluteus site on the host name this page was opened with: its own port (site_port, else port) and
        base_path. Empty when the host name is odd, so the link can never be made to point somewhere else."""
        host = request.headers.get("host", "")
        host = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        if not PLAIN_HOST.fullmatch(host):
            return ""
        return "http://%s:%d%s/" % (host, cfg.site_port or cfg.port, cfg.base_path)

    def guarded(fn):
        @functools.wraps(fn)
        async def wrapper(request):
            user, wait = await sign_in(request)
            if wait:
                return PlainTextResponse("Too many wrong passwords. Try again in %d seconds." % wait, 429,
                                         headers={"Retry-After": str(wait)})
            if user is None:
                return PlainTextResponse("Sign in with an administrator account.", 401, headers=challenge)
            if not user.admin:
                return PlainTextResponse("This account may not use the admin page.", 403)
            if not csrf_ok(request):
                return PlainTextResponse("Cross-site request refused", 403)
            adminweb.current_site_link.set(site_link(request))
            return await fn(request, user)
        return wrapper

    async def form_of(request) -> dict:
        body = await read_limited(request, BODY_LIMIT)
        if body is None:
            return {}
        return parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)

    def done(where: str, code: str) -> RedirectResponse:
        return RedirectResponse("%s?ok=%s" % (where, code), 303)

    # ---- status -----------------------------------------------------------------------------------------------------------
    def disk(path: str):
        try:
            usage = shutil.disk_usage(longpath(path))
            return usage.free, usage.total
        except OSError:
            return None, None

    def collect_status() -> dict:
        roots = []
        for r in st.library.cfg.roots.values():
            free, total = disk(r.path)
            began = time.monotonic()
            ok = os.path.isdir(longpath(r.path))
            if ok:
                try:
                    with os.scandir(longpath(r.path)) as it:
                        next(it, None)
                except OSError:
                    ok = False
            roots.append({"name": r.name, "path": r.path, "ok": ok, "free": free, "total": total,
                          "seconds": time.monotonic() - began})
        up = None
        if cfg.uploads_dir:
            free, total = disk(cfg.uploads_dir)
            users = [{"name": u.name, "size": uploads.tree_size(os.path.join(cfg.uploads_dir, u.name))}
                     for u in auth.users.values() if u.upload and u.name != cfg.anonymous_user]
            up = {"dir": cfg.uploads_dir, "free": free, "total": total, "quota_mb": cfg.upload_quota_mb, "users": users}
        backups = os.path.join(cfg.data_dir, "backups") if cfg.data_dir else ""
        names = [n for n in os.listdir(backups) if n.startswith("state-")] if backups and os.path.isdir(backups) else []
        try:
            newest = max(os.path.getmtime(os.path.join(backups, n)) for n in names) if names else None
            size = os.path.getsize(state.path) if state.persistent else 0
        except OSError:
            newest, size = None, 0
        sessions = state.session_counts()
        devices = state.list_devices()
        locked = [r["name"] for r in limiter.snapshot() if r["locked_for"]]
        return {
            "update": updates.status() if updates else None,
            "bsdtar": bool(cbz.bsdtar()),
            "version": __version__, "python": platform.python_version(), "uptime": time.time() - st.started,
            "roots": roots, "uploads": up,
            "state": {"persistent": state.persistent, "path": state.path, "size": size, "backups": len(names), "newest_backup": newest},
            "counts": {"users": len(auth.users), "sessions": sum(sessions.values()),
                       "devices": len(devices), "locked": len(locked)},
            "names": {"users": sorted(auth.users), "sessions": dict(sessions),
                      "devices": {n: sum(1 for d in devices if d["username"] == n) for n in sorted({d["username"] for d in devices})},
                      "locked": sorted(locked)}}

    @guarded
    async def status(request, user):
        return HTMLResponse(adminweb.status_page(ctx, await run_in_threadpool(collect_status)))

    @guarded
    async def logout(request, user):
        """HTTP Basic has no real sign-out: forget the server's cached checks of this user, then answer 401 so the browser
        drops the login it holds (it may show its prompt once; cancelling it leaves this page)."""
        auth.forget_user(user.name)
        return HTMLResponse(adminweb.signed_out_page(ctx), 401, headers=challenge)

    # ---- security ---------------------------------------------------------------------------------------------------------
    @guarded
    async def security(request, user):
        locks = limiter.snapshot()
        devices = await run_in_threadpool(state.list_devices)
        sessions = await run_in_threadpool(state.session_counts)
        return HTMLResponse(adminweb.security_page(ctx, locks, devices, sessions, request.query_params.get("ok", "")))

    @guarded
    async def unlock(request, user):
        name = _first(await form_of(request), "name")
        cleared = limiter.unlock(name)
        log.info("admin %s cleared the lock on %r", user.name, name[:40])
        return done("/security", "unlocked" if cleared else "nothing")

    @guarded
    async def forget(request, user):
        device = _first(await form_of(request), "id")
        await run_in_threadpool(state.delete_device, device)
        log.info("admin %s forgot a known device", user.name)
        return done("/security", "forgot")

    @guarded
    async def signout(request, user):
        name = _first(await form_of(request), "name")
        await run_in_threadpool(state.delete_user_sessions, name)
        await run_in_threadpool(state.delete_user_devices, name)
        auth.forget_user(name)
        log.info("admin %s signed %r out everywhere", user.name, name[:40])
        return done("/security", "signedout")

    # ---- libraries --------------------------------------------------------------------------------------------------------
    def may_browse(request) -> bool:
        return folderpick.allowed(request.client.host if request.client else "", cfg.library_bases)

    def libraries_html(can_browse: bool, flash: str = "", error: str = "", status_code: int = 200):
        roots = []
        for r in st.library.cfg.roots.values():
            free, total = disk(r.path)
            roots.append({"name": r.name, "path": r.path, "ok": os.path.isdir(longpath(r.path)), "free": free, "total": total})
        return HTMLResponse(adminweb.libraries_page(ctx, roots, can_browse, bool(config_path), flash, error), status_code)

    @guarded
    async def libraries(request, user):
        return await run_in_threadpool(libraries_html, may_browse(request), request.query_params.get("ok", ""))

    async def library_change(request, user, action, code):
        if not config_path:
            return PlainTextResponse("No configuration file: libraries cannot be changed here.", 400)
        form = await form_of(request)
        can = may_browse(request)
        try:
            new_cfg = await run_in_threadpool(action, AdminModel(config_path), form)
        except (EditError, ConfigError) as e:
            return await run_in_threadpool(libraries_html, can, "", str(e), 400)
        except OSError as e:
            return await run_in_threadpool(libraries_html, can, "", "The configuration file cannot be written: %s" % e, 500)
        st.library.set_roots(new_cfg.roots)              # in use at once; a hand edit of the file is picked up by the reloader
        log.info("admin %s: libraries %s", user.name, code)
        return done("/libraries", code)

    @guarded
    async def library_add(request, user):
        return await library_change(request, user, lambda m, f: m.add_library(_first(f, "name"), _first(f, "path")), "libadded")

    @guarded
    async def library_remove(request, user):
        return await library_change(request, user, lambda m, f: m.remove_library(_first(f, "name")), "libremoved")

    @guarded
    async def library_browse(request, user):
        if not may_browse(request):
            return JSONResponse({"error": "The folder picker is only for a browser on this computer. Type the path instead."}, 403)
        try:
            return JSONResponse(await run_in_threadpool(folderpick.browse, request.query_params.get("path", ""), cfg.library_bases))
        except PermissionError as e:
            return JSONResponse({"error": "That folder cannot be shown: %s" % e}, 403)
        except FileNotFoundError:
            return JSONResponse({"error": "That folder does not exist."}, 404)

    # ---- users ------------------------------------------------------------------------------------------------------------
    def users_html(me: str, flash: str = "", error: str = "", status_code: int = 200, new_key=None, address: str = ""):
        if not config_path:
            return HTMLResponse(adminweb.users_page(ctx, [], [], me, can_edit=False), status_code)
        model = AdminModel(config_path)
        return HTMLResponse(adminweb.users_page(ctx, model.users(), model.root_names(), me, flash, error,
                                                keys=state.appkey_users(), new_key=new_key,
                                                address=address), status_code)

    @guarded
    async def users(request, user):
        try:
            return await run_in_threadpool(users_html, user.name, request.query_params.get("ok", ""))
        except (EditError, ConfigError, OSError) as e:
            return await run_in_threadpool(users_html_failed, user.name, str(e))

    def users_html_failed(me: str, message: str):
        return HTMLResponse(adminweb.page(ctx, "Users", "users", "", error="The users cannot be read: " + message), 500)

    def admins_after(model: AdminModel, name: str, new_admin: bool | None) -> int:
        """How many admins there would be if `name` were removed (new_admin None) or given the flag `new_admin`."""
        count = 0
        for u in model.users():
            admin = u.admin if u.name != name else bool(new_admin)
            if u.name == name and new_admin is None:
                continue
            count += 1 if admin else 0
        return count

    async def change(request, user, action):
        """Run one users-page action in a worker thread; show a refused one on the page itself (status 400)."""
        if not config_path:
            return PlainTextResponse("No configuration file: users cannot be changed here.", 400)
        form = await form_of(request)
        try:
            code = await run_in_threadpool(action, AdminModel(config_path), form)
        except (EditError, ConfigError) as e:
            return await run_in_threadpool(users_html, user.name, "", str(e), 400)
        except OSError as e:
            return await run_in_threadpool(users_html, user.name, "", "The configuration file cannot be written: %s" % e, 500)
        log.info("admin %s: users %s", user.name, code)
        return done("/users", code)

    def flags_of(form: dict) -> tuple:
        return "adult" in form, "upload" in form, "admin" in form

    def roots_of(form: dict) -> tuple:
        """The ticked folders; the 'All folders' box means every root (a new user starts with none)."""
        return ("*",) if "all" in form else tuple(form.get("root", []))

    @guarded
    async def add_user(request, user):
        def action(model, form):
            adult, upload, admin = flags_of(form)
            model.add_user(_first(form, "name"), _first(form, "password"), _first(form, "again"), roots_of(form),
                           adult, upload, admin)
            return "added"
        return await change(request, user, action)

    @guarded
    async def change_password(request, user):
        def action(model, form):
            model.change_password(_first(form, "name"), _first(form, "password"), _first(form, "again"))
            return "password"
        return await change(request, user, action)

    @guarded
    async def set_access(request, user):
        def action(model, form):
            name = _first(form, "name")
            adult, upload, admin = flags_of(form)
            if admins_after(model, name, admin) < 1:
                raise EditError("there must be at least one admin: this change would leave none")
            model.set_access(name, roots_of(form), adult, upload, admin)
            return "access"
        return await change(request, user, action)

    @guarded
    async def remove_user(request, user):
        def action(model, form):
            name = _first(form, "name")
            if name == user.name:
                raise EditError("you cannot remove yourself")
            if admins_after(model, name, None) < 1:
                raise EditError("the last admin cannot be removed")
            model.remove_user(name)
            state.delete_appkey(name)                     # a new user with the same name must not inherit the key
            auth.forget_user(name)
            return "removed"
        return await change(request, user, action)

    @guarded
    async def make_appkey(request, user):
        """A new music app key for one user; shown once, on the page that answers (no redirect, so it is not kept in a URL)."""
        if not config_path:
            return PlainTextResponse("No configuration file: users cannot be changed here.", 400)
        name = _first(await form_of(request), "name")
        known = {u.name for u in AdminModel(config_path).users()}
        if name not in known:
            return PlainTextResponse("No such user.", 400)
        secret = secrets.token_urlsafe(18)
        await run_in_threadpool(state.set_appkey, name, secret)
        auth.forget_user(name)                        # logins made with the old key stop at once
        log.info("admin %s: music app key made for %s", user.name, name)
        address = site_link(request).rstrip("/")                      # the site as this page was opened (its own port, not admin_port)
        return await run_in_threadpool(users_html, user.name, "", "", 200, (name, secret), address)

    @guarded
    async def revoke_appkey(request, user):
        name = _first(await form_of(request), "name")
        await run_in_threadpool(state.delete_appkey, name)
        auth.forget_user(name)
        log.info("admin %s: music app key revoked for %s", user.name, name)
        return done("/users", "keyrevoked")

    routes = [
        Route("/users/appkey", make_appkey, methods=["POST"]), Route("/users/appkey/revoke", revoke_appkey, methods=["POST"]),
        Route("/", status), Route("/logout", logout, methods=["POST"]), Route("/security", security),
        Route("/libraries", libraries), Route("/libraries/add", library_add, methods=["POST"]),
        Route("/libraries/remove", library_remove, methods=["POST"]), Route("/libraries/browse", library_browse),
        Route("/security/unlock", unlock, methods=["POST"]), Route("/security/forget", forget, methods=["POST"]),
        Route("/security/signout", signout, methods=["POST"]),
        Route("/users", users), Route("/users/add", add_user, methods=["POST"]),
        Route("/users/password", change_password, methods=["POST"]), Route("/users/access", set_access, methods=["POST"]),
        Route("/users/remove", remove_user, methods=["POST"]),
        *st.asset_routes,                      # stylesheets, icons and themes: public files, same as on the main site
    ]
    return Starlette(routes=routes, middleware=[Middleware(SecurityHeaders)])


class AdminServer:
    def __init__(self, server, thread: threading.Thread):
        self.server, self.thread = server, thread

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(5)


def start_admin_server(main_app) -> AdminServer:
    """Run the admin page in a background thread next to the main server (which keeps the main thread)."""
    import uvicorn
    cfg = main_app.state.cfg
    server = uvicorn.Server(uvicorn.Config(create_admin_app(main_app), host=cfg.admin_host, port=cfg.admin_port,
                                           log_level="info"))
    thread = threading.Thread(target=server.run, name="pluteus-admin", daemon=True)
    thread.start()
    return AdminServer(server, thread)

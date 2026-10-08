"""The Starlette application: routes, authentication guard, error mapping."""
from __future__ import annotations

import functools
import os
import time

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from starlette.routing import Route

from . import opds, web
from .auth import Authenticator
from .config import MIME_TYPES, Config
from .fs import Library, NotFound, PathError
from .urls import Urls, with_page


def _parts(path: str) -> tuple:
    path = path.rstrip("/")
    return tuple(path.split("/")) if path else ()


def _page_number(request: Request) -> int:
    try:
        return int(request.query_params.get("page", "1"))
    except ValueError:
        return 1


def create_app(cfg: Config) -> Starlette:
    lib = Library(cfg)
    auth = Authenticator(cfg.users, cfg.auth_cache_seconds)
    urls = Urls(cfg.base_path)
    challenge = {"WWW-Authenticate": 'Basic realm="%s", charset="UTF-8"' % cfg.realm}

    def authed(fn):
        @functools.wraps(fn)
        async def wrapper(request: Request):
            user = auth.authenticate(request.headers.get("authorization"))
            if user is None:
                return PlainTextResponse("Authentication required", 401, headers=challenge)
            try:
                return await fn(request, user)
            except PathError:
                return PlainTextResponse("Bad path", 400)
            except NotFound:
                return PlainTextResponse("Not found", 404)
        return wrapper

    async def healthz(request: Request):
        return PlainTextResponse("ok")

    @authed
    async def home(request: Request, user):
        roots = lib.roots_for(user)
        return HTMLResponse(web.roots_page(roots, urls, user.name))

    @authed
    async def browse(request: Request, user):
        root = request.path_params["root"]
        parts = _parts(request.path_params.get("path", ""))
        entries = await run_in_threadpool(lib.list_dir, user, root, parts)
        shown, page, pages = opds.paginate(entries, _page_number(request), cfg.page_size)
        return HTMLResponse(web.listing_page(root, parts, shown, page, pages, len(entries), urls))

    @authed
    async def opds_root(request: Request, user):
        roots = lib.roots_for(user)
        now = time.time()
        entries = [opds.nav_entry(r.name, "urn:bookshelf:" + r.name, urls.opds(r.name), now) for r in roots]
        body = opds.build_feed(title="Library", feed_id="urn:bookshelf:root", self_href=urls.opds(),
                               start_href=urls.opds(), updated=now, entries=entries)
        return Response(body, media_type=opds.NAV_TYPE)

    @authed
    async def opds_folder(request: Request, user):
        root = request.path_params["root"]
        parts = _parts(request.path_params.get("path", ""))
        listing = await run_in_threadpool(lib.list_dir, user, root, parts)
        shown, page, pages = opds.paginate(listing, _page_number(request), cfg.page_size)
        here = urls.opds(root, parts)
        entries = []
        for e in shown:
            eid = "urn:bookshelf:" + root + ":" + "/".join(e.parts)
            if e.is_dir:
                entries.append(opds.nav_entry(e.display, eid, urls.opds(root, e.parts), e.mtime))
            else:
                mime = MIME_TYPES.get(os.path.splitext(e.name)[1].lower(), "application/octet-stream")
                entries.append(opds.book_entry(e.name.rsplit(".", 1)[0], eid,
                                               urls.download(root, e.parts), mime, e.mtime, e.size))
        up = urls.opds(root, parts[:-1]) if parts else urls.opds()
        updated = max((e.mtime for e in listing), default=time.time())
        body = opds.build_feed(
            title=lib.display_name(parts[-1]) if parts else root,
            feed_id="urn:bookshelf:" + root + ":" + "/".join(parts),
            self_href=with_page(here, page), start_href=urls.opds(), updated=updated,
            entries=entries, up_href=up,
            next_href=with_page(here, page + 1) if page < pages else None,
            prev_href=with_page(here, page - 1) if page > 1 else None)
        kind = opds.ACQ_TYPE if any(not e.is_dir for e in shown) else opds.NAV_TYPE
        return Response(body, media_type=kind)

    @authed
    async def download(request: Request, user):
        root = request.path_params["root"]
        parts = _parts(request.path_params["path"])
        info = await run_in_threadpool(lib.file_info, user, root, parts)
        return FileResponse(info.path, media_type=info.mime, filename=info.name)

    routes = [
        Route("/healthz", healthz),
        Route("/", home),
        Route("/browse/{root}", browse),
        Route("/browse/{root}/{path:path}", browse),
        Route("/opds", opds_root),
        Route("/opds/", opds_root),
        Route("/opds/{root}", opds_folder),
        Route("/opds/{root}/{path:path}", opds_folder),
        Route("/dl/{root}/{path:path}", download),
    ]
    app = Starlette(routes=routes)
    app.state.library = lib
    return app

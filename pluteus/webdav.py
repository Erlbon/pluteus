"""Read-only WebDAV (class 1): Windows Explorer, macOS Finder, Android file managers, Kodi and the like can mount the library.

Only OPTIONS, HEAD, GET and PROPFIND are answered; every method that would change something gets 405, so nothing here can write.
It shows exactly what the web listing shows (the same folders, rights, markers and file types) and files are always sent as
downloads, never as pages of this site.
"""
from __future__ import annotations

import email.utils
import time
from urllib.parse import quote
from xml.sax.saxutils import escape

from starlette.responses import FileResponse, PlainTextResponse, RedirectResponse, Response

from .fs import NotFound, clamp_time

ALLOW = "OPTIONS, HEAD, GET, PROPFIND"
HEADERS = {"DAV": "1", "MS-Author-Via": "DAV", "Allow": ALLOW}
MAX_ENTRIES = 5000


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _prop(name: str, mtime: float, size: int, mime: str, folder: bool) -> str:
    when = clamp_time(mtime)
    props = "<D:displayname>%s</D:displayname>" % escape(name)
    props += "<D:resourcetype><D:collection/></D:resourcetype>" if folder else "<D:resourcetype/>"
    props += "<D:creationdate>%s</D:creationdate><D:getlastmodified>%s</D:getlastmodified>" % (
        _iso(when), email.utils.formatdate(when, usegmt=True))
    if not folder:
        props += "<D:getcontentlength>%d</D:getcontentlength><D:getcontenttype>%s</D:getcontenttype>" % (size, escape(mime))
    return props


def _response(href: str, props: str) -> str:
    return ("<D:response><D:href>%s</D:href><D:propstat><D:prop>%s</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>"
            "</D:response>" % (escape(href), props))


def _multistatus(items: list) -> Response:
    xml = '<?xml version="1.0" encoding="utf-8"?><D:multistatus xmlns:D="DAV:">%s</D:multistatus>' % "".join(items)
    return Response(xml, 207, media_type="application/xml", headers={**HEADERS, "Cache-Control": "no-store"})


class Dav:
    def __init__(self, cfg, lib, urls):
        self.cfg, self.lib, self.urls = cfg, lib, urls

    def href(self, parts, folder: bool) -> str:
        """/dav/ROOT/a/b, percent-encoded, with the proxy base path and a trailing slash on folders."""
        path = self.urls.base + "/dav" + "".join("/" + quote(p, safe="") for p in parts)
        return path + "/" if folder or not parts else path

    def handle(self, request, user) -> Response:
        """Synchronous (the caller runs it in a worker thread): disk access only."""
        method = request.method
        if method == "OPTIONS":
            return Response(status_code=200, headers=HEADERS)
        if method not in ("HEAD", "GET", "PROPFIND"):
            return PlainTextResponse("This WebDAV share is read-only.", 405, headers=HEADERS)
        parts = tuple(p for p in request.path_params.get("path", "").split("/") if p)
        if method == "PROPFIND":
            depth = request.headers.get("depth", "1")
            return self.propfind(user, parts, 0 if depth == "0" else 1)
        return self.get(user, parts)

    def _folder(self, user, parts):
        """The listing of a folder (None for a file)."""
        if not parts:
            return None
        return self.lib.list_dir(user, parts[0], parts[1:], self.cfg.show_all_files, True)

    def propfind(self, user, parts, depth: int) -> Response:
        now = time.time()
        if not parts:                                               # the top: one folder per library
            roots = self.lib.roots_for(user)
            items = [_response(self.href((), True), _prop("", now, 0, "", True))]
            if depth:
                items += [_response(self.href((r.name,), True), _prop(r.name, now, 0, "", True)) for r in roots]
            return _multistatus(items)
        root, below = parts[0], parts[1:]
        if not self.lib.has_root(user, root):
            return PlainTextResponse("Not found", 404, headers=HEADERS)
        try:
            entries = self._folder(user, parts)
        except NotFound:                                            # not a folder: it may be a file
            if not below:
                raise
            info = self.lib.file_info(user, root, below, self.cfg.show_all_files)
            return _multistatus([_response(self.href(parts, False), _prop(info.name, info.mtime, info.size, info.mime, False))])
        items = [_response(self.href(parts, True), _prop(parts[-1], now, 0, "", True))]
        if depth:
            for e in entries[:MAX_ENTRIES]:
                child = parts + (e.name,)
                if e.is_dir:
                    items.append(_response(self.href(child, True), _prop(e.name, e.mtime, 0, "", True)))
                else:
                    mime = self._mime(e.name)
                    items.append(_response(self.href(child, False), _prop(e.name, e.mtime, e.size, mime, False)))
        return _multistatus(items)

    @staticmethod
    def _mime(name: str) -> str:
        import mimetypes
        return mimetypes.guess_type(name)[0] or "application/octet-stream"

    def get(self, user, parts) -> Response:
        if not parts or len(parts) == 1:                            # a library or the top: the browser page is the useful answer
            return RedirectResponse(self.urls.browse(parts[0]) if parts else self.urls.home(), 303, headers=HEADERS)
        root, below = parts[0], parts[1:]
        try:
            info = self.lib.file_info(user, root, below, self.cfg.show_all_files)
        except NotFound:
            self.lib.folder_path(user, root, below)                  # a folder the user may open: send the browser page
            return RedirectResponse(self.urls.browse(root, below), 303, headers=HEADERS)
        return FileResponse(info.path, media_type=info.mime, filename=info.name,
                            headers={**HEADERS, "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=60"})

"""Markdown files shown as a page. Safe by construction: every piece of raw HTML in the file is escaped (so it is shown as
text, never run), links may only go to http, https, mailto or to other files in the same library, and images may only
be files in the same library (a remote image would be a tracking pixel, and the CSP blocks it anyway).

Rendering is done by mistune (small, pure Python). A very large file is not rendered (the caller shows it as text).
"""
from __future__ import annotations

import posixpath
import re
from html import escape
from urllib.parse import quote, unquote

import mistune

from . import kinds

MAX_RENDER_BYTES = 256 * 1024
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")
_TAGS = re.compile(r"<[^>]+>")


class Resolver:
    """Turns the URLs written in a Markdown file into URLs of this server, or None when they must not be used."""

    def __init__(self, urls, root: str, folder: tuple):
        self.urls, self.root, self.folder = urls, root, tuple(folder)

    def _local_parts(self, url: str):
        """(parts below the root, fragment) for a relative file reference that stays inside the library, else None."""
        if url.startswith(("/", "\\")) or "\\" in url:
            return None
        path, _, fragment = url.partition("#")
        path = unquote(path.partition("?")[0])
        if not path:
            return None
        joined = posixpath.normpath(posixpath.join("/".join(self.folder), path)) if self.folder else posixpath.normpath(path)
        parts = tuple(p for p in joined.split("/") if p not in ("", "."))
        if not parts or ".." in parts or any("\x00" in p or ":" in p for p in parts):
            return None
        return parts, fragment

    def link(self, url: str):
        url = (url or "").strip()
        if not url:
            return None
        if url.startswith("#"):
            return url
        scheme = _SCHEME.match(url)
        if scheme:
            return url if scheme.group(1).lower() in ("http", "https", "mailto") else None
        if url.startswith("//"):
            return None
        found = self._local_parts(url)
        if found is None:
            return None
        parts, fragment = found
        return self.urls.view(self.root, parts) + ("#" + quote(fragment) if fragment else "")

    def image(self, url: str):
        url = (url or "").strip()
        if _SCHEME.match(url) or url.startswith("//"):
            return None                                   # remote images are never fetched
        found = self._local_parts(url)
        if found is None or kinds.extension(found[0][-1]) not in kinds.IMAGE:
            return None
        return self.urls.raw(self.root, found[0])


class _Renderer(mistune.HTMLRenderer):
    def __init__(self, resolver: Resolver):
        super().__init__(escape=True)
        self.resolver = resolver

    def link(self, text, url, title=None):
        target = self.resolver.link(url)
        if target is None:
            return text                                    # an unsafe or unusable link is just its words
        extra = " target='_blank' rel='noopener noreferrer'" if target.startswith(("http://", "https://")) else ""
        tip = " title='%s'" % escape(title, quote=True) if title else ""
        return "<a href='%s'%s%s>%s</a>" % (escape(target, quote=True), tip, extra, text)

    def image(self, text, url, title=None):
        alt = escape(_TAGS.sub("", text or ""))
        target = self.resolver.image(url)
        if target is None:
            return "<span class='pl-md-noimage'>[image: %s]</span>" % (alt or "no description")
        tip = " title='%s'" % escape(title, quote=True) if title else ""
        return "<img src='%s' alt='%s'%s loading='lazy'>" % (escape(target, quote=True), alt, tip)


class TooLarge(Exception):
    pass


def render(text: str, urls, root: str, folder: tuple) -> str:
    """HTML for a Markdown text. Raises TooLarge for text the renderer should not be asked to handle."""
    if len(text.encode("utf-8", "ignore")) > MAX_RENDER_BYTES:
        raise TooLarge("larger than %d KB" % (MAX_RENDER_BYTES // 1024))
    md = mistune.create_markdown(renderer=_Renderer(Resolver(urls, root, folder)),
                                 plugins=["table", "strikethrough", "task_lists", "url"])
    try:
        return md(text)
    except RecursionError:                                 # absurdly nested lists or quotes
        raise TooLarge("too deeply nested")

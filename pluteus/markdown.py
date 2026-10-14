"""Markdown files shown as a page. Safe by construction: every piece of raw HTML in the file is escaped (so it is shown as
text, never run), links may only go to http, https, mailto or to other files in the same library, and images may only
be files in the same library (a remote image would be a tracking pixel, and the CSP blocks it anyway).

Rendering is done by mistune (small, pure Python). A very large file is not rendered (the caller shows it as text).
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from html import escape, unescape
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
    def __init__(self, resolver: Resolver, anchors: bool = False, wiki=None):
        super().__init__(escape=True)
        self.resolver, self.anchors, self.wiki = resolver, anchors, wiki
        self.headings: list = []                           # (level, plain text, id) for the contents list
        self._ids: dict = {}

    def heading(self, text, level, **attrs):
        if not self.anchors:
            return super().heading(text, level, **attrs)
        plain = _TAGS.sub("", text)
        base = slug(unescape(plain)) or "section"
        n = self._ids.get(base, 0)
        self._ids[base] = n + 1
        anchor = base if n == 0 else "%s-%d" % (base, n)
        self.headings.append((level, unescape(plain), anchor))
        return ("<h%d id='%s'>%s <a class='pl-anchor' href='#%s' aria-label='Link to this heading'>#</a></h%d>\n"
                % (level, escape(anchor, quote=True), text, escape(anchor, quote=True), level))

    def wikilink(self, name, label=None):
        shown = escape(label or name)
        target = self.wiki(name.strip()) if self.wiki else None
        if not target:
            return shown                                   # not found: plain words, never a dead link
        return "<a class='pl-wiki' href='%s'>%s</a>" % (escape(target, quote=True), shown)

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


_WIKI = r"\[\[(?P<wiki>[^\[\]\n|]{1,120}?)(?:\|(?P<label>[^\[\]\n]{1,120}))?\]\]"


def _wiki_plugin(md) -> None:
    def parse(inline, m, state):
        label = m.group("label")
        state.append_token({"type": "wikilink", "raw": m.group("wiki"), "attrs": {"label": label.strip() if label else None}})
        return m.end()
    md.inline.register("wikilink", _WIKI, parse, before="link")


def slug(text: str) -> str:
    """A heading's anchor: lower case, words joined by '-', letters of any language kept (GitHub style)."""
    return re.sub(r"[\s_]+", "-", re.sub(r"[^\w\s-]", "", text.strip().lower())).strip("-")[:80]


_FRONT_KEY = re.compile(r"^([A-Za-z_][\w-]{0,40})\s*:\s*(.*)$")
_FRONT_ITEM = re.compile(r"^\s*-\s+(.*)$")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def front_matter(text: str) -> tuple:
    """(meta, body): a `---` block at the very top (title, tags, ...) read as simple `key: value` lines, `key: [a, b]` or `- item` lists, and
    the text without it. Anything that is not a clean block is left alone and shown as it is. Keys are lower case; a value is a string or a list."""
    text = text.lstrip("﻿")
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n", 80)[:80]
    if lines[0].rstrip() != "---":
        return {}, text
    meta, current = {}, None
    for number, line in enumerate(lines[1:], start=1):
        line = line.rstrip("\r")
        if line.strip() in ("---", "..."):
            return meta, "\n".join(text.split("\n")[number + 1:]).lstrip("\n")
        item = _FRONT_ITEM.match(line)
        if item and current is not None:
            value = meta[current]
            if isinstance(value, str):
                value = meta[current] = [value] if value else []
            value.append(_unquote(item.group(1)))
            continue
        found = _FRONT_KEY.match(line)
        if not found:
            if line.strip():
                return {}, text                            # not front matter after all
            continue
        current = found.group(1).lower()
        value = found.group(2).strip()
        if value.startswith("[") and value.endswith("]"):
            meta[current] = [_unquote(v) for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[current] = _unquote(value)
    return {}, text                                        # no closing line


@dataclass
class Rendered:
    html: str
    meta: dict = field(default_factory=dict)
    title: str = ""                                        # the front matter title, else the first heading


def render_full(text: str, urls, root: str, folder: tuple, *, toc: bool = True, wiki=None) -> Rendered:
    """A whole document: front matter read and hidden, headings with anchors, a contents list for a long one, wiki links resolved by `wiki`
    (a function name -> url or None). Raises TooLarge like render()."""
    if len(text.encode("utf-8", "ignore")) > MAX_RENDER_BYTES:
        raise TooLarge("larger than %d KB" % (MAX_RENDER_BYTES // 1024))
    meta, body = front_matter(text)
    renderer = _Renderer(Resolver(urls, root, folder), anchors=True, wiki=wiki)
    md = mistune.create_markdown(renderer=renderer, plugins=["table", "strikethrough", "task_lists", "url", _wiki_plugin])
    try:
        html = md(body)
    except RecursionError:                                 # absurdly nested lists or quotes
        raise TooLarge("too deeply nested")
    entries = [h for h in renderer.headings if h[0] <= 3]
    if toc and len(entries) >= 4:
        items = "".join("<li class='pl-toc-%d'><a href='#%s'>%s</a></li>" % (lv, escape(a, quote=True), escape(t)) for lv, t, a in entries)
        html = "<nav class='pl-toc' aria-label='Contents'><strong>Contents</strong><ul>%s</ul></nav>%s" % (items, html)
    title = str(meta.get("title") or "") or (renderer.headings[0][1] if renderer.headings else "")
    return Rendered(html, meta, title)


def render(text: str, urls, root: str, folder: tuple) -> str:
    """HTML for a short Markdown text (a folder's note): front matter hidden, no anchors or contents. Raises TooLarge for text the
    renderer should not be asked to handle."""
    if len(text.encode("utf-8", "ignore")) > MAX_RENDER_BYTES:
        raise TooLarge("larger than %d KB" % (MAX_RENDER_BYTES // 1024))
    md = mistune.create_markdown(renderer=_Renderer(Resolver(urls, root, folder)),
                                 plugins=["table", "strikethrough", "task_lists", "url"])
    try:
        return md(front_matter(text)[1])
    except RecursionError:
        raise TooLarge("too deeply nested")

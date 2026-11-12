"""Markdown files shown as a page. Safe by construction: every piece of raw HTML in the file is escaped (so it is shown as
text, never run), links may only go to http, https, mailto or to other files in the same library, and images may only
be files in the same library (a remote image would be a tracking pixel, and the CSP blocks it anyway).

Rendering is done by mistune (small, pure Python). A very large file is not rendered (the caller shows it as text).

A file can be written to make the renderer slow (a long run of "[a](" takes time that grows with the square of its length), and a
note is rendered on every visit to its folder. So: a rendering is kept, keyed on the text itself, and made once (a small, bounded
cache); only one text is rendered at a time; and a rendering that takes longer than RENDER_SECONDS is stopped and the text shown
plain instead (that answer is kept too, so the cost is paid once).
"""
from __future__ import annotations

import copy
import hashlib
import posixpath
import re
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from html import escape, unescape
from urllib.parse import quote, unquote

import mistune

from . import kinds

MAX_RENDER_BYTES = 256 * 1024
RENDER_SECONDS = 3.0                  # the time one text gets (a 250 KB file of ordinary Markdown takes about half a second)
CACHE_ITEMS = 64                      # renderings kept ...
CACHE_BYTES = 8 * 1024 * 1024         # ... and their size together (counted in characters)
MAX_KEY_TEXT = 300                    # characters of a heading or a list item that are looked at to recognise a section or a release
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")
_TAGS = re.compile(r"<[^>]+>")
_GROUP = re.compile(r"[(\[][^)\]]*[)\]]")
# Where a release's badge goes in a kept rendering: the zero bytes cannot come from the file (they are taken out of the text first).
_BADGE = re.compile("\x00pl-rel:([^\x00]*)\x00")


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


# Headings whose list items are releases of a band or artist: the note names them, pluteus says which ones the folders have (see release_key).
RELEASE_HEADINGS = {"discography", "studio albums", "albums", "live albums", "live", "compilations", "compilation albums", "eps", "ep", "singles",
                    "singles and eps", "eps and singles", "remix albums", "remixes", "soundtracks", "box sets", "video albums", "other releases", "releases"}


def release_key(text: str) -> str:
    """What a release title and an album folder name share: lower case, "&" as "and", a leading year and any (year) or [year] group left out,
    everything that is not a letter or digit as one space. "Speak & Spell (1981)" and "[1981] - Speak And Spell" are the same key."""
    text = unescape(text[:MAX_KEY_TEXT * 4])[:MAX_KEY_TEXT].lower().replace("&", " and ")      # capped: the patterns below are slow on long lines
    for apostrophe in ("'", "’", "‘", "`"):                 # What's Up? and Whats Up are the same title
        text = text.replace(apostrophe, "")
    text = _GROUP.sub(" ", text)
    text = re.sub(r"^\W*\d{4}\W+(?=\w)", "", text.strip())
    return re.sub(r"[\W_]+", " ", text).strip()


class _Renderer(mistune.HTMLRenderer):
    def __init__(self, resolver: Resolver, anchors: bool = False, releases: bool = False):
        super().__init__(escape=True)
        self.resolver, self.anchors = resolver, anchors
        self.releases = releases                          # mark the list items that name a release (see _with_badges)
        self.section = ""
        self.headings: list = []                           # (level, plain text, id) for the contents list
        self._ids: dict = {}

    def list_item(self, text):
        if self.releases and self.section in RELEASE_HEADINGS:
            title = " ".join(_TAGS.sub("", text).split())[:MAX_KEY_TEXT]
            key = release_key(re.sub(r" [-–—] .*$", "", title)) if title else ""
            if key:
                text = text.rstrip("\n") + " \x00pl-rel:%s\x00" % key       # which folders the library has is asked on every call, not kept
        return super().list_item(text)

    def heading(self, text, level, **attrs):
        plain = unescape(_TAGS.sub("", text))[:MAX_KEY_TEXT]             # capped: the pattern is slow on a very long heading
        self.section = " ".join(_GROUP.sub(" ", plain).split()).lower()
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


# ---- one rendering at a time, within a time budget, and kept ------------------------------------------------------------------
_PLUGINS = ["table", "strikethrough", "task_lists", "url"]
_RENDER = threading.Lock()                                 # held while a text is being rendered
_cache: OrderedDict = OrderedDict()                        # key -> (outcome, value, size), the most recently used last
_cache_lock = threading.Lock()
_cache_size = 0


class _OverBudget(BaseException):
    """Raised inside the renderer when its time is up. Not an Exception, so nothing in the renderer swallows it."""


def clear_cache() -> None:
    global _cache_size
    with _cache_lock:
        _cache.clear()
        _cache_size = 0


def _within(seconds: float, work):
    """work(), stopped with _OverBudget when it runs longer than `seconds`. The renderer is plain Python, so it cannot be stopped
    from outside; the clock is looked at as it goes, on every 64th function call it makes (sys.settrace, this thread only, put
    back afterwards)."""
    deadline = time.monotonic() + seconds
    calls = 0

    def watch(frame, event, arg):
        nonlocal calls
        calls += 1
        if not calls & 63 and time.monotonic() > deadline:
            raise _OverBudget()
        return None                                        # no line-by-line tracing: only calls are counted

    before = sys.gettrace()
    sys.settrace(watch)
    try:
        return work()
    finally:
        sys.settrace(before)


def _key(kind: str, text: str, urls, root: str, folder: tuple) -> tuple:
    """What a rendering depends on: the text itself, where its relative links start from, and how this server writes its addresses."""
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).digest()
    return kind, digest, root, tuple(folder), urls.view(root, ("x",)), urls.raw(root, ("x",))


def _rendered(key: tuple, work) -> tuple:
    """(outcome, value): ("ok", what work() returned), ("slow", None) when it ran out of time, ("nested", None) when the text is nested
    too deeply, ("busy", None) when another text was being rendered for the whole wait. All but "busy" are kept."""
    global _cache_size
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit[:2]
    if not _RENDER.acquire(timeout=RENDER_SECONDS):
        return "busy", None
    try:
        with _cache_lock:
            hit = _cache.get(key)                          # made by the request we waited for
        if hit is not None:
            return hit[:2]
        try:
            outcome, value, size = "ok", *_within(RENDER_SECONDS, work)
        except _OverBudget:
            outcome, value, size = "slow", None, 0
        except RecursionError:                             # absurdly nested lists or quotes
            outcome, value, size = "nested", None, 0
    finally:
        _RENDER.release()
    with _cache_lock:
        if size <= CACHE_BYTES // 4:
            old = _cache.pop(key, None)
            _cache_size += size - (old[2] if old else 0)
            _cache[key] = (outcome, value, size)
        while _cache and (len(_cache) > CACHE_ITEMS or _cache_size > CACHE_BYTES):
            _cache_size -= _cache.popitem(last=False)[1][2]
    return outcome, value


def _plain(text: str) -> str:
    """The text as it is, escaped: what is shown when the renderer could not finish in time."""
    return "<pre class='pl-md-plain'>%s</pre>" % escape(text)


def _with_badges(html: str, releases) -> str:
    def badge(match) -> str:
        folder = releases(match.group(1))
        if folder:
            return "<span class='pl-have' title='%s'>in library</span>" % escape(folder, quote=True)
        return "<span class='pl-missing'>missing</span>"
    return _BADGE.sub(badge, html)


def render_full(text: str, urls, root: str, folder: tuple, *, toc: bool = True) -> Rendered:
    """A whole document: front matter read and hidden, headings with anchors, a contents list for a long one. Raises TooLarge like
    render(), and also when the text cannot be rendered within RENDER_SECONDS (the caller then shows it in the plain text viewer)."""
    if len(text.encode("utf-8", "ignore")) > MAX_RENDER_BYTES:
        raise TooLarge("larger than %d KB" % (MAX_RENDER_BYTES // 1024))
    text = text.replace("\x00", "")

    def work() -> tuple:
        meta, body = front_matter(text)
        renderer = _Renderer(Resolver(urls, root, folder), anchors=True)
        html = mistune.create_markdown(renderer=renderer, plugins=_PLUGINS)(body)
        entries = [h for h in renderer.headings if h[0] <= 3]
        if toc and len(entries) >= 4:
            items = "".join("<li class='pl-toc-%d'><a href='#%s'>%s</a></li>" % (lv, escape(a, quote=True), escape(t)) for lv, t, a in entries)
            html = "<nav class='pl-toc' aria-label='Contents'><strong>Contents</strong><ul>%s</ul></nav>%s" % (items, html)
        title = str(meta.get("title") or "") or (renderer.headings[0][1] if renderer.headings else "")
        return (html, meta, title), len(html)

    outcome, value = _rendered(_key("full-toc" if toc else "full", text, urls, root, folder), work)
    if outcome == "nested":
        raise TooLarge("too deeply nested")
    if outcome != "ok":
        raise TooLarge("takes too long to render")
    html, meta, title = value
    return Rendered(html, copy.deepcopy(meta), title)


def render(text: str, urls, root: str, folder: tuple, releases=None) -> str:
    """HTML for a short Markdown text (a folder's note): front matter hidden, no anchors or contents. `releases`: None, or a function
    key -> folder name / None that says which releases the folders have (asked on every call; the rendering itself is kept). Raises
    TooLarge for text the renderer should not be asked to handle; a text that cannot be rendered within RENDER_SECONDS comes back as
    it is, escaped."""
    if len(text.encode("utf-8", "ignore")) > MAX_RENDER_BYTES:
        raise TooLarge("larger than %d KB" % (MAX_RENDER_BYTES // 1024))
    text = text.replace("\x00", "")
    marked = releases is not None

    def work() -> tuple:
        renderer = _Renderer(Resolver(urls, root, folder), releases=marked)
        html = mistune.create_markdown(renderer=renderer, plugins=_PLUGINS)(front_matter(text)[1])
        return html, len(html)

    outcome, html = _rendered(_key("note-releases" if marked else "note", text, urls, root, folder), work)
    if outcome == "nested":
        raise TooLarge("too deeply nested")
    if outcome != "ok":
        return _plain(front_matter(text)[1])
    return _with_badges(html, releases) if marked else html

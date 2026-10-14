"""The "about" text other tools already write into a folder, read when the folder has no note of its own:
`artist.nfo` and `album.nfo` (Kodi, Plex, Jellyfin, Lidarr and MusicBrainz Picard), and Calibre's `metadata.opf` (the book's description).

Only the text is taken (a biography, a review, a description), as plain paragraphs: nothing in these files is ever run, linked or fetched. The XML
is read with the standard library after a check that refuses documents that declare entities (so an "entity bomb" is never expanded), and
the file size is capped.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html import unescape

MAX_BYTES = 256 * 1024
FILES = ("artist.nfo", "album.nfo", "metadata.opf")
_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"(?i)<\s*(?:br\s*/?|/p|/div|/li|/h[1-6])\s*>")


def _safe_root(raw: bytes):
    if len(raw) > MAX_BYTES or b"<!ENTITY" in raw or b"<!entity" in raw.lower():
        return None
    try:
        return ET.fromstring(raw)
    except (ET.ParseError, ValueError):
        return None


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _children(root, *names) -> list:
    wanted = set(names)
    return [(_local(e.tag), (e.text or "").strip()) for e in root.iter() if _local(e.tag) in wanted and (e.text or "").strip()]


def clean_text(text: str) -> str:
    """Plain paragraphs from text that may hold a little HTML (Calibre descriptions do): tags dropped, line breaks kept as paragraphs."""
    text = _BREAK.sub("\n\n", text)
    text = unescape(_TAG.sub("", text))
    paragraphs = [re.sub(r"[ \t\r\f\v]+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    return "\n\n".join(p for p in paragraphs if p)


def summary(name: str, raw: bytes) -> str:
    """The text of one of FILES as plain paragraphs, or '' when there is none or the file is not usable."""
    root = _safe_root(raw)
    if root is None:
        return ""
    lower = name.lower()
    if lower == "metadata.opf":
        body = [t for tag, t in _children(root, "description")]
        return clean_text(body[0]) if body else ""
    # Kodi style: <artist><biography>, <album><review> or <overview>; the first of them that has text
    kind = {"artist.nfo": ("biography", "overview", "description"), "album.nfo": ("review", "overview", "biography", "description")}.get(lower)
    if kind is None:
        return ""
    found = {tag: t for tag, t in _children(root, *kind)}
    text = next((clean_text(found[k]) for k in kind if k in found and clean_text(found[k])), "")
    if not text:
        return ""
    facts = []
    genres = [t for tag, t in _children(root, "genre") if t][:6]
    if genres:
        facts.append("Genre: " + ", ".join(genres))
    for label, tags in (("Formed", ("formed", "born")), ("Disbanded", ("disbanded", "died")), ("Year", ("year",))):
        values = [t for tag, t in _children(root, *tags)][:1]
        if values and len(values[0]) <= 40:
            facts.append("%s: %s" % (label, values[0]))
    return text + (("\n\n" + " · ".join(facts)) if facts else "")


def as_html(text: str) -> str:
    """The summary as safe HTML paragraphs."""
    from html import escape
    return "".join("<p>%s</p>" % escape(p).replace("\n", "<br>") for p in text.split("\n\n") if p.strip())

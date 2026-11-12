"""The "about" text other tools already write into a folder, read when the folder has no note of its own:
`artist.nfo` and `album.nfo` (Kodi, Plex, Jellyfin, Lidarr and MusicBrainz Picard), and Calibre's `metadata.opf` (the book's description).

Only the text is taken (a biography, a review, a description), as plain paragraphs: nothing in these files is ever run, linked or fetched. The XML
is read with the standard library after a check that refuses documents that declare a document type or entities (so an "entity bomb" is never
expanded; see safe_xml, which the EPUB readers use too), and the file size is capped.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html import unescape

MAX_BYTES = 256 * 1024
FILES = ("artist.nfo", "album.nfo", "metadata.opf")
_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"(?i)<\s*(?:br\s*/?|/p|/div|/li|/h[1-6])\s*>")


_DECLARED = re.compile(rb"""\s*<\?xml[^>]{0,200}?encoding\s*=\s*["']([A-Za-z0-9._-]{1,40})["']""")
_ONE_BYTE = {"us-ascii": "ascii", "ascii": "ascii", "iso-8859-1": "latin-1", "latin-1": "latin-1", "latin1": "latin-1",
             "iso-8859-15": "iso-8859-15", "windows-1252": "cp1252", "cp1252": "cp1252"}


def xml_text(raw: bytes) -> str:
    """The bytes of an XML file as text, decoded here and not by the parser: UTF-8 (the default), UTF-16 (by its byte order mark, or by
    the zero bytes around the first '<') or one of a few Western one-byte encodings when the file declares it. Raises ValueError for any
    other encoding and for bytes that are not what they claim, so nothing can hide from safe_xml's check behind an encoding."""
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8")
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw[:2] == b"<\x00":
        return raw.decode("utf-16-le")
    if raw[:2] == b"\x00<":
        return raw.decode("utf-16-be")
    declared = _DECLARED.match(raw[:300])
    name = declared.group(1).decode("ascii").lower() if declared else "utf-8"
    if name in ("utf-8", "utf8"):
        return raw.decode("utf-8")
    if name not in _ONE_BYTE:
        raise ValueError("an encoding that is not read here")
    return raw.decode(_ONE_BYTE[name], "replace")


def safe_xml(raw: bytes, limit: int = MAX_BYTES):
    """The root element of an XML document from an untrusted file. Raises ValueError when it is larger than `limit`, declares a document
    type or entities (checked on the decoded text, so UTF-16 hides nothing; an "entity bomb" is never expanded) or does not parse."""
    if len(raw) > limit:
        raise ValueError("too large")
    text = xml_text(raw)
    lower = text.lower()
    if "<!doctype" in lower or "<!entity" in lower:
        raise ValueError("declares a document type or entities")
    try:
        return ET.fromstring(text)                         # text, not bytes: the parser then ignores the declared encoding
    except ET.ParseError as e:
        raise ValueError("not well-formed XML: %s" % e)


def _safe_root(raw: bytes):
    try:
        return safe_xml(raw)
    except ValueError:
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


# ---- the description inside a single book file: an EPUB's package file, a CBZ's ComicInfo.xml ---------------------------------------
def _member(zf, wanted: str):
    """The bytes of an archive member by name (any letter case, at most MAX_BYTES), or None."""
    for info in zf.infolist():
        if info.filename.lower() == wanted.lower():
            if info.file_size > MAX_BYTES:
                return None
            with zf.open(info) as f:
                return f.read(MAX_BYTES + 1)
    return None


def from_book(path: str, name: str) -> str:
    """The description or summary written inside an .epub (dc:description) or .cbz (ComicInfo.xml Summary) as plain paragraphs, or ''."""
    from posixpath import normpath
    from urllib.parse import unquote

    from . import ziputil
    lower = name.lower()
    try:
        with ziputil.open_zip(path) as zf:
            if lower.endswith(".epub"):
                container = _safe_root(_member(zf, "META-INF/container.xml") or b"")
                if container is None:
                    return ""
                rootfile = next((e for e in container.iter() if _local(e.tag) == "rootfile" and e.get("full-path")), None)
                if rootfile is None:
                    return ""
                raw = _member(zf, normpath(unquote(rootfile.get("full-path"))))
                return summary("metadata.opf", raw) if raw else ""
            if lower.endswith(".cbz"):
                raw = _member(zf, "ComicInfo.xml")
                root = _safe_root(raw) if raw else None
                if root is None:
                    return ""
                found = [t for tag, t in _children(root, "summary")]
                return clean_text(found[0]) if found else ""
    except Exception:                                      # noqa: BLE001  a damaged book simply has no description
        return ""
    return ""

"""OPDS 1.2 (Atom) feeds: one navigation feed per folder, books as acquisition entries."""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET

ATOM = "http://www.w3.org/2005/Atom"
OPDS = "http://opds-spec.org/2010/catalog"
DC = "http://purl.org/dc/terms/"
ET.register_namespace("", ATOM)
ET.register_namespace("opds", OPDS)
ET.register_namespace("dc", DC)
PSE = "http://vaemendis.net/opds-pse/ns"                    # OPDS Page Streaming Extension (spec v1.2)
PSE_STREAM_REL = "http://vaemendis.net/opds-pse/stream"
PSE_TYPES = ("image/jpeg", "image/png", "image/gif")         # the only link types the spec allows
ET.register_namespace("pse", PSE)

NAV_TYPE = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQ_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition"
ACQUISITION_REL = "http://opds-spec.org/acquisition"


def _q(tag: str) -> str:
    return "{%s}%s" % (ATOM, tag)


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _text(parent, tag: str, text: str):
    el = ET.SubElement(parent, _q(tag))
    el.text = text
    return el


def _link(parent, rel: str, href: str, type_: str):
    ET.SubElement(parent, _q("link"), {"rel": rel, "href": href, "type": type_})


def nav_entry(title: str, entry_id: str, href: str, updated: float) -> ET.Element:
    e = ET.Element(_q("entry"))
    _text(e, "title", title)
    _text(e, "id", entry_id)
    _text(e, "updated", _iso(updated))
    _link(e, "subsection", href, NAV_TYPE)
    return e


def stream_mime(first_page_mime: str) -> str:
    """PSE only allows jpeg/png/gif as the link type; clients decode by the real bytes, so JPEG is the safe default."""
    return first_page_mime if first_page_mime in PSE_TYPES else "image/jpeg"


IMAGE_REL = "http://opds-spec.org/image"
THUMB_REL = "http://opds-spec.org/image/thumbnail"


def book_entry(title: str, entry_id: str, href: str, mime: str, updated: float, size: int,
               meta: dict | None = None, stream: dict | None = None, cover: tuple | None = None) -> ET.Element:
    meta = meta or {}
    e = ET.Element(_q("entry"))
    _text(e, "title", meta.get("title") or title)
    _text(e, "id", entry_id)
    _text(e, "updated", _iso(updated))
    for name in meta.get("authors", ()):
        _text(ET.SubElement(e, _q("author")), "name", name)
    if meta.get("language"):
        ET.SubElement(e, "{%s}language" % DC).text = meta["language"]
    if meta.get("year"):
        ET.SubElement(e, "{%s}issued" % DC).text = meta["year"]
    if meta.get("series"):
        label = meta["series"] + (" #" + meta["series_index"] if meta.get("series_index") else "")
        ET.SubElement(e, _q("content"), {"type": "text"}).text = label
    ET.SubElement(e, "{%s}format" % DC).text = mime
    ET.SubElement(e, _q("link"), {"rel": ACQUISITION_REL, "href": href, "type": mime,
                                  "length": str(size)})
    if cover:                                           # (small picture url, larger picture url), both JPEG
        ET.SubElement(e, _q("link"), {"rel": THUMB_REL, "href": cover[0], "type": "image/jpeg"})
        ET.SubElement(e, _q("link"), {"rel": IMAGE_REL, "href": cover[1], "type": "image/jpeg"})
    if stream:
        # OPDS-PSE: read this comic page by page. href holds the literal {pageNumber} (0-based) for the client to fill in.
        attrs = {"rel": PSE_STREAM_REL, "href": stream["href"], "type": stream_mime(stream["mime"]),
                 "{%s}count" % PSE: str(stream["count"])}
        if stream.get("last_read"):
            attrs["{%s}lastRead" % PSE] = str(stream["last_read"])                 # 1-based, as the spec says
            attrs["{%s}lastReadDate" % PSE] = _iso(stream["last_read_date"])
        ET.SubElement(e, _q("link"), attrs)
    return e


def build_feed(*, title: str, feed_id: str, self_href: str, start_href: str, updated: float,
               entries: list, up_href: str | None = None, next_href: str | None = None,
               prev_href: str | None = None) -> bytes:
    has_books = any(e.find(_q("link")).get("rel") == ACQUISITION_REL for e in entries)
    kind = ACQ_TYPE if has_books else NAV_TYPE
    feed = ET.Element(_q("feed"))
    _text(feed, "id", feed_id)
    _text(feed, "title", title)
    _text(feed, "updated", _iso(updated))
    _link(feed, "self", self_href, kind)
    _link(feed, "start", start_href, NAV_TYPE)
    if up_href:
        _link(feed, "up", up_href, NAV_TYPE)
    if prev_href:
        _link(feed, "previous", prev_href, kind)
    if next_href:
        _link(feed, "next", next_href, kind)
    for e in entries:
        feed.append(e)
    return ET.tostring(feed, encoding="utf-8", xml_declaration=True)


def paginate(items: list, page: int, size: int):
    """Return (slice, page, pages) with page clamped into range."""
    pages = max(1, -(-len(items) // size))
    page = min(max(1, page), pages)
    return items[(page - 1) * size: page * size], page, pages

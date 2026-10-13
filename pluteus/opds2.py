"""OPDS 2.0 (JSON) feeds: the same folders and books as the OPDS 1.2 (Atom) feeds, picked by the client's Accept header
(`application/opds+json`) so that nothing changes for apps that only know the Atom feeds."""
from __future__ import annotations

import json
import time

TYPE = "application/opds+json"
ACQUISITION_REL = "http://opds-spec.org/acquisition"
PSE_STREAM_REL = "http://vaemendis.net/opds-pse/stream"


def wants(request) -> bool:
    """True when the client asks for OPDS 2.0 (Accept header; ?format=opds2 does the same for a person with a browser)."""
    return TYPE in request.headers.get("accept", "") or request.query_params.get("format") == "opds2"


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def nav_link(title: str, href: str) -> dict:
    return {"title": title, "href": href, "type": TYPE, "rel": "subsection"}


def publication(title: str, identifier: str, href: str, mime: str, updated: float, meta: dict | None = None,
                stream: dict | None = None, cover: tuple | None = None) -> dict:
    meta = meta or {}
    info = {"@type": "http://schema.org/Book", "identifier": identifier, "title": meta.get("title") or title,
            "modified": _iso(updated)}
    if meta.get("authors"):
        info["author"] = [{"name": n} for n in meta["authors"]]
    if meta.get("language"):
        info["language"] = meta["language"]
    if meta.get("year"):
        info["published"] = meta["year"]
    if meta.get("series"):
        series = {"name": meta["series"]}
        try:
            series["position"] = float(meta["series_index"]) if meta.get("series_index") else None
        except ValueError:
            series["position"] = None
        info["belongsTo"] = {"series": [{k: v for k, v in series.items() if v is not None}]}
    links = [{"rel": ACQUISITION_REL, "href": href, "type": mime}]
    if stream:
        # Page streaming for comics (the OPDS-PSE extension): href holds the literal {pageNumber} (0-based) for the client to fill in.
        props = {"numberOfItems": stream["count"]}
        if stream.get("last_read"):
            props["lastRead"] = stream["last_read"]
            props["lastReadDate"] = _iso(stream["last_read_date"])
        links.append({"rel": PSE_STREAM_REL, "href": stream["href"], "type": stream["mime"], "properties": props,
                      "templated": True})
    result = {"metadata": info, "links": links}
    if cover:
        result["images"] = [{"href": cover[1], "type": "image/jpeg"}, {"href": cover[0], "type": "image/jpeg", "width": 160}]
    return result


def feed(*, title: str, self_href: str, start_href: str, navigation: list, publications: list, up_href: str | None = None,
         next_href: str | None = None, prev_href: str | None = None, page: int = 1, per_page: int = 0, total: int = 0) -> bytes:
    links = [{"rel": "self", "href": self_href, "type": TYPE}, {"rel": "start", "href": start_href, "type": TYPE}]
    for rel, href in (("up", up_href), ("previous", prev_href), ("next", next_href)):
        if href:
            links.append({"rel": rel, "href": href, "type": TYPE})
    metadata = {"title": title}
    if total:
        metadata.update({"numberOfItems": total, "itemsPerPage": per_page, "currentPage": page})
    body = {"metadata": metadata, "links": links}
    if navigation:
        body["navigation"] = navigation
    if publications:
        body["publications"] = publications
    return json.dumps(body, ensure_ascii=False).encode("utf-8")

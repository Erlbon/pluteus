"""EPUB metadata (title, authors, series, language, year) read from the OPF inside the book.

Only the books on the page being shown are read, never a whole library, and results
are kept in a disposable SQLite cache keyed on path + size + mtime (WAL mode). The
cache can never break serving: any cache or parse problem falls back to "no metadata".
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote

from .dbutil import open_database

log = logging.getLogger("pluteus.meta")
_SCHEMA = "CREATE TABLE IF NOT EXISTS meta (path TEXT PRIMARY KEY, size INTEGER, mtime REAL, data TEXT);"

NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container",
      "opf": "http://www.idpf.org/2007/opf",
      "dc": "http://purl.org/dc/elements/1.1/"}
OPF_ROLE = "{http://www.idpf.org/2007/opf}role"
MAX_XML_BYTES = 2 * 1024 * 1024        # refuse absurdly large container/OPF files (zip bombs)
_READ_ERRORS = (OSError, zipfile.BadZipFile, KeyError, ValueError, ET.ParseError, RuntimeError,
                NotImplementedError, AttributeError, OverflowError)


def _read_member(zf: zipfile.ZipFile, names: dict, name: str) -> bytes:
    real = names[name.lower()]
    if zf.getinfo(real).file_size > MAX_XML_BYTES:
        raise ValueError("member too large")
    return zf.read(real)


def _text(el) -> str:
    return " ".join((el.text or "").split()) if el is not None else ""


def _number(text: str) -> str:
    try:
        value = float(text)
    except ValueError:
        return text.strip()
    return str(int(value)) if value == int(value) else str(value)


def parse_opf(opf: ET.Element) -> dict:
    md = opf.find("opf:metadata", NS)
    if md is None:
        return {}
    out: dict = {}
    title = next((t for t in (_text(e) for e in md.findall("dc:title", NS)) if t), "")
    if title:
        out["title"] = title
    authors = []
    for e in md.findall("dc:creator", NS):
        role = e.get(OPF_ROLE)
        name = _text(e)
        if name and role in (None, "", "aut") and name not in authors:
            authors.append(name)
    if authors:
        out["authors"] = authors
    lang = next((t for t in (_text(e) for e in md.findall("dc:language", NS)) if t), "")
    if lang:
        out["language"] = lang
    date = next((t for t in (_text(e) for e in md.findall("dc:date", NS)) if t), "")
    m = re.match(r"\d{4}", date)
    if m:
        out["year"] = m.group(0)
    # Series: Calibre's meta tags first, then EPUB 3 collections.
    metas = md.findall("opf:meta", NS)
    by_name = {e.get("name"): e.get("content") for e in metas if e.get("name")}
    if by_name.get("calibre:series"):
        out["series"] = by_name["calibre:series"].strip()
        if by_name.get("calibre:series_index"):
            out["series_index"] = _number(by_name["calibre:series_index"])
    else:
        refines: dict = {}
        for e in metas:
            ref = (e.get("refines") or "").lstrip("#")
            if ref and e.get("property"):
                refines.setdefault(ref, {})[e.get("property")] = _text(e)
        for e in metas:
            if e.get("property") == "belongs-to-collection" and _text(e):
                info = refines.get(e.get("id") or "", {})
                if info.get("collection-type", "series") == "series":
                    out["series"] = _text(e)
                    if info.get("group-position"):
                        out["series_index"] = _number(info["group-position"])
                    break
    return out


def read_epub_metadata(path: str) -> dict | None:
    """Metadata dict (possibly empty), or None when the file is not a readable EPUB."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = {n.lower(): n for n in zf.namelist()}
            container = ET.fromstring(_read_member(zf, names, "META-INF/container.xml"))
            rootfile = container.find(".//c:rootfile", NS)
            opf_path = unquote(rootfile.get("full-path"))
            return parse_opf(ET.fromstring(_read_member(zf, names, opf_path)))
    except _READ_ERRORS:
        return None


class MetaCache:
    """SQLite (WAL) cache of parsed metadata. cache_dir '' keeps it in memory only."""

    def __init__(self, cache_dir: str = ""):
        self._lock = threading.Lock()
        self._path = os.path.join(cache_dir, "metadata.sqlite3") if cache_dir else ":memory:"
        # Disposable by design: a damaged file is set aside and a new cache started.
        self._db = open_database(self._path, _SCHEMA, log=log)

    def get(self, path: str, size: int, mtime: float):
        """Cached dict, None for a miss. A cached 'unreadable' result comes back as {}."""
        try:
            with self._lock:
                row = self._db.execute("SELECT size, mtime, data FROM meta WHERE path=?", (path,)).fetchone()
        except sqlite3.Error:
            return None
        if row and row[0] == size and row[1] == mtime:
            try:
                return json.loads(row[2])
            except ValueError:                      # a corrupt row is just a cache miss
                return None
        return None

    def put(self, path: str, size: int, mtime: float, data: dict) -> None:
        try:
            with self._lock, self._db:
                self._db.execute("INSERT OR REPLACE INTO meta VALUES (?,?,?,?)",
                                 (path, size, mtime, json.dumps(data, ensure_ascii=False)))
        except sqlite3.Error:
            pass

    def close(self) -> None:
        with self._lock:
            self._db.close()


class MetaService:
    def __init__(self, cache: MetaCache, workers: int = 8, enabled: bool = True):
        self.cache = cache
        self.enabled = enabled
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="meta")

    def _one(self, entry) -> dict:
        hit = self.cache.get(entry.path, entry.size, entry.mtime)
        if hit is not None:
            return hit
        data = read_epub_metadata(entry.path) or {}
        self.cache.put(entry.path, entry.size, entry.mtime, data)
        return data

    def pages_for(self, info) -> list:
        """Page names of a CBZ, through the same disposable cache (keyed on path + size + mtime).
        Raises cbz.CbzError for an unreadable archive; failures are not cached."""
        from . import cbz
        key = "cbz:" + info.path
        hit = self.cache.get(key, info.size, info.mtime)
        if hit and hit.get("pages"):
            return hit["pages"]
        pages = cbz.list_pages(info.path)
        self.cache.put(key, info.size, info.mtime, {"pages": pages})
        return pages

    def lookup_pages(self, entries) -> dict:
        """{entry.path: page names} for the CBZ entries given; unreadable archives are left out. Parallel, cached."""
        from .cbz import CbzError
        comics = [e for e in entries if not e.is_dir and e.name.lower().endswith(".cbz")]

        def one(entry):
            try:
                return self.pages_for(entry)
            except CbzError:
                return None
        return {e.path: pages for e, pages in zip(comics, self._pool.map(one, comics)) if pages}

    def for_file(self, info) -> dict:
        """Metadata for one file (anything with path, size and mtime), through the same cache."""
        return self._one(info) if self.enabled else {}

    def lookup_many(self, entries) -> dict:
        """{entry.path: metadata dict} for the EPUB entries given ({} for other types)."""
        if not self.enabled:
            return {}
        epubs = [e for e in entries if not e.is_dir and e.name.lower().endswith(".epub")]
        return dict(zip((e.path for e in epubs), self._pool.map(self._one, epubs)))

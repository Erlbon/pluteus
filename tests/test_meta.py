"""EPUB metadata reading, the SQLite cache, and how both show up in feeds and pages."""
import dataclasses
import os
import xml.etree.ElementTree as ET

import pytest
from epub_factory import make_epub
from starlette.testclient import TestClient

import bookshelf.meta as meta
from bookshelf.app import create_app
from bookshelf.fs import Entry

ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/terms/}"


# ---- parsing ---------------------------------------------------------------
def test_reads_calibre_style_metadata(tmp_path):
    p = make_epub(tmp_path / "a.epub", title="Dune", authors=("Frank Herbert", "Brian Herbert"),
                  language="en-US", date="1965-08-01T00:00:00+00:00",
                  calibre_series="Dune Chronicles", series_index="1.0")
    assert meta.read_epub_metadata(p) == {
        "title": "Dune", "authors": ["Frank Herbert", "Brian Herbert"], "language": "en-US",
        "year": "1965", "series": "Dune Chronicles", "series_index": "1"}


def test_reads_epub3_collection_series(tmp_path):
    p = make_epub(tmp_path / "a.epub", collection="The Expanse", position="2.5")
    got = meta.read_epub_metadata(p)
    assert got["series"] == "The Expanse" and got["series_index"] == "2.5"


def test_illustrators_are_not_authors(tmp_path):
    p = make_epub(tmp_path / "a.epub", authors=("Some Illustrator",), creator_role="ill")
    assert "authors" not in meta.read_epub_metadata(p)


def test_container_may_point_to_a_root_level_opf(tmp_path):
    p = make_epub(tmp_path / "a.epub", title="Flat", opf_dir="")
    assert meta.read_epub_metadata(p)["title"] == "Flat"


def test_unreadable_files_return_none(tmp_path):
    fake = tmp_path / "fake.epub"
    fake.write_bytes(b"EPUBDATA" * 50)
    assert meta.read_epub_metadata(str(fake)) is None
    assert meta.read_epub_metadata(str(tmp_path / "missing.epub")) is None
    import zipfile
    empty = tmp_path / "empty.epub"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("hello.txt", "no container here")
    assert meta.read_epub_metadata(str(empty)) is None
    broken = tmp_path / "broken.epub"
    with zipfile.ZipFile(broken, "w") as z:
        z.writestr("META-INF/container.xml", "<not xml")
    assert meta.read_epub_metadata(str(broken)) is None


def test_oversized_opf_is_refused(tmp_path):
    p = make_epub(tmp_path / "bomb.epub", pad_bytes=meta.MAX_XML_BYTES + 10)
    assert meta.read_epub_metadata(p) is None


# ---- cache -------------------------------------------------------------------
def _entry(path, size=10, mtime=1.0):
    return Entry(os.path.basename(path), os.path.basename(path), False, size, mtime, (), path)


def test_cache_hits_and_invalidates_on_change(tmp_path, monkeypatch):
    calls = []
    real = meta.read_epub_metadata
    monkeypatch.setattr(meta, "read_epub_metadata", lambda p: calls.append(p) or real(p))
    p = make_epub(tmp_path / "a.epub", title="One")
    svc = meta.MetaService(meta.MetaCache(""))
    e = _entry(p)
    assert svc.lookup_many([e])[p]["title"] == "One"
    assert svc.lookup_many([e])[p]["title"] == "One"
    assert len(calls) == 1                                    # second lookup came from the cache
    changed = dataclasses.replace(e, mtime=2.0)
    svc.lookup_many([changed])
    assert len(calls) == 2                                    # a new mtime forces a re-read


def test_unreadable_epub_is_cached_as_empty(tmp_path, monkeypatch):
    fake = tmp_path / "fake.epub"
    fake.write_bytes(b"x" * 20)
    calls = []
    real = meta.read_epub_metadata
    monkeypatch.setattr(meta, "read_epub_metadata", lambda p: calls.append(p) or real(p))
    svc = meta.MetaService(meta.MetaCache(""))
    e = _entry(str(fake), 20)
    assert svc.lookup_many([e]) == {str(fake): {}}
    svc.lookup_many([e])
    assert len(calls) == 1


def test_cache_survives_restart_and_recovers_from_damage(tmp_path):
    cache_dir = str(tmp_path / "cache")
    c1 = meta.MetaCache(cache_dir)
    c1.put("p", 5, 1.0, {"title": "Kept"})
    c1.close()
    c2 = meta.MetaCache(cache_dir)
    assert c2.get("p", 5, 1.0) == {"title": "Kept"}
    assert c2.get("p", 6, 1.0) is None
    c2.close()
    db_file = os.path.join(cache_dir, "metadata.sqlite3")
    for suffix in ("-wal", "-shm"):
        if os.path.exists(db_file + suffix):
            os.remove(db_file + suffix)
    with open(db_file, "wb") as f:
        f.write(b"this is definitely not a sqlite database" * 100)
    c3 = meta.MetaCache(cache_dir)                            # must not raise
    assert c3.get("p", 5, 1.0) is None
    c3.put("q", 1, 1.0, {"title": "Fresh"})
    assert c3.get("q", 1, 1.0) == {"title": "Fresh"}
    assert any(n.startswith("metadata.sqlite3.corrupt-") for n in os.listdir(cache_dir))
    c3.close()


def test_disabled_service_reads_nothing(tmp_path):
    p = make_epub(tmp_path / "a.epub")
    svc = meta.MetaService(meta.MetaCache(""), enabled=False)
    assert svc.lookup_many([_entry(p)]) == {}


# ---- feeds and pages -----------------------------------------------------------
GOOD = "/opds/books/Good%20Author"


def _entries(client, headers, url=GOOD):
    r = client.get(url, headers=headers)
    assert r.status_code == 200
    return ET.fromstring(r.content).findall(ATOM + "entry")


def test_opds_entries_carry_title_author_series_language(client, alice):
    e = {x.find(ATOM + "title").text: x for x in _entries(client, alice)}
    real = e["The Real Title"]
    assert real.find(ATOM + "author").find(ATOM + "name").text == "Good Author"
    assert real.find(ATOM + "content").text == "Great Saga #3"
    assert real.find(DC + "language").text == "no"
    assert real.find(DC + "issued").text == "2011"
    assert real.find(ATOM + "link").get("href").endswith("/Real%20Book.epub")
    # a book with no usable metadata falls back to its file name
    assert "Plain Filename" in e and e["Plain Filename"].find(ATOM + "author") is None


def test_html_listing_shows_title_and_author(client, alice):
    html = client.get("/browse/books/Good%20Author", headers=alice).text
    assert "The Real Title" in html and "Good Author" in html and "Great Saga" in html
    assert "Plain Filename.epub" in html


def test_metadata_can_be_switched_off(cfg, alice):
    off = TestClient(create_app(dataclasses.replace(cfg, metadata=False)))
    titles = [x.find(ATOM + "title").text for x in _entries(off, alice)]
    assert titles == ["Plain Filename", "Real Book"]

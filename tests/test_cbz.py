"""The comic reader: page index, page endpoint, reader page, progress, and the limits that protect the server."""
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from cbz_factory import make_cbz, png
from starlette.testclient import TestClient
from test_login_web import HTML, login, make, post

from pluteus import cbz
from pluteus.cbz import CbzError

STATIC = Path(__file__).resolve().parent.parent / "pluteus" / "static"
RED, GREEN, BLUE, GREY = png((255, 0, 0)), png((0, 255, 0)), png((0, 0, 255)), png((120, 120, 120))
COMIC = "Plain/Comic%20One.cbz"
PAGES = "/cbz/comics/" + COMIC
READ = "/read/comics/" + COMIC


@pytest.fixture
def comic_app(cfg, tmp_path, library):
    # A real comic in the 'comics' library: pages stored out of order, plus archive clutter that must be skipped.
    make_cbz(library / "comics" / "Plain" / "Comic One.cbz", [
        ("page10.png", BLUE), ("page2.png", GREEN), ("page1.png", RED),
        ("__MACOSX/page1.png", GREY), (".hidden.png", GREY), ("Thumbs.db", GREY), ("notes.txt", b"hello"),
        ("ComicInfo.xml", b"<ComicInfo/>"), ("extras/", b""), ("extras/bonus.png", GREY)])
    client, app = make(cfg, tmp_path)
    login(client)
    return client, app


# ---- the archive ------------------------------------------------------------------------------------------
def test_pages_are_images_only_in_natural_order(tmp_path):
    path = make_cbz(tmp_path / "a.cbz", [("p10.jpg", b"x"), ("p2.JPG", b"x"), ("p1.png", b"x"), ("ch2/p1.webp", b"x"),
                                          ("ch1/p9.gif", b"x"), ("ch1/p10.gif", b"x"), ("__MACOSX/p1.png", b"x"),
                                          (".DS_Store", b"x"), ("Thumbs.db", b"x"), ("readme.txt", b"x"), ("d/", b"")])
    assert cbz.list_pages(path) == ["ch1/p9.gif", "ch1/p10.gif", "ch2/p1.webp", "p1.png", "p2.JPG", "p10.jpg"]


@pytest.mark.parametrize("entries,problem", [([], "no images"), ([("notes.txt", b"x")], "no images")])
def test_archives_without_pages_are_refused(tmp_path, entries, problem):
    with pytest.raises(CbzError, match=problem):
        cbz.list_pages(make_cbz(tmp_path / "a.cbz", entries))


def test_a_file_that_is_not_a_zip_or_does_not_exist_is_refused(tmp_path):
    fake = tmp_path / "fake.cbz"
    fake.write_bytes(b"this is not a zip file" * 10)
    for path in (str(fake), str(tmp_path / "missing.cbz")):
        with pytest.raises(CbzError):
            cbz.list_pages(path)
    with pytest.raises(CbzError):
        cbz.read_page(str(fake), "p1.png")


def test_too_many_pages_and_huge_pages_are_refused(tmp_path, monkeypatch):
    path = make_cbz(tmp_path / "a.cbz", [("p%d.png" % i, b"x") for i in range(6)])
    monkeypatch.setattr(cbz, "MAX_PAGES", 5)
    with pytest.raises(CbzError, match="more than 5"):
        cbz.list_pages(path)
    big = make_cbz(tmp_path / "b.cbz", [("p1.png", b"x" * 5000)])
    with pytest.raises(CbzError, match="larger than"):
        cbz.read_page(big, "p1.png", limit=1000)
    assert cbz.read_page(big, "p1.png", limit=5000) == b"x" * 5000


def test_the_size_limit_holds_even_when_the_zip_header_lies(tmp_path):
    path = make_cbz(tmp_path / "a.cbz", [("p1.png", b"y" * 5000)])
    # claim the entry is tiny: the real data is bigger than the limit and must still be refused
    raw = bytearray(Path(path).read_bytes())
    marker = b"p1.png"
    for sig in (b"PK\x03\x04", b"PK\x01\x02"):
        at = 0
        while (at := raw.find(sig, at)) != -1:
            offset = 22 if sig == b"PK\x03\x04" else 24                 # uncompressed-size field
            raw[at + offset:at + offset + 4] = (10).to_bytes(4, "little")
            at += 4
    Path(path).write_bytes(bytes(raw))
    with pytest.raises(CbzError):
        cbz.read_page(path, "p1.png", limit=1000)


def test_encrypted_entries_are_not_pages(tmp_path):
    path = make_cbz(tmp_path / "a.cbz", [("p1.png", b"x"), ("p2.png", b"x")])
    raw = bytearray(Path(path).read_bytes())
    at = raw.find(b"PK\x01\x02")                                           # set the 'encrypted' bit on the first entry
    raw[at + 8] |= 1
    Path(path).write_bytes(bytes(raw))
    assert cbz.list_pages(path) == ["p2.png"]


def test_mime_types():
    assert cbz.mime_for("a/b.JPG") == "image/jpeg" and cbz.mime_for("x.webp") == "image/webp"
    assert cbz.mime_for("noextension") == "application/octet-stream"


# ---- the page endpoint ---------------------------------------------------------------------------------------
def test_pages_come_out_in_order_with_their_own_bytes_and_type(comic_app):
    client, _ = comic_app
    # natural order of the full path: 'extras/bonus.png' first, then page1, page2, page10 (not page10 before page2)
    for n, data in enumerate((GREY, RED, GREEN, BLUE)):
        r = client.get("%s?page=%d" % (PAGES, n))
        assert r.status_code == 200 and r.content == data, n
        assert r.headers["content-type"] == "image/png"
    assert client.get(PAGES).content == GREY                                 # no ?page= means the first page


def test_page_responses_are_cacheable_and_revalidate(comic_app):
    client, _ = comic_app
    first = client.get(PAGES + "?page=1")
    assert first.headers["cache-control"] == "private, max-age=86400" and first.headers["etag"]
    again = client.get(PAGES + "?page=1", headers={"if-none-match": first.headers["etag"]})
    assert again.status_code == 304 and again.content == b""
    other = client.get(PAGES + "?page=2")
    assert other.headers["etag"] != first.headers["etag"]


@pytest.mark.parametrize("query,status", [("?page=4", 404), ("?page=99", 404), ("?page=-1", 404), ("?page=abc", 400),
                                           ("?page=1.5", 400), ("?page=", 400)])
def test_bad_page_numbers(comic_app, query, status):
    client, _ = comic_app
    assert client.get(PAGES + query).status_code == status


def test_the_page_endpoint_is_only_for_comics_you_may_see(comic_app, library):
    client, app = comic_app
    assert client.get("/cbz/books/Good%20Author/Real%20Book.epub?page=0").status_code == 404      # not a CBZ
    assert client.get("/cbz/comics/Plain/notes.txt?page=0").status_code == 404
    assert client.get("/cbz/comics/Adult%20Pub/Naughty%20%282000%29/n.cbz?page=0").status_code == 404   # adult folder
    assert client.get("/cbz/comics/%2e%2e/%2e%2e/secret.txt?page=0").status_code in (400, 404)
    anon = TestClient(app, follow_redirects=False)
    r = anon.get(PAGES + "?page=0")
    assert r.status_code == 401 and "www-authenticate" not in r.headers                           # no browser login popup for <img>
    assert anon.get(PAGES + "?page=0", headers={"authorization": "Basic " + __import__("base64").b64encode(b"alice:pw-alice").decode()}).status_code == 200
    carol = TestClient(app, follow_redirects=False)
    login(carol, "carol", "pw-carol")
    assert carol.get(PAGES + "?page=0").status_code == 200                                         # carol has the comics root
    bob = TestClient(app, follow_redirects=False)
    login(bob, "bob", "pw-bob")
    assert bob.get("/cbz/comics/Adult%20Pub/Naughty%20%282000%29/n.cbz?page=0").status_code == 422   # allowed, but a fake zip


def test_a_broken_comic_gives_a_polite_error_not_a_crash(comic_app):
    client, _ = comic_app
    r = client.get("/cbz/comics/3%20-%20Marvel%20%281961-2012%29/Spider-Man%20%281963-%29/Amazing%20001.cbz?page=0")
    assert r.status_code == 422 and "Cannot read" in r.text or "not a readable" in r.text


def test_the_page_list_is_cached_until_the_file_changes(comic_app, library, monkeypatch):
    client, _ = comic_app
    calls = []
    real = cbz.list_pages
    monkeypatch.setattr(cbz, "list_pages", lambda p: calls.append(p) or real(p))
    client.get(PAGES + "?page=0"); client.get(PAGES + "?page=1"); client.get(READ, headers=HTML)
    assert len(calls) == 1                                                  # one read of the central directory
    path = library / "comics" / "Plain" / "Comic One.cbz"
    make_cbz(path, [("only.png", BLUE)])
    import os
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 10))
    assert client.get(PAGES + "?page=0").content == BLUE and len(calls) == 2
    assert client.get(PAGES + "?page=1").status_code == 404                  # the new file has a single page


# ---- the reader page ----------------------------------------------------------------------------------------------
def test_comic_reader_page(comic_app):
    client, _ = comic_app
    r = client.get(READ, headers=HTML)
    assert r.status_code == 200
    html = r.text
    assert "id='reader'" in html and "pl-comic" in html and "pl-reader-page" in html
    assert "data-pages='/cbz/comics/Plain/Comic%20One.cbz'" in html
    assert "data-count='4'" in html                                             # four pages (clutter skipped)
    assert "data-progress='/api/progress/comics/Plain/Comic%20One.cbz'" in html
    assert "<img class='pl-page' id='page'" in html and "reader-comic.js" in html
    assert "epub.min.js" not in html                                            # none of the EPUB machinery
    assert "href='/browse/comics/Plain'" in html and ">Comic One<" in html
    for control in ("fit-btn", "fs-btn", "id='prev'", "id='next'", "seek", "pl-scheme"):
        assert control in html


def test_comic_reader_needs_a_login_and_refuses_broken_archives(comic_app, library):
    client, app = comic_app
    anon = TestClient(app, follow_redirects=False)
    assert anon.get(READ, headers=HTML).status_code == 303
    r = client.get("/read/comics/3%20-%20Marvel%20%281961-2012%29/Spider-Man%20%281963-%29/Amazing%20001.cbz", headers=HTML)
    assert r.status_code == 422 and "download" in r.text.lower()
    assert client.get("/read/comics/Plain/Nope.cbz", headers=HTML).status_code == 404


def test_the_comic_script_parses_and_is_served(comic_app):
    client, _ = comic_app
    js = client.get("/static/reader-comic.js")
    assert js.status_code == 200 and "page:" in js.text and "cfg.progress" in js.text
    if shutil.which("node"):
        done = subprocess.run(["node", "--check", str(STATIC / "reader-comic.js")], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr


# ---- progress and the home page ----------------------------------------------------------------------------------------
def test_comic_progress_roundtrip_and_continue_reading(comic_app):
    client, app = comic_app
    api = "/api/progress/comics/" + COMIC
    r = client.put(api, content=json.dumps({"locator": "page:2", "percent": 0.75}),
                   headers={"content-type": "application/json", "origin": "http://testserver"})
    assert r.status_code == 200
    assert client.get(api).json()["locator"] == "page:2"
    home = client.get("/", headers=HTML).text
    assert "Continue reading" in home and "75%" in home and "href='/read/comics/Plain/Comic%20One.cbz'" in home
    app.state.state.put_progress("alice", "comics", "Plain/Comic One.cbz", "page:3", 1.0)
    assert "Continue reading" not in client.get("/", headers=HTML).text        # finished comics drop off the list
    listing = client.get("/browse/comics/Plain", headers=HTML).text
    assert "100%" in listing

"""OPDS page streaming (PSE spec v1.2): the stream link in the feed, and a client's view of it."""
import base64
import dataclasses
import xml.etree.ElementTree as ET

import pytest
from cbz_factory import make_cbz, png
from starlette.testclient import TestClient

from pluteus import cbz, opds
from pluteus.app import create_app

PSE = "{http://vaemendis.net/opds-pse/ns}"
ATOM = "{http://www.w3.org/2005/Atom}"
STREAM_REL = "http://vaemendis.net/opds-pse/stream"
ACQ_REL = "http://opds-spec.org/acquisition"
RED, GREEN, BLUE, GREY = png((255, 0, 0)), png((0, 255, 0)), png((0, 0, 255)), png((120, 120, 120))
AUTH = {"Authorization": "Basic " + base64.b64encode(b"alice:pw-alice").decode()}
FEED = "/opds/comics/Plain"


@pytest.fixture
def pse(cfg, library):
    make_cbz(library / "comics" / "Plain" / "Comic One.cbz",
             [("p10.png", BLUE), ("p2.png", GREEN), ("p1.png", RED), ("notes.txt", b"x")])
    (library / "comics" / "Plain" / "Fake.cbz").write_bytes(b"not a zip at all")
    return TestClient(create_app(cfg)), cfg


def entries(client, url=FEED):
    r = client.get(url, headers=AUTH)
    assert r.status_code == 200, r.text
    return r, {e.find(ATOM + "title").text: e for e in ET.fromstring(r.content).findall(ATOM + "entry")}


def links(entry, rel):
    return [l for l in entry.findall(ATOM + "link") if l.get("rel") == rel]


def test_a_comic_gets_a_stream_link_as_the_spec_describes(pse):
    client, _ = pse
    r, found = entries(client)
    (stream,) = links(found["Comic One"], STREAM_REL)
    assert stream.get("type") == "image/png"                                   # one of jpeg/png/gif, from the first page
    assert stream.get(PSE + "count") == "3"                                    # 3 images; notes.txt is not a page
    assert stream.get("href") == "/cbz/comics/Plain/Comic%20One.cbz?page={pageNumber}"
    raw = r.text
    assert raw.count('xmlns:pse="http://vaemendis.net/opds-pse/ns"') == 1      # the namespace is declared (once)
    assert "?page={pageNumber}" in raw and "%7B" not in raw                    # the template braces stay literal
    assert PSE + "lastRead" not in stream.attrib                               # never read: no position claimed
    assert len(links(found["Comic One"], ACQ_REL)) == 1                       # the plain download link is still there
    assert "kind=acquisition" in r.headers["content-type"]


def test_a_client_can_walk_the_whole_template(pse):
    client, _ = pse
    _, found = entries(client)
    (stream,) = links(found["Comic One"], STREAM_REL)
    count, template = int(stream.get(PSE + "count")), stream.get("href")
    pages = [client.get(template.replace("{pageNumber}", str(n)), headers=AUTH) for n in range(count)]
    assert [p.content for p in pages] == [RED, GREEN, BLUE]                    # numbered from 0, natural order
    assert all(p.headers["content-type"] == "image/png" for p in pages)
    assert client.get(template.replace("{pageNumber}", str(count)), headers=AUTH).status_code == 404


def test_the_link_type_is_always_one_the_spec_allows(tmp_path):
    assert opds.stream_mime("image/png") == "image/png" and opds.stream_mime("image/gif") == "image/gif"
    assert opds.stream_mime("image/webp") == "image/jpeg" and opds.stream_mime("image/avif") == "image/jpeg"


def test_a_webp_comic_is_advertised_as_jpeg_but_served_as_what_it_is(cfg, library):
    make_cbz(library / "comics" / "Plain" / "Web.cbz", [("p1.webp", b"RIFFxxxxWEBP"), ("p2.webp", b"RIFFxxxxWEBP")])
    client = TestClient(create_app(cfg))
    _, found = entries(client)
    (stream,) = links(found["Web"], STREAM_REL)
    assert stream.get("type") == "image/jpeg" and stream.get(PSE + "count") == "2"
    assert client.get("/cbz/comics/Plain/Web.cbz?page=0", headers=AUTH).headers["content-type"] == "image/webp"


def test_broken_archives_and_other_books_get_no_stream_link(pse):
    client, _ = pse
    _, found = entries(client)
    assert links(found["Fake"], STREAM_REL) == []                              # not a zip: no stream link ...
    assert len(links(found["Fake"], ACQ_REL)) == 1                             # ... but still downloadable
    _, books = entries(client, "/opds/books/Good%20Author")
    assert all(links(e, STREAM_REL) == [] for e in books.values())              # EPUBs never stream


def test_last_read_follows_the_users_reading_position(pse):
    client, cfg = pse
    app_state = client.app.state.state
    app_state.put_progress("alice", "comics", "Plain/Comic One.cbz", "page:2", 1.0, now=1_700_000_000)
    _, found = entries(client)
    (stream,) = links(found["Comic One"], STREAM_REL)
    assert stream.get(PSE + "lastRead") == "3"                                 # our 0-based page 2 is the spec's page 3
    assert stream.get(PSE + "lastReadDate") == "2023-11-14T22:13:20Z"
    app_state.put_progress("alice", "comics", "Plain/Comic One.cbz", "page:99", 1.0, now=1_700_000_100)
    assert links(entries(client)[1]["Comic One"], STREAM_REL)[0].get(PSE + "lastRead") == "3"   # clamped to the page count
    app_state.put_progress("alice", "comics", "Plain/Comic One.cbz", "epubcfi(/6/4)", 0.2)
    assert PSE + "lastRead" not in links(entries(client)[1]["Comic One"], STREAM_REL)[0].attrib   # not a page position
    app_state.put_progress("bob", "comics", "Plain/Comic One.cbz", "page:1", 0.5)
    app_state.put_progress("alice", "comics", "Plain/Comic One.cbz", "garbage", 0.5)
    assert PSE + "lastRead" not in links(entries(client)[1]["Comic One"], STREAM_REL)[0].attrib   # another user's position never leaks


def test_it_can_be_switched_off_and_then_reads_nothing(cfg, library, monkeypatch):
    make_cbz(library / "comics" / "Plain" / "Comic One.cbz", [("p1.png", RED)])
    calls = []
    real = cbz.list_pages
    monkeypatch.setattr(cbz, "list_pages", lambda p: calls.append(p) or real(p))
    client = TestClient(create_app(dataclasses.replace(cfg, opds_pse=False)))
    _, found = entries(client)
    assert links(found["Comic One"], STREAM_REL) == [] and len(links(found["Comic One"], ACQ_REL)) == 1
    assert calls == []                                                          # no archive was even opened


def test_each_comic_is_read_once_and_then_served_from_the_cache(pse, monkeypatch):
    client, _ = pse
    calls = []
    real = cbz.list_pages
    monkeypatch.setattr(cbz, "list_pages", lambda p: calls.append(p) or real(p))
    for _ in range(3):
        entries(client)
    good = [c for c in calls if c.endswith("Comic One.cbz")]
    assert len(good) == 1                                                       # three feeds, one read of the real comic


def test_the_stream_url_needs_a_login_and_a_comic_you_may_see(pse):
    client, _ = pse
    assert client.get("/cbz/comics/Plain/Comic%20One.cbz?page=0").status_code == 401
    anon = client.get("/opds/comics/Plain")
    assert anon.status_code == 401
    assert client.get("/cbz/comics/Adult%20Pub/Naughty%20%282000%29/n.cbz?page=0", headers=AUTH).status_code == 404


def test_navigation_feeds_are_unchanged(pse):
    client, _ = pse
    r = client.get("/opds/comics", headers=AUTH)
    assert "kind=navigation" in r.headers["content-type"] and "pse:" not in r.text
    ET.fromstring(r.content)

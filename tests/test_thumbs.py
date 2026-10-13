"""Cover thumbnails for the OPDS feeds."""
import io

import pytest
from cbz_factory import make_cbz, png
from conftest import basic
from epub_factory import make_epub
from PIL import Image
from starlette.testclient import TestClient

from pluteus import thumbs
from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import parse_config

AUTH = basic("alice", "pw")
BIG = png((200, 30, 30), size=(800, 1200))


@pytest.fixture
def client(tmp_path):
    lib = tmp_path / "lib"
    make_epub(lib / "A" / "three.epub", title="Three", cover=BIG)
    make_epub(lib / "A" / "two.epub", title="Two", cover=BIG, cover_style="epub2")
    make_epub(lib / "A" / "none.epub", title="None")
    make_cbz(lib / "A" / "comic.cbz", [("p1.png", BIG), ("p2.png", png((0, 0, 255)))])
    cfg = parse_config({"server": {"data_dir": str(tmp_path / "data")}, "roots": [{"name": "lib", "path": str(lib)}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]}]})
    return TestClient(create_app(cfg)), tmp_path


def size_of(content):
    return Image.open(io.BytesIO(content)).size


def test_the_epub_cover_is_found_both_ways(client):
    c, _ = client
    for name in ("three", "two"):
        r = c.get("/thumb/lib/A/%s.epub?w=160" % name, headers=AUTH)
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        assert size_of(r.content) == (160, 240)                                  # scaled down, proportions kept


def test_a_comic_uses_its_first_page_and_widths_are_clamped(client):
    c, _ = client
    assert size_of(c.get("/thumb/lib/A/comic.cbz?w=100", headers=AUTH).content) == (100, 150)
    assert size_of(c.get("/thumb/lib/A/comic.cbz?w=1", headers=AUTH).content)[0] == thumbs.MIN_WIDTH
    assert size_of(c.get("/thumb/lib/A/comic.cbz?w=99999", headers=AUTH).content)[0] == 800        # never enlarged, never above the limit
    assert size_of(c.get("/thumb/lib/A/comic.cbz?w=abc", headers=AUTH).content)[0] == 200          # not a number: the default


def test_no_cover_is_a_404_and_the_login_applies(client):
    c, _ = client
    assert c.get("/thumb/lib/A/none.epub", headers=AUTH).status_code == 404
    assert c.get("/thumb/lib/A/three.epub").status_code == 401
    assert c.get("/thumb/lib/A/missing.epub", headers=AUTH).status_code == 404


def test_thumbnails_are_cached_on_disk_and_revalidated(client):
    c, tmp = client
    first = c.get("/thumb/lib/A/three.epub?w=160", headers=AUTH)
    assert list((tmp / "data" / "thumbs").glob("*.jpg"))
    again = c.get("/thumb/lib/A/three.epub?w=160", headers=AUTH)
    assert again.content == first.content
    assert c.get("/thumb/lib/A/three.epub?w=160", headers={**AUTH, "If-None-Match": first.headers["etag"]}).status_code == 304


def test_feeds_link_the_covers(client):
    c, _ = client
    atom = c.get("/opds/lib/A", headers=AUTH).text
    assert "http://opds-spec.org/image/thumbnail" in atom and "/thumb/lib/A/three.epub?w=160" in atom
    feed = c.get("/opds/lib/A", headers={**AUTH, "Accept": "application/opds+json"}).json()
    book = [p for p in feed["publications"] if p["metadata"]["title"] == "Three"][0]
    assert book["images"][0]["href"] == "/thumb/lib/A/three.epub?w=600" and book["images"][1]["width"] == 160


def test_without_pillow_the_original_picture_is_sent(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(thumbs, "scaled", lambda data, width: None)
    r = c.get("/thumb/lib/A/three.epub?w=160", headers=AUTH)
    assert r.status_code == 200 and r.content == BIG

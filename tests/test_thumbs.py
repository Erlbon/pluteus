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
    cfg = parse_config({"server": {"data_dir": str(tmp_path / "data"), "thumbnails": True}, "roots": [{"name": "lib", "path": str(lib)}],
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
    assert size_of(c.get("/thumb/lib/A/comic.cbz?w=100", headers=AUTH).content) == (160, 240)       # rounded up to a fixed width
    assert size_of(c.get("/thumb/lib/A/comic.cbz?w=1", headers=AUTH).content)[0] == thumbs.MIN_WIDTH
    assert size_of(c.get("/thumb/lib/A/comic.cbz?w=99999", headers=AUTH).content)[0] == 800        # never enlarged, never above the limit
    assert size_of(c.get("/thumb/lib/A/comic.cbz?w=abc", headers=AUTH).content)[0] == 240          # not a number: the default


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


# ---- the cover view of a folder -----------------------------------------------------------------------------------------------------------
HTML = {"Accept": "text/html"}


def test_the_cover_view_shows_a_picture_for_books_and_an_icon_for_the_rest(client):
    c, _ = client
    page = c.get("/browse/lib/A?view=grid", headers={**AUTH, **HTML}).text
    assert "pl-grid" in page and "src='/thumb/lib/A/three.epub?w=320'" in page and "grid.js" in page
    assert "pl-tile-fallback" in page and "Three" in page
    assert "view=grid" in c.get("/browse/lib/A?view=grid&sort=size", headers={**AUTH, **HTML}).text      # the choice stays on the sort links


def test_the_list_is_the_default_and_the_choice_can_be_saved_in_settings(client):
    c, _ = client
    assert "pl-grid" not in c.get("/browse/lib/A", headers={**AUTH, **HTML}).text
    r = c.post("/settings", data={"theme": "default", "icons": "gold", "view": "grid"}, headers={**AUTH, "Origin": "http://testserver"},
               follow_redirects=False)
    assert r.status_code == 303
    assert "pl-grid" in c.get("/browse/lib/A", headers={**AUTH, **HTML}).text
    assert "pl-grid" not in c.get("/browse/lib/A?view=list", headers={**AUTH, **HTML}).text           # one request can ask for the other


# ---- thumbnails are off until switched on ---------------------------------------------------------------------------------------------------
@pytest.fixture
def quiet(tmp_path):
    lib = tmp_path / "lib"
    make_epub(lib / "A" / "three.epub", title="Three", cover=BIG)
    cfg = parse_config({"roots": [{"name": "lib", "path": str(lib)}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]}]})
    return TestClient(create_app(cfg))


def test_by_default_there_are_no_thumbnails_anywhere(quiet):
    assert quiet.get("/thumb/lib/A/three.epub", headers=AUTH).status_code == 404
    assert "thumb" not in quiet.get("/opds/lib/A", headers=AUTH).text
    feed = quiet.get("/opds/lib/A", headers={**AUTH, "Accept": "application/opds+json"}).json()
    assert "images" not in feed["publications"][0]


def test_by_default_the_cover_view_is_not_offered(quiet):
    page = quiet.get("/browse/lib/A?view=grid", headers={**AUTH, **HTML}).text
    assert "pl-grid" not in page and "Covers" not in page                               # not even when the address asks for it
    assert "Folders are shown as" not in quiet.get("/settings", headers={**AUTH, **HTML}).text


# ---- a small file can hold a huge picture ---------------------------------------------------------------------------------------------------
def test_a_picture_with_too_many_pixels_is_refused_before_it_is_decoded():
    flat = png((10, 10, 10), size=(6000, 6000))                                   # about 120 KB of PNG, 36 million pixels
    assert len(flat) < 200_000
    assert thumbs.scaled(flat, 160) is False
    assert thumbs.scaled(b"not a picture", 160) is False


def test_only_a_few_widths_exist_so_the_cache_cannot_blow_up(client):
    c, tmp = client
    for w in range(48, 120, 7):
        c.get("/thumb/lib/A/three.epub?w=%d" % w, headers=AUTH)
    assert len(list((tmp / "data" / "thumbs").glob("*.jpg"))) <= 2
    assert [thumbs.clamp_width(str(n)) for n in (1, 96, 97, 500, 5000)] == [96, 96, 160, 640, 900]


def test_a_refused_picture_gives_no_cover_not_the_original(tmp_path):
    lib = tmp_path / "lib"
    make_epub(lib / "A" / "bomb.epub", title="Bomb", cover=png((9, 9, 9), size=(6000, 6000)))
    cfg = parse_config({"server": {"thumbnails": True}, "roots": [{"name": "lib", "path": str(lib)}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]}]})
    assert TestClient(create_app(cfg)).get("/thumb/lib/A/bomb.epub", headers=AUTH).status_code == 404

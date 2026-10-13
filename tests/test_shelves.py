"""Favourites and "Read next": the buttons in a listing, the boxes on the home page, order, hiding, rights, and auto-removal."""
import re

import pytest
from conftest import basic
from starlette.testclient import TestClient

from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import parse_config

HTML = {"Accept": "text/html"}
AUTH = basic("alice", "pw")
ORIGIN = {**AUTH, "Origin": "http://testserver"}


@pytest.fixture
def client(tmp_path):
    lib = tmp_path / "lib"
    (lib / "Series One").mkdir(parents=True)
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (lib / "Series One" / name).write_bytes(b"%PDF-1.4 x")
    (lib / "Private").mkdir()
    (lib / "Private" / ".private").write_text("someone\n")
    (lib / "Private" / "x.pdf").write_bytes(b"%PDF")
    cfg = parse_config({"roots": [{"name": "lib", "path": str(lib)}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]},
                                  {"name": "bob", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]}]})
    return TestClient(create_app(cfg))


def add(c, shelf, path, headers=ORIGIN):
    return c.post("/api/shelf/%s/lib/%s" % (shelf, path), headers=headers)


def home(c, headers=AUTH):
    return c.get("/", headers={**headers, **HTML}).text


def test_a_file_and_a_folder_can_go_on_the_shelves_and_show_on_the_home_page(client):
    assert add(client, "favourite", "Series%20One/a.pdf").status_code == 201
    assert add(client, "favourite", "Series%20One").status_code == 201                 # a folder
    assert add(client, "next", "Series%20One/b.pdf").status_code == 201
    page = home(client)
    assert "Favourites" in page and "Read next" in page
    assert "href='/browse/lib/Series%20One'" in page and "href='/view/lib/Series%20One/a.pdf'" in page
    assert page.index("Read next") < page.index("Browse")


def test_the_boxes_are_absent_when_the_shelves_are_empty(client):
    page = home(client)
    assert "Favourites" not in page and "Read next" not in page


def test_the_listing_marks_what_is_on_a_shelf(client):
    add(client, "favourite", "Series%20One/a.pdf")
    add(client, "next", "Series%20One/a.pdf")
    page = client.get("/browse/lib/Series%20One", headers={**AUTH, **HTML}).text
    row = page[page.index("a.pdf"):page.index("b.pdf")]
    assert row.count("aria-pressed='true'") == 2 and "shelf.js" in page
    assert page[page.index("b.pdf"):].count("aria-pressed='true'") == 0
    grid = client.get("/browse/lib/Series%20One?view=grid", headers={**AUTH, **HTML}).text
    assert "pl-shelf" in grid


def test_taking_an_entry_off_and_adding_twice(client):
    add(client, "favourite", "Series%20One/a.pdf")
    assert add(client, "favourite", "Series%20One/a.pdf").status_code == 201            # again: nothing changes
    assert "Favourites" in home(client)
    assert client.delete("/api/shelf/favourite/lib/Series%20One/a.pdf", headers=ORIGIN).status_code == 200
    assert "Favourites" not in home(client)


def read_next_order(client):
    box = home(client).split("aria-label='Read next'")[1].split("</section>")[0]
    return re.findall(r"href='/view/lib/Series%20One/(\w)\.pdf'", box)


def test_the_read_next_order_can_be_changed(client):
    for name in ("a", "b", "c"):
        add(client, "next", "Series%20One/" + name + ".pdf")
    assert read_next_order(client) == ["a", "b", "c"]
    assert client.post("/api/shelf/next/lib/Series%20One/c.pdf?move=up", headers=ORIGIN).json() == {"ok": True}
    assert read_next_order(client) == ["a", "c", "b"]
    assert client.post("/api/shelf/next/lib/Series%20One/a.pdf?move=down", headers=ORIGIN).json() == {"ok": True}
    assert read_next_order(client) == ["c", "a", "b"]
    assert client.post("/api/shelf/next/lib/Series%20One/c.pdf?move=up", headers=ORIGIN).json() == {"ok": False}    # already first
    assert client.post("/api/shelf/favourite/lib/Series%20One/a.pdf?move=up", headers=ORIGIN).status_code == 400   # only "next" has an order


def test_bad_requests_and_hidden_things_are_refused(client):
    assert add(client, "bogus", "Series%20One/a.pdf").status_code == 404
    assert add(client, "favourite", "Series%20One/missing.pdf").status_code == 404
    assert add(client, "favourite", "Private/x.pdf").status_code == 404                   # a .private folder alice is not listed in
    assert add(client, "favourite", "Series%20One/a.pdf", headers=AUTH).status_code == 403   # no Origin: cross-site refused
    assert client.post("/api/shelf/favourite/lib/Series%20One/a.pdf").status_code == 401


def test_each_user_has_their_own_shelves(client):
    add(client, "favourite", "Series%20One/a.pdf")
    assert "Favourites" not in home(client, basic("bob", "pw"))


def test_an_entry_that_disappears_is_skipped_not_shown(client, tmp_path):
    add(client, "favourite", "Series%20One/b.pdf")
    (tmp_path / "lib" / "Series One" / "b.pdf").unlink()
    assert "Favourites" not in home(client)


def test_finishing_a_book_takes_it_off_read_next(client):
    add(client, "next", "Series%20One/a.pdf")
    add(client, "next", "Series%20One/b.pdf")
    r = client.put("/api/progress/lib/Series%20One/a.pdf", json={"locator": "page:9", "percent": 0.99}, headers=ORIGIN)
    assert r.status_code == 200
    page = home(client)
    assert "Series%20One/b.pdf" in page and "href='/view/lib/Series%20One/a.pdf'" not in page.split("Read next")[1].split("Browse")[0]
    client.put("/api/progress/lib/Series%20One/b.pdf", json={"locator": "page:1", "percent": 0.4}, headers=ORIGIN)
    assert "Series%20One/b.pdf" in home(client)                                        # not finished: stays


def test_an_app_key_login_cannot_change_the_shelves(client):
    client.app.state.state.set_appkey("alice", "the-key")
    r = client.post("/api/shelf/favourite/lib/Series%20One/a.pdf", headers={**basic("alice", "the-key"), "Origin": "http://testserver"})
    assert r.status_code == 403

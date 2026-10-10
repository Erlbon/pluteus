"""End-to-end HTTP tests against a fake library (see conftest.py)."""
import xml.etree.ElementTree as ET
from urllib.parse import quote

ATOM = "{http://www.w3.org/2005/Atom}"
MARVEL = quote("3 - Marvel (1961-2012)", safe="")
SPIDEY = quote("Spider-Man (1963-)", safe="")


def feed(resp):
    assert resp.status_code == 200, resp.text
    return ET.fromstring(resp.content)          # also proves the XML is well formed


def titles(root):
    return [e.find(ATOM + "title").text for e in root.findall(ATOM + "entry")]


def links(root, rel):
    return [l.get("href") for l in root.findall(ATOM + "link") if l.get("rel") == rel]


# ---- authentication ------------------------------------------------------
def test_healthz_is_open(client):
    assert client.get("/healthz").text == "ok"


def test_everything_else_needs_a_login(client):
    for url in ("/", "/opds", "/opds/comics", "/browse/comics", "/dl/comics/x.cbz"):
        r = client.get(url)
        assert r.status_code == 401, url
        assert r.headers["www-authenticate"].startswith("Basic")


def test_wrong_password_is_refused(client):
    from conftest import basic
    assert client.get("/opds", headers=basic("alice", "nope")).status_code == 401
    assert client.get("/opds", headers=basic("ghost", "pw-alice")).status_code == 401


def test_anonymous_user_is_opt_in_and_never_overrides_bad_credentials(cfg):
    import dataclasses

    from starlette.testclient import TestClient

    from pluteus.app import create_app
    from pluteus.config import ConfigError, parse_config
    from conftest import basic

    anon = TestClient(create_app(dataclasses.replace(cfg, anonymous_user="carol")))
    assert titles(feed(anon.get("/opds"))) == ["comics"]                       # carol's roots only
    assert anon.get("/opds/books").status_code == 404
    assert anon.get("/opds", headers=basic("alice", "wrong")).status_code == 401
    assert titles(feed(anon.get("/opds", headers=basic("alice", "pw-alice")))) == ["comics", "books"]
    off = TestClient(create_app(cfg))
    assert off.get("/opds").status_code == 401                                 # default stays closed
    import pytest
    with pytest.raises(ConfigError):
        parse_config({"server": {"anonymous_user": "ghost"}, "roots": [{"name": "r", "path": "."}],
                      "users": [{"name": "u", "password_hash": "scrypt$1$2$3$4$5"}]})


# ---- roots and per-user access -------------------------------------------
def test_opds_root_lists_only_the_users_roots(client, alice, carol):
    assert titles(feed(client.get("/opds", headers=alice))) == ["comics", "books"]
    assert titles(feed(client.get("/opds", headers=carol))) == ["comics"]
    assert client.get("/opds/books", headers=carol).status_code == 404
    assert client.get("/browse/books", headers=carol).status_code == 404


def test_home_page_lists_roots(client, alice, carol):
    assert "books" in client.get("/", headers=alice).text
    assert "books" not in client.get("/", headers=carol).text


# ---- folder feeds: order, display names, paging --------------------------
def test_folder_feed_is_natural_order_with_display_names_and_paging(client, alice):
    p1 = feed(client.get("/opds/comics", headers=alice))
    assert titles(p1) == ["National Allied (1934-1946)", "Marvel (1961-2012)"]
    assert links(p1, "next") == ["/opds/comics?page=2"]
    assert links(p1, "previous") == []
    assert links(p1, "up") == ["/opds"]
    p2 = feed(client.get("/opds/comics?page=2", headers=alice))
    assert titles(p2) == ["Marvel Early (1980-1981)", "Marvel Epic (1982-1996)"]     # 3.9 before 3.10
    assert links(p2, "previous") == ["/opds/comics"]
    p3 = feed(client.get("/opds/comics?page=3", headers=alice))
    assert titles(p3) == ["Plain"]
    assert links(p3, "next") == []


def test_page_numbers_are_clamped(client, alice):
    assert titles(feed(client.get("/opds/comics?page=99", headers=alice))) == ["Plain"]
    assert titles(feed(client.get("/opds/comics?page=abc", headers=alice)))[0].startswith("National")


def test_navigation_entries_link_to_subfolders(client, alice):
    p1 = feed(client.get("/opds/comics", headers=alice))
    hrefs = [e.find(ATOM + "link").get("href") for e in p1.findall(ATOM + "entry")]
    assert hrefs[1] == "/opds/comics/" + MARVEL
    assert client.get(hrefs[1], headers=alice).status_code == 200


def test_book_feed_is_acquisition_with_download_links(client, alice):
    url = "/opds/comics/%s/%s" % (MARVEL, SPIDEY)
    r = client.get(url, headers=alice)
    assert "kind=acquisition" in r.headers["content-type"]
    root = feed(r)
    assert titles(root) == ["Amazing 001", "Amazing 002"]
    link = root.find(ATOM + "entry").find(ATOM + "link")
    assert link.get("rel") == "http://opds-spec.org/acquisition"
    assert link.get("type") == "application/vnd.comicbook+zip"
    assert link.get("href") == "/dl/comics/%s/%s/Amazing%%20001.cbz" % (MARVEL, SPIDEY)
    assert titles(feed(client.get(url + "?page=2", headers=alice))) == ["Amazing 003", "Amazing 010"]


def test_non_book_files_and_excluded_folders_are_not_listed(client, alice):
    assert titles(feed(client.get("/opds/comics/Plain", headers=alice))) == []
    all_titles = []
    for page in (1, 2, 3):
        all_titles += titles(feed(client.get("/opds/comics?page=%d" % page, headers=alice)))
    assert "_incoming" not in all_titles
    assert client.get("/opds/comics/_incoming", headers=alice).status_code == 404
    assert client.get("/dl/comics/Plain/notes.txt", headers=alice).status_code == 404


# ---- adult marker ----------------------------------------------------------
def test_adult_folders_are_hidden_from_users_without_the_flag(client, alice, bob):
    names_alice, names_bob = [], []
    for page in (1, 2, 3, 4):
        names_alice += titles(feed(client.get("/opds/comics?page=%d" % page, headers=alice)))
        names_bob += titles(feed(client.get("/opds/comics?page=%d" % page, headers=bob)))
    assert "Adult Pub" not in names_alice
    assert "Adult Pub" in names_bob
    deep = "Adult%20Pub/Naughty%20%282000%29"
    assert client.get("/opds/comics/Adult%20Pub", headers=alice).status_code == 404
    assert client.get("/browse/comics/" + deep, headers=alice).status_code == 404
    assert client.get("/dl/comics/%s/n.cbz" % deep, headers=alice).status_code == 404
    assert client.get("/dl/comics/%s/n.cbz" % deep, headers=bob).status_code == 200


def test_marker_file_itself_is_never_served(client, bob):
    assert client.get("/dl/comics/Adult%20Pub/.adult", headers=bob).status_code == 404


# ---- path traversal --------------------------------------------------------
def test_path_traversal_is_refused(client, alice):
    for url in ("/dl/comics/%2e%2e/secret.txt", "/dl/comics/..%5Csecret.txt",
                "/dl/comics/Plain/%2e%2e/%2e%2e/secret.txt", "/browse/comics/%2e%2e",
                "/opds/comics/%2e%2e", "/dl/comics/C%3A/secret.txt", "/dl/comics//x.cbz"):
        r = client.get(url, headers=alice)
        assert r.status_code in (400, 404), url
        assert b"outside the library" not in r.content, url


# ---- downloads ---------------------------------------------------------------
BOOK = "/dl/books/%s/%s" % (quote("Lescroart, John T.", safe=""), quote("Il Tredicesimo Giurato.epub", safe=""))


def test_download_from_a_folder_whose_name_ends_in_a_dot(client, alice):
    r = client.get(BOOK, headers=alice)
    assert r.status_code == 200
    assert len(r.content) == 800
    assert r.headers["content-type"] == "application/epub+zip"
    assert "attachment" in r.headers["content-disposition"]
    listing = client.get("/browse/books/" + quote("Lescroart, John T.", safe=""), headers=alice)
    assert listing.status_code == 200 and "Il Tredicesimo Giurato.epub" in listing.text


def test_download_supports_range_requests(client, alice):
    h = dict(alice, Range="bytes=0-9")
    r = client.get(BOOK, headers=h)
    assert r.status_code == 206
    assert r.content == b"EPUBDATAEP"
    assert r.headers["content-range"] == "bytes 0-9/800"


def test_head_request_works(client, alice):
    r = client.head(BOOK, headers=alice)
    assert r.status_code == 200 and r.headers["content-length"] == "800"


def test_browse_page_paginates(client, alice):
    html = client.get("/browse/comics", headers=alice).text
    assert "Page 1 of 3" in html and "page=2" in html
    assert "National Allied" in html and "Marvel Epic" not in html

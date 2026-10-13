"""Read-only WebDAV: PROPFIND listings, file downloads, the rights of the site, and that nothing can be written."""
import xml.etree.ElementTree as ET

import pytest
from conftest import basic
from starlette.testclient import TestClient

from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import parse_config

D = "{DAV:}"
AUTH = basic("alice", "pw")


@pytest.fixture
def client(tmp_path):
    books = tmp_path / "books"
    (books / "Good Author").mkdir(parents=True)
    (books / "Good Author" / "Real Book.epub").write_bytes(b"EPUBDATA" * 50)
    (books / "Good Author" / "notes.txt").write_text("hello", encoding="utf-8")
    (books / "Private").mkdir()
    (books / "Private" / ".private").write_text("someoneelse\n")
    (books / "Private" / "secret.txt").write_text("no")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "x.txt").write_text("x")
    cfg = parse_config({"server": {"webdav": True, "base_path": ""},
                        "roots": [{"name": "books", "path": str(books)}, {"name": "other", "path": str(tmp_path / "other")}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["books"]}]})
    return TestClient(create_app(cfg))


def hrefs(response):
    return [r.find(D + "href").text for r in ET.fromstring(response.content).findall(D + "response")]


def test_it_is_off_by_default(tmp_path):
    cfg = parse_config({"roots": [{"name": "b", "path": str(tmp_path)}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]}]})
    assert TestClient(create_app(cfg)).request("PROPFIND", "/dav/", headers=AUTH).status_code == 404


def test_options_describes_a_read_only_dav_share(client):
    r = client.options("/dav/", headers=AUTH)
    assert r.status_code == 200 and r.headers["dav"] == "1" and "PUT" not in r.headers["allow"] and r.headers["ms-author-via"] == "DAV"


def test_a_login_is_required(client):
    r = client.request("PROPFIND", "/dav/")
    assert r.status_code == 401 and "Basic" in r.headers["www-authenticate"]


def test_the_top_lists_only_the_libraries_this_user_may_open(client):
    r = client.request("PROPFIND", "/dav/", headers={**AUTH, "Depth": "1"})
    assert r.status_code == 207 and hrefs(r) == ["/dav/", "/dav/books/"]


def test_a_folder_lists_its_entries_with_their_properties(client):
    r = client.request("PROPFIND", "/dav/books/Good%20Author", headers={**AUTH, "Depth": "1"})
    assert r.status_code == 207
    assert hrefs(r) == ["/dav/books/Good%20Author/", "/dav/books/Good%20Author/Real%20Book.epub", "/dav/books/Good%20Author/notes.txt"] or \
        set(hrefs(r)) == {"/dav/books/Good%20Author/", "/dav/books/Good%20Author/Real%20Book.epub", "/dav/books/Good%20Author/notes.txt"}
    root = ET.fromstring(r.content)
    epub = [x for x in root.findall(D + "response") if x.find(D + "href").text.endswith("Real%20Book.epub")][0]
    assert epub.find(".//" + D + "getcontentlength").text == "400" and epub.find(".//" + D + "resourcetype/" + D + "collection") is None
    assert "epub" in epub.find(".//" + D + "getcontenttype").text
    top = root.findall(D + "response")[0]
    assert top.find(".//" + D + "resourcetype/" + D + "collection") is not None


def test_depth_zero_describes_only_the_item(client):
    r = client.request("PROPFIND", "/dav/books/Good%20Author", headers={**AUTH, "Depth": "0"})
    assert hrefs(r) == ["/dav/books/Good%20Author/"]
    f = client.request("PROPFIND", "/dav/books/Good%20Author/notes.txt", headers={**AUTH, "Depth": "0"})
    assert hrefs(f) == ["/dav/books/Good%20Author/notes.txt"]


def test_files_are_downloads_with_range_support(client):
    r = client.get("/dav/books/Good%20Author/notes.txt", headers=AUTH)
    assert r.status_code == 200 and r.content == b"hello" and "attachment" in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"
    part = client.get("/dav/books/Good%20Author/Real%20Book.epub", headers={**AUTH, "Range": "bytes=0-7"})
    assert part.status_code == 206 and part.content == b"EPUBDATA"
    assert client.head("/dav/books/Good%20Author/notes.txt", headers=AUTH).status_code == 200


def test_the_rights_of_the_site_apply(client):
    assert client.request("PROPFIND", "/dav/other/", headers=AUTH).status_code == 404                  # a library alice may not open
    assert client.get("/dav/other/x.txt", headers=AUTH).status_code == 404
    folders = hrefs(client.request("PROPFIND", "/dav/books/", headers={**AUTH, "Depth": "1"}))
    assert "/dav/books/Private/" not in folders and "/dav/books/Good%20Author/" in folders         # .private is honoured
    assert client.request("PROPFIND", "/dav/books/Private/", headers=AUTH).status_code == 404
    assert client.get("/dav/books/Private/secret.txt", headers=AUTH).status_code == 404


def test_paths_cannot_leave_the_library(client):
    assert client.get("/dav/books/..%2F..%2Fsecret.txt", headers=AUTH).status_code in (400, 404)
    assert client.request("PROPFIND", "/dav/books/%2e%2e/", headers=AUTH).status_code in (400, 404)


@pytest.mark.parametrize("method", ["PUT", "DELETE", "MKCOL", "COPY", "MOVE", "PROPPATCH", "LOCK", "POST"])
def test_nothing_can_be_changed(client, method):
    r = client.request(method, "/dav/books/Good%20Author/new.txt", headers={**AUTH, "Origin": "http://testserver"}, content=b"data")
    assert r.status_code in (403, 405)
    assert client.get("/dav/books/Good%20Author/new.txt", headers=AUTH).status_code == 404

"""Regression tests for the 2026-10-11 review findings (each one was reproduced before it was fixed)."""
import os
import re
import sys
import zipfile

import pytest
from conftest import basic
from starlette.testclient import TestClient
from test_login_web import HTML, make

from pluteus import cbz
from pluteus.config import ConfigError, parse_config
from pluteus.fs import clamp_time, natural_key

windows = pytest.mark.skipif(sys.platform != "win32", reason="8.3 short names exist on Windows only")


def short_name(path: str) -> str:
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024)
    return buf.value


@windows
def test_excluded_folders_cannot_be_reached_through_their_8_3_short_name(cfg, tmp_path, library):
    short = short_name(str(library / "comics" / "_incoming"))
    if not short or os.path.basename(short).lower() == "_incoming":
        pytest.skip("this volume has 8.3 names switched off")
    client, _ = make(cfg, tmp_path)
    r = client.get("/dl/comics/%s/hidden.cbz" % os.path.basename(short), headers=basic("bob", "pw-bob"))
    assert r.status_code == 404
    assert client.get("/dl/comics/_incoming/hidden.cbz", headers=basic("bob", "pw-bob")).status_code == 404


def test_an_absurd_modification_time_does_not_break_the_folder(cfg, tmp_path, library):
    book = library / "comics" / "Plain" / "notes.txt"
    os.utime(book, (-5e9, -5e9))
    big = library / "comics" / "Plain" / "far.txt"
    big.write_bytes(b"x")
    os.utime(big, (1e11, 1e11))
    client, _ = make(cfg, tmp_path)
    for url in ("/opds/comics/Plain", "/browse/comics/Plain", "/view/comics/Plain/notes.txt?as=text"):
        r = client.get(url, headers=dict(basic("alice", "pw-alice"), **HTML))
        assert r.status_code == 200, url
    assert clamp_time(-1) == 0.0 and clamp_time(1e12) == 0.0 and clamp_time(5.0) == 5.0


def test_natural_key_survives_enormous_digit_runs():
    natural_key("page " + "9" * 5000 + ".jpg")
    assert natural_key("a2") < natural_key("a10")


def test_a_comic_with_hostile_page_names_is_refused_cleanly(tmp_path):
    path = tmp_path / "evil.cbz"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("1" * 5000 + ".jpg", b"x")
        zf.writestr("2.jpg", b"x")
    try:
        pages = cbz.list_pages(str(path))
    except cbz.CbzError:
        return
    assert len(pages) == 2                                  # sorting simply works: either outcome is acceptable


def test_a_hostile_cbz_does_not_500_the_folder_feed(cfg, tmp_path, library):
    path = library / "comics" / "Plain" / "evil.cbz"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("1" * 5000 + ".jpg", b"x")
    client, _ = make(cfg, tmp_path)
    assert client.get("/opds/comics/Plain", headers=basic("alice", "pw-alice")).status_code == 200
    assert client.get("/read/comics/Plain/evil.cbz", headers=dict(basic("alice", "pw-alice"), **HTML)).status_code in (200, 422)


@pytest.mark.parametrize("key,value", [("page_size", 0), ("page_size", -3), ("port", 0), ("port", 70000), ("page_size", "x")])
def test_silly_numbers_in_the_config_are_refused(library, key, value):
    data = {"server": {key: value}, "roots": [{"name": "r", "path": str(library / "comics")}],
            "users": [{"name": "u", "password_hash": "scrypt$1$2$3$4$5"}]}
    with pytest.raises(ConfigError, match=key):
        parse_config(data)


def test_continue_reading_drops_books_the_user_may_no_longer_see(cfg, tmp_path, library):
    client, app = make(cfg, tmp_path)
    me = TestClient(app, follow_redirects=False)
    assert me.post("/login", content="username=bob&password=pw-bob", headers={
        "content-type": "application/x-www-form-urlencoded", "origin": "http://testserver"}).status_code == 303
    path = "Adult%20Pub/Naughty%20%282000%29/n.cbz"
    put = me.put("/api/progress/comics/" + path, json={"locator": "page:3", "percent": 0.2},
                 headers={"origin": "http://testserver"})
    assert put.status_code == 200
    assert "Adult%20Pub" in me.get("/", headers=HTML).text                      # listed while bob may see it
    # bob loses adult access: the link must disappear from his home page
    from pluteus.config import User
    users = dict(app.state.auth.users)
    users["bob"] = User("bob", users["bob"].password_hash, adult=False, roots=users["bob"].roots)
    app.state.auth.set_users(users)
    assert "Adult%20Pub" not in me.get("/", headers=HTML).text


def test_the_page_endpoint_challenges_apps_but_not_browser_sessions(cfg, tmp_path):
    client, app = make(cfg, tmp_path)
    r = client.get("/cbz/comics/Plain/x.cbz?page=0")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    r = client.get("/api/progress/comics/Plain/x.cbz")
    assert r.status_code == 401 and "www-authenticate" not in r.headers


def test_an_unreachable_root_answers_503_not_404(cfg, tmp_path, library):
    import shutil
    client, _ = make(cfg, tmp_path)
    shutil.move(str(library / "comics"), str(library / "comics-away"))                  # the share went offline
    r = client.get("/opds/comics", headers=basic("alice", "pw-alice"))
    assert r.status_code == 503 and "Retry-After" in r.headers
    assert client.get("/opds/comics/NoSuchFolder", headers=basic("alice", "pw-alice")).status_code == 503
    assert client.get("/opds/books", headers=basic("alice", "pw-alice")).status_code == 200   # other roots unaffected
    shutil.move(str(library / "comics-away"), str(library / "comics"))
    assert client.get("/opds/comics/NoSuchFolder", headers=basic("alice", "pw-alice")).status_code == 404


# ---- sortable folder listings (a per-folder reordering of the listing already read; no index) --------------------------
def _sorted_names(client, url, headers):
    import re
    text = client.get(url, headers=headers).text
    return re.findall(r"<li class='pl-row pl-file'>.*?<a class='pl-title' href='[^']*'>([^<]+)</a>", text)


def test_listing_can_be_sorted_by_name_type_size_and_date(cfg, tmp_path, library):
    folder = library / "comics" / "Plain"
    for name, size, when in (("b.txt", 10, 3e8), ("a.zip", 30, 5e8), ("c.csv", 20, 4e8)):
        (folder / name).write_bytes(b"x" * size)
        os.utime(folder / name, (when, when))
    os.utime(folder / "notes.txt", (1e8, 1e8))
    client, _ = make(cfg, tmp_path, page_size=50)
    h = dict(basic("alice", "pw-alice"), **HTML)
    base = "/browse/comics/Plain"
    assert _sorted_names(client, base, h) == ["a.zip", "b.txt", "c.csv", "notes.txt"]
    assert _sorted_names(client, base + "?sort=name&dir=desc", h) == ["notes.txt", "c.csv", "b.txt", "a.zip"]
    assert _sorted_names(client, base + "?sort=type", h) == ["c.csv", "b.txt", "notes.txt", "a.zip"]
    assert _sorted_names(client, base + "?sort=size", h)[-1] == "a.zip"
    assert _sorted_names(client, base + "?sort=date&dir=desc", h) == ["a.zip", "c.csv", "b.txt", "notes.txt"]
    assert _sorted_names(client, base + "?sort=nonsense", h) == _sorted_names(client, base, h)     # unknown: default


def test_sort_choice_survives_paging_and_links_flip_direction(cfg, tmp_path, library):
    folder = library / "comics" / "Plain"
    for i in range(5):
        (folder / ("f%d.txt" % i)).write_bytes(b"x" * i)
    client, _ = make(cfg, tmp_path, page_size=2)
    h = dict(basic("alice", "pw-alice"), **HTML)
    text = client.get("/browse/comics/Plain?sort=size&dir=desc", headers=h).text
    assert "/browse/comics/Plain?sort=size&amp;dir=desc&amp;page=2" in text           # the pager keeps the sort
    assert "/browse/comics/Plain?sort=size'" in text                                   # the current column flips to ascending
    assert "/browse/comics/Plain?sort=date&amp;dir=desc'" in text                      # a new date column starts newest first


# ---- progress case, base path, proxy host ------------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="letter case is ignored on Windows only")
def test_progress_is_one_record_whatever_the_letter_case_of_the_url(cfg, tmp_path, library):
    client, app = make(cfg, tmp_path)
    me = TestClient(app, follow_redirects=False)
    me.post("/login", content="username=alice&password=pw-alice",
            headers={"content-type": "application/x-www-form-urlencoded", "origin": "http://testserver"})
    h = {"origin": "http://testserver"}
    assert me.put("/api/progress/books/Good%20Author/Real%20Book.epub", json={"locator": "a", "percent": 0.3}, headers=h).status_code == 200
    assert me.put("/api/progress/books/good%20author/REAL%20BOOK.EPUB", json={"locator": "b", "percent": 0.4}, headers=h).status_code == 200
    rows = app.state.state.recent_progress("alice", 10)
    assert len(rows) == 1 and rows[0]["path"] == "Good Author/Real Book.epub" and rows[0]["locator"] == "b"


def test_base_path_works_with_or_without_the_proxy_stripping_it(cfg, tmp_path):
    client, _ = make(cfg, tmp_path, base_path="/lib")
    h = dict(basic("alice", "pw-alice"))
    assert client.get("/opds/books", headers=h).status_code == 200                 # proxy stripped the prefix
    assert client.get("/lib/opds/books", headers=h).status_code == 200             # proxy passed it through
    assert "/lib/opds/books" in client.get("/lib/opds", headers=h).text            # links carry the prefix
    r = client.get("/browse/books", headers=HTML)                                  # not signed in: back to the login...
    assert r.status_code == 303 and r.headers["location"] == "/lib/login?next=%2Flib%2Fbrowse%2Fbooks"
    form = {"content-type": "application/x-www-form-urlencoded", "origin": "http://testserver"}
    r = client.post("/login", content="username=alice&password=pw-alice&next=%2Flib%2Fbrowse%2Fbooks", headers=form)
    assert r.status_code == 303 and r.headers["location"] == "/lib/browse/books"      # ...and on to the page asked for


def test_a_proxy_that_rewrites_host_can_be_told_the_public_name(cfg, tmp_path):
    form = {"content-type": "application/x-www-form-urlencoded", "origin": "https://books.example.org"}
    body = "username=alice&password=pw-alice"
    client, _ = make(cfg, tmp_path)
    assert client.post("/login", content=body, headers=form).status_code == 403          # Host is "testserver"
    client, _ = make(cfg, tmp_path, allowed_hosts=("books.example.org",))
    assert client.post("/login", content=body, headers=form).status_code == 303
    client, _ = make(cfg, tmp_path, trust_proxy=True)
    assert client.post("/login", content=body, headers=dict(form, **{"x-forwarded-host": "books.example.org"})).status_code == 303
    client, _ = make(cfg, tmp_path)                                                      # without trust the header is ignored
    assert client.post("/login", content=body, headers=dict(form, **{"x-forwarded-host": "books.example.org"})).status_code == 403


def test_a_folder_with_many_subfolders_hides_the_marked_ones_for_everyone_but_adults(cfg, tmp_path, library):
    big = library / "comics" / "Big"
    for i in range(40):
        (big / ("sub%02d" % i)).mkdir(parents=True)
    for i in (3, 17, 39):
        (big / ("sub%02d" % i) / ".adult").write_bytes(b"")
    client, _ = make(cfg, tmp_path, page_size=100)
    import re
    count = lambda who: len(re.findall(r"&#128193; sub\d\d", client.get(
        "/browse/comics/Big", headers=dict(basic(*who), **HTML)).text))
    assert count(("alice", "pw-alice")) == 37 and count(("bob", "pw-bob")) == 40


def test_behind_a_proxy_that_hides_clients_the_address_limit_does_not_lock_everyone_out(cfg, tmp_path):
    client, _ = make(cfg, tmp_path, trust_proxy=True)
    nat = {"x-forwarded-for": "192.168.16.1"}                    # what Docker Desktop shows for every visitor
    for i in range(40):                                          # 40 guesses at 40 DIFFERENT names from "one address"
        assert client.get("/opds", headers={**basic("nobody%d" % i, "x"), **nat}).status_code == 401
    assert client.get("/opds", headers={**basic("alice", "pw-alice"), **nat}).status_code == 200   # real users still get in


def test_a_real_public_address_behind_the_proxy_is_still_limited_per_address(cfg, tmp_path):
    client, _ = make(cfg, tmp_path, trust_proxy=True)
    real = {"x-forwarded-for": "8.8.8.8"}
    for i in range(30):
        client.get("/opds", headers={**basic("nobody%d" % i, "x"), **real})
    assert client.get("/opds", headers={**basic("alice", "pw-alice"), **real}).status_code == 429
    assert client.get("/opds", headers={**basic("alice", "pw-alice"), "x-forwarded-for": "8.8.4.4"}).status_code == 200


def test_a_config_file_with_a_byte_order_mark_is_accepted(tmp_path, library):
    """PowerShell 5.1 and some Windows editors write a UTF-8 BOM; the file is still valid."""
    from pluteus import useredit
    from pluteus.config import load_config
    path = tmp_path / "config.toml"
    text = ('[server]\nhost = "127.0.0.1"\n[[roots]]\nname = "r"\npath = \'%s\'\n[[users]]\nname = "u"\n'
            'password_hash = "scrypt$1$2$3$4$5"\n' % str(library / "comics"))
    path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    assert list(load_config(str(path)).users) == ["u"]
    useredit.add_user(str(path), "v", "scrypt$1$2$3$4$5")                      # the edit tools read it too ...
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")                    # ... and save it without the BOM
    assert list(load_config(str(path)).users) == ["u", "v"]


def test_a_file_row_opens_the_reader_by_default_and_has_a_separate_download_button(cfg, tmp_path, library):
    client, _ = make(cfg, tmp_path)
    h = dict(basic("alice", "pw-alice"), **HTML)
    html = client.get("/browse/books/Good%20Author", headers=h).text
    epub = re.search(r"<li class='pl-row pl-file'>(.*?)</li>", html, re.S).group(1)
    assert re.search(r"<a class='pl-title' href='/read/books/Good%20Author/[^']+\.epub'", epub)           # title -> reader
    assert re.search(r"<a class='pl-dl' href='/dl/books/Good%20Author/[^']+\.epub' title='Download'", epub)  # icon -> download
    assert epub.index("pl-dl") < epub.index("pl-title")                                                    # download comes first (left)
    assert "pl-i-download" in epub
    other = client.get("/browse/comics/Plain", headers=h).text                                             # a non-book: the viewer
    assert "<a class='pl-title' href='/view/comics/Plain/notes.txt'" in other
    assert "<li class='pl-row pl-folder'>" in client.get("/browse/comics", headers=h).text

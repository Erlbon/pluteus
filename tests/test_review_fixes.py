"""Regression tests for the 2026-10-11 review findings (each one was reproduced before it was fixed)."""
import os
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
    return re.findall(r"<span class='pl-title'>([^<]+)</span>", text)


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

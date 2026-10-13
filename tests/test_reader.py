"""The in-browser EPUB reader page, its assets, and the reading-progress API."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import basic
from starlette.testclient import TestClient
from test_login_web import HTML, login, make, post

BOOK = "Good%20Author/Real%20Book.epub"
READ = "/read/books/" + BOOK
API = "/api/progress/books/" + BOOK
STATIC = Path(__file__).resolve().parent.parent / "pluteus" / "static"
ADULT_FILE = "/api/progress/comics/Adult%20Pub/Naughty%20%282000%29/n.cbz"


@pytest.fixture
def signed_in(cfg, tmp_path):
    client, app = make(cfg, tmp_path)
    login(client)
    return client, app


def put(client, url, body, content_type="application/json", origin="http://testserver"):
    headers = {"content-type": content_type}
    if origin:
        headers["origin"] = origin
    return client.put(url, content=body if isinstance(body, (bytes, str)) else json.dumps(body), headers=headers)


# ---- the page --------------------------------------------------------------------------------------
def test_reader_page_carries_everything_the_script_needs(signed_in):
    client, _ = signed_in
    r = client.get(READ, headers=HTML)
    assert r.status_code == 200
    html = r.text
    assert "id='reader'" in html and "pl-reader-page" in html
    assert "data-book='/dl/books/Good%20Author/Real%20Book.epub'" in html
    assert "data-progress='/api/progress/books/Good%20Author/Real%20Book.epub'" in html
    assert "data-content-css='/theme/_/reader-content.css'" in html
    assert "data-key='" in html
    assert "The Real Title" in html and "Good Author" in html                      # from the book's own metadata
    assert "href='/browse/books/Good%20Author'" in html                           # back to its folder
    scripts = [html.index("vendor/jszip.min.js"), html.index("vendor/epub.min.js"), html.index("reader-epub.js")]
    assert scripts == sorted(scripts)                                              # JSZip, then epub.js, then the reader
    assert "script-src 'self'" in r.headers["content-security-policy"]
    for control in ("toc-btn", "settings-btn", "fs-btn", "size-up", "id='font'", "id='line'", "id='flow'", "id='spread'",
                    "pl-scheme"):
        assert control in html
    assert "<option value='single'>One page, centered</option>" in html and "<option value='double'>" in html   # the page-width choice


def test_a_book_without_metadata_is_titled_by_its_file_name(signed_in):
    client, _ = signed_in
    html = client.get("/read/books/Good%20Author/Plain%20Filename.epub", headers=HTML).text
    assert "<div class='pl-reader-title' title=''>Plain Filename</div>" in html


def test_reader_needs_a_login_and_only_opens_epubs_you_may_see(cfg, tmp_path, signed_in):
    client, app = signed_in
    anon = TestClient(app, follow_redirects=False)
    r = anon.get(READ, headers=HTML)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=%2Fread%2Fbooks")
    assert client.get("/read/comics/3%20-%20Marvel%20%281961-2012%29/Spider-Man%20%281963-%29/Amazing%20001.cbz",
                      headers=HTML).status_code == 422                              # a CBZ, but this one is not a zip
    assert client.get("/read/books/Good%20Author/Nope.epub", headers=HTML).status_code == 404
    assert client.get("/read/books/%2e%2e/secret.epub", headers=HTML).status_code in (400, 404)
    assert client.get("/read/books/Good%20Author", headers=HTML).status_code == 404   # a folder is not a book
    carol = TestClient(app, follow_redirects=False)
    login(carol, "carol", "pw-carol")
    assert carol.get(READ, headers=HTML).status_code == 404                           # carol has no 'books' root


def test_listing_offers_read_for_epubs_and_comics_but_not_other_files(signed_in):
    client, _ = signed_in
    books = client.get("/browse/books/Good%20Author", headers=HTML).text
    assert "href='/read/books/Good%20Author/Real%20Book.epub'" in books
    comics = client.get("/browse/comics/3%20-%20Marvel%20%281961-2012%29/Spider-Man%20%281963-%29", headers=HTML).text
    assert "href='/read/comics/3%20-%20Marvel%20%281961-2012%29/Spider-Man%20%281963-%29/Amazing%20001.cbz'" in comics


# ---- assets --------------------------------------------------------------------------------------------
def test_reader_assets_are_served_and_vendored_libraries_are_intact(signed_in):
    client, _ = signed_in
    for url, minimum in (("/static/reader-epub.js", 4000), ("/static/vendor/epub.min.js", 200_000),
                         ("/static/vendor/jszip.min.js", 90_000)):
        r = client.get(url)
        assert r.status_code == 200 and len(r.content) > minimum, url
    assert (STATIC / "vendor" / "LICENSE-epubjs.txt").read_text("utf-8").startswith("Copyright (c) 2013, FuturePress")
    assert b"Stuart Knightley" in (STATIC / "vendor" / "LICENSE-jszip.txt").read_bytes()
    readme = (STATIC / "vendor" / "README.md").read_text("utf-8")
    assert "0.3.93" in readme and "3.10.1" in readme and "sha512" in readme


def test_the_book_page_stylesheet_follows_the_users_theme_with_fallback(signed_in, tmp_path):
    client, _ = signed_in
    css = client.get("/theme/_/reader-content.css")
    assert css.status_code == 200 and "var(--pl-book-bg)" in css.text
    own = tmp_path / "themes" / "ocean" / "reader-content.css"
    own.write_text("body { letter-spacing: .02em !important; }", encoding="utf-8")
    assert "var(--pl-book-bg)" in client.get("/theme/_/reader-content.css").text        # still the default
    post(client, "/settings", {"theme": "ocean"})
    assert "letter-spacing" in client.get("/theme/_/reader-content.css").text           # now the theme's own


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_reader_script_parses():
    done = subprocess.run(["node", "--check", str(STATIC / "reader-epub.js")], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


# ---- progress API -----------------------------------------------------------------------------------------------
def test_progress_roundtrip_replace_and_clamp(signed_in):
    client, app = signed_in
    assert client.get(API).json() == {}
    r = put(client, API, {"locator": "epubcfi(/6/4!/4/2)", "percent": 0.25})
    assert r.status_code == 200 and r.json() == {"ok": True}
    got = client.get(API).json()
    assert got["locator"] == "epubcfi(/6/4!/4/2)" and got["percent"] == 0.25 and got["updated"] > 0
    put(client, API, {"locator": "epubcfi(/6/8)", "percent": 5})
    assert client.get(API).json()["percent"] == 1.0 and client.get(API).json()["locator"] == "epubcfi(/6/8)"
    assert app.state.state.get_progress("alice", "books", "Good Author/Real Book.epub")["percent"] == 1.0


def test_progress_is_private_to_each_user(signed_in):
    client, app = signed_in
    put(client, API, {"locator": "mine", "percent": 0.4})
    bob = TestClient(app, follow_redirects=False)
    login(bob, "bob", "pw-bob")
    assert bob.get(API).json() == {}
    put(bob, API, {"locator": "bobs", "percent": 0.9})
    assert client.get(API).json()["locator"] == "mine"


def test_progress_api_answers_json_errors_and_never_a_challenge_or_redirect(signed_in):
    client, app = signed_in
    anon = TestClient(app, follow_redirects=False)
    r = anon.get(API, headers=HTML)
    assert r.status_code == 401 and r.json() == {"error": "login required"} and "www-authenticate" not in r.headers
    assert put(anon, API, {"locator": "x", "percent": 0.1}).status_code == 401


@pytest.mark.parametrize("body,kind,expected", [
    ('{"locator": "x", "percent": 0.5}', "text/plain", 415),
    ("{not json", "application/json", 400),
    ('{"percent": 0.5}', "application/json", 400),
    ('{"locator": "x"}', "application/json", 400),
    ('{"locator": "", "percent": 0.5}', "application/json", 400),
    ('{"locator": 5, "percent": 0.5}', "application/json", 400),
    ('{"locator": "x", "percent": "much"}', "application/json", 400),
    ('{"locator": "x", "percent": NaN}', "application/json", 400),
    ('["locator", "percent"]', "application/json", 400),
    (json.dumps({"locator": "x" * 3000, "percent": 0.5}), "application/json", 400),
    (json.dumps({"locator": "x", "percent": 0.5, "pad": "y" * 9000}), "application/json", 413)])
def test_progress_put_validates_its_input(signed_in, body, kind, expected):
    client, _ = signed_in
    assert put(client, API, body, content_type=kind).status_code == expected
    assert client.get(API).json() == {}                                              # nothing was stored


def test_progress_writes_must_come_from_this_site(signed_in):
    client, _ = signed_in
    assert put(client, API, {"locator": "x", "percent": 0.1}, origin=None).status_code == 403
    assert put(client, API, {"locator": "x", "percent": 0.1}, origin="http://evil.example").status_code == 403
    assert client.get(API).json() == {}


def test_progress_exists_only_for_files_you_can_see(signed_in, cfg, tmp_path):
    client, app = signed_in
    assert put(client, "/api/progress/books/Good%20Author/Nope.epub", {"locator": "x", "percent": 0.1}).status_code == 404
    assert put(client, ADULT_FILE, {"locator": "x", "percent": 0.1}).status_code == 404       # alice may not see adult folders
    bob = TestClient(app, follow_redirects=False)
    login(bob, "bob", "pw-bob")
    assert put(bob, ADULT_FILE, {"locator": "x", "percent": 0.1}).status_code == 200
    assert client.get("/api/progress/books/%2e%2e/secret.txt").status_code in (400, 404)


def test_basic_auth_users_can_use_the_progress_api_too(cfg, tmp_path):
    client, _ = make(cfg, tmp_path)
    h = basic("alice", "pw-alice")
    r = client.put(API, content=json.dumps({"locator": "x", "percent": 0.3}),
                   headers=dict(h, **{"content-type": "application/json", "origin": "http://testserver"}))
    assert r.status_code == 200
    assert client.get(API, headers=h).json()["percent"] == 0.3


# ---- continue reading ---------------------------------------------------------------------------------------------
def test_home_lists_unfinished_epubs_newest_first(signed_in):
    client, app = signed_in
    st = app.state.state
    st.put_progress("alice", "books", "Good Author/Real Book.epub", "a", 0.5, now=100)
    st.put_progress("alice", "books", "Good Author/Plain Filename.epub", "b", 0.99, now=200)       # finished: hidden
    st.put_progress("alice", "comics", "Plain/notes.txt", "c", 0.2, now=300)                       # no progress kind: hidden
    st.put_progress("alice", "gone", "x.epub", "d", 0.2, now=400)                                  # root no longer exists
    html = client.get("/", headers=HTML).text
    assert "Pick up where you left off" in html and "50%" in html
    assert "href='/read/books/Good%20Author/Real%20Book.epub'" in html
    assert "Plain Filename" not in html and "notes" not in html and "x.epub" not in html


def test_home_hides_continue_reading_for_roots_a_user_lost(signed_in):
    client, app = signed_in
    app.state.state.put_progress("carol", "books", "Good Author/Real Book.epub", "a", 0.5)
    carol = TestClient(app, follow_redirects=False)
    login(carol, "carol", "pw-carol")
    assert "Pick up where you left off" not in carol.get("/", headers=HTML).text


def test_epub_reader_defaults_to_one_centered_page_with_a_two_page_option():
    from pathlib import Path
    static = Path(__file__).resolve().parent.parent / "pluteus" / "static"
    js = (static / "reader-epub.js").read_text(encoding="utf-8")
    assert 'spread: "single"' in js                                          # the default
    assert 'settings.spread === "double" ? "auto" : "none"' in js           # single page = no spread
    assert "pl-single" in js and 'rendition.spread(' in js and '"spread"' in js
    css = (static / "base.css").read_text(encoding="utf-8")
    assert ".pl-reader.pl-single #viewer" in css and "min(100%, 50rem)" in css   # centered, a comfortable line length


def test_android_back_and_pull_to_refresh_handling(signed_in):
    from pathlib import Path
    static = Path(__file__).resolve().parent.parent / "pluteus" / "static"
    client, _ = signed_in
    html = client.get(READ, headers=HTML).text
    assert html.index("reader-epub.js") < html.index("menus-back.js")                    # the menus exist before it looks for them
    back = (static / "menus-back.js").read_text(encoding="utf-8")
    for needle in ("pushState", "history.back()", "popstate", "MutationObserver", ".pl-menu"):
        assert needle in back, needle
    for name in ("viewer-media.js", "viewer-image.js"):                                   # moving between files must not pile up history
        js = (static / name).read_text(encoding="utf-8")
        assert "location.replace" in js and "location.assign" not in js and "location.href =" not in js, name
    css = (static / "base.css").read_text(encoding="utf-8")
    assert "html:has(body.pl-reader-page) { overscroll-behavior-y: contain; }" in css
    content = (static / "themes" / "default" / "reader-content.css").read_text(encoding="utf-8")
    assert "overscroll-behavior: contain" in content                                      # the book's own iframe too


def test_the_epub_reader_turns_pages_with_the_mouse_wheel():
    import os
    js = open(os.path.join(os.path.dirname(__file__), "..", "pluteus", "static", "reader-epub.js"), encoding="utf-8").read()
    assert 'document.addEventListener("wheel", onWheel, { passive: false })' in js
    assert 'contents.document.addEventListener("wheel", onWheel' in js                    # also over the book page itself
    assert 'settings.flow !== "paginated" || e.ctrlKey' in js                              # the scrolling view and Ctrl+wheel are left alone
    assert "wheelLock = now + 350" in js                                                   # one page per gesture


def test_the_epub_reader_forwards_the_wheel_to_the_scrolling_box_in_the_scrolling_layout():
    import os
    js = open(os.path.join(os.path.dirname(__file__), "..", "pluteus", "static", "reader-epub.js"), encoding="utf-8").read()
    assert 'settings.flow !== "paginated"' in js and 'document.querySelector(".epub-container")' in js
    assert "box.scrollBy(" in js and "e.deltaMode === 1" in js                         # lines, pixels and pages are all handled
    assert js.index("e.ctrlKey || !rendition.manager") < js.index("box.scrollBy(")     # Ctrl + wheel and an unready book are left alone

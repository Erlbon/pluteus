"""The EPUB reader shows a book in its own font and line spacing unless the reader picks another (Saw Hammer sets Libre Baskerville at 1.3:
the reader used to force its theme font and 1.55 on the text, while the headings kept the book's font). Checked in a real browser.
Needs Playwright and Google Chrome; skipped where one is missing."""
import socket
import threading
import time
import zipfile

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

from conftest import FAST                                                  # noqa: E402
from test_login_web import make                                            # noqa: E402

from pluteus.auth import hash_password                                     # noqa: E402
from pluteus.config import parse_config                                    # noqa: E402

CSS = 'body { font-family: "Book Face", monospace; line-height: 1.3; } h1 { font-family: "Book Face", monospace; }'
CHAPTER = ("<?xml version='1.0' encoding='utf-8'?><html xmlns='http://www.w3.org/1999/xhtml'><head><title>One</title>"
           "<link rel='stylesheet' type='text/css' href='style.css'/></head><body><h1>One</h1>"
           + "<p>Words on a page that the reader shows.</p>" * 20 + "</body></html>")
OPF = ("<?xml version='1.0' encoding='utf-8'?><package xmlns='http://www.idpf.org/2007/opf' version='3.0' unique-identifier='id'>"
       "<metadata xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:identifier id='id'>urn:uuid:42</dc:identifier><dc:title>Styled</dc:title>"
       "<dc:language>en</dc:language></metadata><manifest><item id='c1' href='c1.xhtml' media-type='application/xhtml+xml'/>"
       "<item id='css' href='style.css' media-type='text/css'/></manifest><spine><itemref idref='c1'/></spine></package>")
CONTAINER = ("<?xml version='1.0'?><container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'><rootfiles>"
             "<rootfile full-path='OEBPS/content.opf' media-type='application/oebps-package+xml'/></rootfiles></container>")
LOOK = """() => { const d = document.querySelector('#viewer iframe').contentDocument, p = d.querySelector('p'), s = getComputedStyle(p);
                  return {font: s.fontFamily, line: parseFloat(s.lineHeight) / parseFloat(s.fontSize)}; }"""


@pytest.fixture
def site(tmp_path):
    books = tmp_path / "lib"
    books.mkdir()
    with zipfile.ZipFile(books / "Styled.epub", "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", OPF)
        z.writestr("OEBPS/c1.xhtml", CHAPTER)
        z.writestr("OEBPS/style.css", CSS)
    cfg = parse_config({"roots": [{"name": "lib", "path": str(books)}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw-alice", **FAST), "roots": ["*"]}]})
    _, app = make(cfg, tmp_path)
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    yield "http://127.0.0.1:%d" % port
    server.should_exit = True
    thread.join(10)


@pytest.fixture
def page(site):
    with playwright_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True, args=["--mute-audio"])
        except Exception as e:                                             # no Google Chrome on this machine
            pytest.skip("Google Chrome is needed: %s" % e)
        page = browser.new_page()
        page.goto(site + "/login")
        page.fill("input[name=username]", "alice")
        page.fill("input[name=password]", "pw-alice")
        page.click("button[type=submit]")
        page.wait_for_load_state()
        page.site = site
        yield page
        browser.close()


def open_book(page, saved=None):
    if saved is not None:                                                  # what an earlier version of the reader left behind
        page.evaluate("s => localStorage.setItem('pluteus-reader', s)", saved)
    page.goto(page.site + "/read/lib/Styled.epub?noshell=1")
    page.wait_for_function("() => { const f = document.querySelector('#viewer iframe'); return f && f.contentDocument.querySelector('p'); }")
    return page.evaluate(LOOK)


def test_a_book_keeps_its_own_font_and_line_spacing(page):
    look = open_book(page)
    assert look["font"].startswith('"Book Face"') and abs(look["line"] - 1.3) < 0.02
    assert page.input_value("#font") == "book" and page.input_value("#line") == "book"


def test_the_reader_choices_still_replace_the_books_look(page):
    open_book(page)
    page.click("#settings-btn")
    page.select_option("#font", "sans")
    page.select_option("#line", "1.8")
    look = page.evaluate(LOOK)
    assert "Book Face" not in look["font"] and abs(look["line"] - 1.8) < 0.02
    page.select_option("#font", "book")
    page.select_option("#line", "book")
    look = page.evaluate(LOOK)
    assert look["font"].startswith('"Book Face"') and abs(look["line"] - 1.3) < 0.02


def test_the_old_defaults_saved_by_an_earlier_reader_move_to_the_books_own(page):
    page.goto(page.site + "/login")
    look = open_book(page, '{"size": 110, "font": "theme", "line": "1.55", "flow": "paginated", "spread": "single"}')
    assert look["font"].startswith('"Book Face"') and abs(look["line"] - 1.3) < 0.02
    assert page.evaluate("() => JSON.parse(localStorage.getItem('pluteus-reader')).size") == 110        # the other choices stay


def test_a_choice_made_on_purpose_is_kept(page):
    page.goto(page.site + "/login")
    look = open_book(page, '{"font": "serif", "line": "1.8", "v": 2}')
    assert "Book Face" not in look["font"] and abs(look["line"] - 1.8) < 0.02

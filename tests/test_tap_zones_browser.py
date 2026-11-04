"""Turning pages by tapping, in a real browser, the same in the EPUB, comic, PDF and picture readers: the page is split top to bottom so
either thumb reaches forward (a left hand could not reach the old right-hand edge). Upper part: back a page. A band below it: show or hide
the reader's bars. The rest: forward. The first 24 px under the bar do nothing (a tap that misses a bar button turns no page).
Needs Playwright and Google Chrome; skipped where one is missing."""
import io
import socket
import threading
import time
import zipfile

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

from PIL import Image                                                      # noqa: E402

from cbz_factory import make_cbz, png                                     # noqa: E402
from conftest import FAST                                                  # noqa: E402
from test_login_web import make                                            # noqa: E402

from pluteus.auth import hash_password                                     # noqa: E402
from pluteus.config import parse_config                                    # noqa: E402

PHONE = {"width": 375, "height": 740}
CHAPTER = ("<?xml version='1.0' encoding='utf-8'?><html xmlns='http://www.w3.org/1999/xhtml'><head><title>%d</title></head><body>"
           "<h1>Chapter %d</h1><p><a id='note' href='c3.xhtml'>A link in the text</a></p>%s</body></html>")
OPF = ("<?xml version='1.0' encoding='utf-8'?><package xmlns='http://www.idpf.org/2007/opf' version='3.0' unique-identifier='id'>"
       "<metadata xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:identifier id='id'>urn:uuid:7</dc:identifier><dc:title>Long</dc:title>"
       "<dc:language>en</dc:language></metadata><manifest>%s</manifest><spine>%s</spine></package>")
CONTAINER = ("<?xml version='1.0'?><container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'><rootfiles>"
             "<rootfile full-path='OEBPS/content.opf' media-type='application/oebps-package+xml'/></rootfiles></container>")


def build_library(root):
    books = root / "lib"
    books.mkdir()
    with zipfile.ZipFile(books / "Long.epub", "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER)
        items = "".join("<item id='c%d' href='c%d.xhtml' media-type='application/xhtml+xml'/>" % (n, n) for n in (1, 2, 3))
        z.writestr("OEBPS/content.opf", OPF % (items, "".join("<itemref idref='c%d'/>" % n for n in (1, 2, 3))))
        for n in (1, 2, 3):
            z.writestr("OEBPS/c%d.xhtml" % n, CHAPTER % (n, n, "<p>Words that fill page after page of the book. </p>" * 120))
    make_cbz(books / "Comic.cbz", [("p%d.png" % n, png((40 * n, 40, 90), (300, 450))) for n in (1, 2, 3)])
    pages = [Image.new("RGB", (300, 420), c) for c in ("white", "#ddd", "#bbb")]
    pdf = io.BytesIO()
    pages[0].save(pdf, "PDF", save_all=True, append_images=pages[1:])
    (books / "Paper.pdf").write_bytes(pdf.getvalue())
    pics = books / "pics"
    pics.mkdir()
    for n in (1, 2, 3):
        (pics / ("%d.png" % n)).write_bytes(png((90, 40 * n, 40), (300, 400)))


@pytest.fixture
def site(tmp_path):
    build_library(tmp_path)
    cfg = parse_config({"server": {"show_all_files": True}, "roots": [{"name": "lib", "path": str(tmp_path / "lib")}],
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
        page = browser.new_page(viewport=PHONE)
        page.goto(site + "/login")
        page.fill("input[name=username]", "alice")
        page.fill("input[name=password]", "pw-alice")
        page.click("button[type=submit]")
        page.wait_for_load_state()
        page.site = site
        yield page
        browser.close()


def tap(page, share=None, below_bar=None):
    """Click the reader's page at a share of its height (0 = its top edge), or a number of pixels below its top edge."""
    box = page.locator(".pl-reader-stage").bounding_box()
    y = box["y"] + (below_bar if below_bar is not None else share * box["height"])
    page.mouse.click(box["x"] + box["width"] / 2, y)
    page.wait_for_timeout(400)


def bars_shown(page):
    return page.locator(".pl-reader-bar").is_visible() and page.locator(".pl-reader-foot").is_visible()


def check_bars_and_quiet_strip(page, where):
    before = where()
    tap(page, below_bar=10)                                                # just under the bar: a near miss of a bar button
    assert where() == before and bars_shown(page)
    tap(page, 0.36)                                                        # the band: the bars go away for more page ...
    assert not bars_shown(page) and where() == before
    tap(page, 0.36)                                                        # ... and come back
    assert bars_shown(page) and where() == before


def test_the_comic_reader(page):
    page.goto(page.site + "/read/lib/Comic.cbz?noshell=1")
    pct = lambda: page.inner_text("#pct")
    page.wait_for_function("() => document.getElementById('pct').textContent === '1 / 3'")
    tap(page, 0.7)
    assert pct() == "2 / 3"
    tap(page, 0.9)                                                         # the whole lower part, any side
    assert pct() == "3 / 3"
    tap(page, 0.15)
    assert pct() == "2 / 3"
    check_bars_and_quiet_strip(page, pct)


def test_the_pdf_reader(page):
    page.goto(page.site + "/view/lib/Paper.pdf?noshell=1")
    pct = lambda: page.inner_text("#pct")
    page.wait_for_function("() => document.getElementById('pct').textContent === '1 / 3'")
    tap(page, 0.7)
    page.wait_for_function("() => document.getElementById('pct').textContent === '2 / 3'")
    tap(page, 0.15)
    page.wait_for_function("() => document.getElementById('pct').textContent === '1 / 3'")
    check_bars_and_quiet_strip(page, pct)


def test_the_picture_viewer(page):
    from urllib.parse import unquote

    def picture(name):
        page.goto(page.site + "/view/lib/pics/%s?noshell=1" % name)
        page.wait_for_selector("#page")
    picture("2.png")
    tap(page, 0.7)
    page.wait_for_url(lambda url: "pics/3.png" in unquote(url))           # the next picture opens (in the site's frame page)
    picture("2.png")
    tap(page, 0.15)
    page.wait_for_url(lambda url: "pics/1.png" in unquote(url))
    picture("2.png")
    check_bars_and_quiet_strip(page, lambda: page.url)


EPUB_AT = "() => document.querySelector('.epub-container').scrollLeft"


def test_the_epub_reader(page):
    page.goto(page.site + "/read/lib/Long.epub?noshell=1")
    page.wait_for_function("() => { const f = document.querySelector('#viewer iframe'); return f && f.contentDocument.querySelector('p'); }")
    page.wait_for_timeout(500)
    where = lambda: page.evaluate(EPUB_AT)
    start = where()
    tap(page, 0.7)
    page.wait_for_function("s => document.querySelector('.epub-container').scrollLeft > s", arg=start)
    tap(page, 0.15)
    page.wait_for_function("s => document.querySelector('.epub-container').scrollLeft === s", arg=start)
    check_bars_and_quiet_strip(page, where)


def test_epub_a_link_in_the_text_still_works_and_an_open_menu_only_closes(page):
    page.goto(page.site + "/read/lib/Long.epub?noshell=1")
    page.wait_for_function("() => { const f = document.querySelector('#viewer iframe'); return f && f.contentDocument.querySelector('p'); }")
    page.wait_for_timeout(500)
    start = page.evaluate(EPUB_AT)
    page.click("#toc-btn")
    assert page.locator("#toc").is_visible()
    box = page.locator(".pl-reader-stage").bounding_box()
    page.mouse.click(box["x"] + box["width"] - 10, box["y"] + 0.7 * box["height"])     # beside the open menu
    page.wait_for_timeout(400)
    assert not page.locator("#toc").is_visible() and page.evaluate(EPUB_AT) == start     # closed, and no page turned
    page.frame_locator("#viewer iframe").locator("#note").click()                       # a link in the book: it goes to chapter 3
    page.wait_for_function("() => { const f = document.querySelector('#viewer iframe'), h = f && f.contentDocument.querySelector('h1');"
                           " return h && h.textContent === 'Chapter 3'; }")

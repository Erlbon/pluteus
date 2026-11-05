"""A book's own boxes stay readable in the reader's dark scheme. The reader puts its own text colour on the book, but took away the book's
own background only on paragraphs and a few other elements: a stat box drawn as a table with light cells (Betrayed by the Hero's Party)
showed light text on a light box. Checked in a real browser, in both schemes.
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

CSS = ("table.stats { background: #eeece6; border: 8px solid #333; border-collapse: collapse; }"
       " td, th { background-color: #f2f0ea; color: #bbb; padding: 4px; }"
       " th { letter-spacing: .2em; font-variant: small-caps; color: #c8c8c8; }"
       " aside.note { background: #fafafa; }")
CHAPTER = ("<?xml version='1.0' encoding='utf-8'?><html xmlns='http://www.w3.org/1999/xhtml'><head><title>One</title>"
           "<link rel='stylesheet' type='text/css' href='style.css'/></head><body><p>By the third week the mark had more to say.</p>"
           "<table class='stats'><tr><th></th><td>The mark lit: HEALER, LEVEL 4</td></tr><tr><th>Body</th><td>60</td></tr>"
           "<tr><th>Burden</th><td>0 of 48</td></tr><tr><th>Skills</th><td>Triage, Draw, Release</td></tr></table>"
           "<aside class='note'><small>A note in a pale box.</small></aside><p>Level four, after three weeks.</p></body></html>")
OPF = ("<?xml version='1.0' encoding='utf-8'?><package xmlns='http://www.idpf.org/2007/opf' version='3.0' unique-identifier='id'>"
       "<metadata xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:identifier id='id'>urn:uuid:43</dc:identifier><dc:title>Boxes</dc:title>"
       "<dc:language>en</dc:language></metadata><manifest><item id='c1' href='c1.xhtml' media-type='application/xhtml+xml'/>"
       "<item id='css' href='style.css' media-type='text/css'/></manifest><spine><itemref idref='c1'/></spine></package>")
CONTAINER = ("<?xml version='1.0'?><container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'><rootfiles>"
             "<rootfile full-path='OEBPS/content.opf' media-type='application/oebps-package+xml'/></rootfiles></container>")
# the contrast (WCAG) of every piece of text in the book with the background it is drawn on: the first box up the tree that has one
CONTRASTS = """() => {
  const d = document.querySelector('#viewer iframe').contentDocument, w = d.defaultView;
  const rgb = c => (c.match(/[\\d.]+/g) || []).map(Number);
  const lum = c => { const v = rgb(c).slice(0, 3).map(x => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
                     return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
  const behind = el => { for (; el; el = el.parentElement) { const c = w.getComputedStyle(el).backgroundColor, a = rgb(c);
                         if (a.length === 3 || a[3] > 0) return c; } return 'rgb(255, 255, 255)'; };
  return Array.from(d.querySelectorAll('p, td, th, small')).filter(el => el.textContent.trim()).map(el => {
    const fg = lum(w.getComputedStyle(el).color), bg = lum(behind(el));
    return [el.textContent.trim(), (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05)];
  });
}"""


@pytest.fixture
def site(tmp_path):
    books = tmp_path / "lib"
    books.mkdir()
    with zipfile.ZipFile(books / "Boxes.epub", "w") as z:
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


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_text_in_the_books_own_boxes_is_readable(site, scheme):
    with playwright_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True, args=["--mute-audio"])
        except Exception as e:                                             # no Google Chrome on this machine
            pytest.skip("Google Chrome is needed: %s" % e)
        page = browser.new_page(color_scheme=scheme)                       # no saved choice: the system's scheme decides
        page.goto(site + "/login")
        page.fill("input[name=username]", "alice")
        page.fill("input[name=password]", "pw-alice")
        page.click("button[type=submit]")
        page.wait_for_load_state()
        page.goto(site + "/read/lib/Boxes.epub?noshell=1")
        page.wait_for_function("() => { const f = document.querySelector('#viewer iframe'); return f && f.contentDocument.querySelector('td'); }")
        page.wait_for_timeout(500)
        poor = [(text, round(ratio, 1)) for text, ratio in page.evaluate(CONTRASTS) if ratio < 4.5]
        browser.close()
    assert poor == []

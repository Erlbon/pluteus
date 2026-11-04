"""On a touch screen a tap on a word in the EPUB reader turns the page, and must not also make the phone's browser select the word and
open its "explain this word" panel (Chrome's Touch to Search). So the book text is not selectable on a coarse pointer; with a mouse it
stays selectable, for copying a line. Checked in a real browser. Needs Playwright and Google Chrome; skipped where one is missing."""
import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from test_reader_book_fonts_browser import site                            # noqa: E402,F401  (the fixture: a library with one book)

SELECT = """() => { const d = document.querySelector('#viewer iframe').contentDocument, s = getComputedStyle(d.querySelector('p'));
                    return {select: s.userSelect || s.webkitUserSelect, callout: s.webkitTouchCallout || ''}; }"""


def open_book(site, **context):
    with playwright_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True, args=["--mute-audio"])
        except Exception as e:                                             # no Google Chrome on this machine
            pytest.skip("Google Chrome is needed: %s" % e)
        page = browser.new_context(**context).new_page()
        page.goto(site + "/login")
        page.fill("input[name=username]", "alice")
        page.fill("input[name=password]", "pw-alice")
        page.click("button[type=submit]")
        page.wait_for_load_state()
        page.goto(site + "/read/lib/Styled.epub?noshell=1")
        page.wait_for_function("() => { const f = document.querySelector('#viewer iframe'); return f && f.contentDocument.querySelector('p'); }")
        look = page.evaluate(SELECT)
        browser.close()
        return look


def test_a_tap_on_a_phone_does_not_select_the_word(site):
    look = open_book(site, has_touch=True, is_mobile=True, viewport={"width": 390, "height": 800})
    assert look["select"] == "none"


def test_with_a_mouse_the_text_stays_selectable(site):
    look = open_book(site)
    assert look["select"] != "none"

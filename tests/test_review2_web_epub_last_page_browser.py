"""EPUB reader: every page of a chapter is shown, also on a screen with a fractional device pixel ratio (review card 37).
epub.js asked "is there another page in this chapter" with scrollLeft + offsetWidth + delta <= scrollWidth. On a screen whose pixel
ratio is not a whole number the browser snaps the scroll offset to a device pixel, so it reads back as, say, 411.0476 for a 411-pixel
page; on the second-to-last page the sum then overshoots scrollWidth by a fraction of a pixel and the reader left the chapter with its
last page unread. The vendored test now allows half a page of slack (see static/vendor/README.md).

The pixel ratio has to be the browser's real one (--force-device-scale-factor): with the emulated ratio that Playwright's
device_scale_factor sets, Chrome keeps scroll offsets on whole CSS pixels and the fault does not show.
Needs Playwright and Google Chrome; skipped where one is missing."""
import zipfile

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from test_tap_zones_browser import site                                    # noqa: E402,F401  (fixture: Long.epub, three chapters)

AT = """() => { const f = document.querySelector('#viewer iframe'), h = f && f.contentDocument.querySelector('h1'),
  c = document.querySelector('.epub-container');
  return {chapter: h ? h.textContent : '', left: c.scrollLeft, page: c.offsetWidth, all: c.scrollWidth}; }"""
IDLE = "!document.getElementById('reader').classList.contains('pl-turning')"
# after a press: the turn is over, a chapter is on the page, and the place is another one than before
TURNED = ("b => { if (%s) { const a = (%s)(); return !!a.chapter && (a.chapter !== b.chapter || Math.abs(a.left - b.left) > 1); }"
          " return false; }" % (IDLE, AT))
STABLE = """() => { const c = document.querySelector('.epub-container'), f = document.querySelector('#viewer iframe');
  if (!c || !f || !f.contentDocument.querySelector('p')) return false;
  const now = c.scrollWidth + ':' + c.offsetWidth, same = window.__seen === now && Date.now() - window.__since > 600;
  if (window.__seen !== now) { window.__seen = now; window.__since = Date.now(); }
  return same; }"""


def open_book(p, site, ratio, width, book="Long.epub"):                    # noqa: F811
    try:
        browser = p.chromium.launch(channel="chrome", headless=True,
                                    args=["--mute-audio", "--force-device-scale-factor=%s" % ratio, "--window-size=800,740"])
    except Exception as e:                                                 # no Google Chrome on this machine
        pytest.skip("Google Chrome is needed: %s" % e)
    page = browser.new_context(no_viewport=True).new_page()               # no emulated screen: the pixel ratio stays the real one
    # a phone's width; deviceScaleFactor 0 leaves the browser's own ratio alone
    page.context.new_cdp_session(page).send("Emulation.setDeviceMetricsOverride",
                                            {"width": width, "height": 740, "deviceScaleFactor": 0, "mobile": False})
    page.goto(site + "/login")
    page.fill("input[name=username]", "alice")
    page.fill("input[name=password]", "pw-alice")
    page.click("button[type=submit]")
    page.wait_for_load_state()
    page.goto(site + "/read/lib/%s?noshell=1" % book)
    page.wait_for_function(STABLE, timeout=20000)                          # the chapter is laid out and its width has stopped changing
    assert abs(page.evaluate("devicePixelRatio") - ratio) < 0.001 and page.evaluate("innerWidth") == width
    return browser, page


def turn(page, key):
    """Press a page key once the last turn is over; the place after the turn, or None when the press turned no page."""
    page.wait_for_function("() => " + IDLE)
    before = page.evaluate(AT)
    page.keyboard.press(key)
    try:
        page.wait_for_function(TURNED, arg=before, timeout=5000)
    except playwright_api.TimeoutError:
        return None
    return page.evaluate(AT)


# 2.625 at 411 px and 2.75 at 393 px are common Android phones; 1.5 is a laptop at 150 %; 1, 1.25 and 3 never showed the fault
@pytest.mark.parametrize("ratio,width", [(2.625, 411), (2.75, 393), (1.5, 411), (1.25, 411), (1, 411), (3, 360)])
def test_every_page_of_a_chapter_is_shown_forward_and_back(site, ratio, width):   # noqa: F811
    with playwright_api.sync_playwright() as p:
        browser, page = open_book(p, site, ratio, width)
        try:
            walk(page, ratio, "ArrowRight", "ArrowLeft")
        finally:
            browser.close()


def stops(page, key, chapter, ratio):
    """Press the key until another chapter opens: the places shown in this chapter after each press, and the place in the other chapter."""
    seen = []
    while True:
        here = turn(page, key)
        assert here is not None, "a %s press turned no page in %s (ratio %s), after %s" % (key, chapter, ratio, seen)
        if here["chapter"] != chapter:
            return seen, here
        seen.append(round(abs(here["left"]), 2))
        assert len(seen) < 40, "never left %s: %s" % (chapter, seen)


def walk(page, ratio, forward, back):
    """Chapter 1 forward, chapter 2 forward, then chapter 2 back: every page once, no page skipped, no press that turns nothing."""
    here = page.evaluate(AT)
    assert here["chapter"] == "Chapter 1" and here["left"] == 0
    pages = round(here["all"] / here["page"])
    assert pages >= 4                                                      # long enough for the rounding error to build up
    last = here["all"] - here["page"]                                      # where the chapter's last page starts

    for chapter, following in (("Chapter 1", "Chapter 2"), ("Chapter 2", "Chapter 3")):
        seen, here = stops(page, forward, chapter, ratio)
        seen.insert(0, 0)                                                  # the page the chapter opened at
        assert len(seen) == pages, "%s has %d pages, %d were shown going forward at ratio %s: %s" % (chapter, pages, len(seen), ratio, seen)
        assert abs(seen[-1] - last) <= 3                                   # the last one shown is the chapter's last page
        assert here["chapter"] == following and here["left"] == 0

    seen, here = stops(page, back, "Chapter 3", ratio)                     # back: chapter 2 opens at its last page ...
    assert seen == [] and here["chapter"] == "Chapter 2" and abs(abs(here["left"]) - last) <= 3
    seen, here = stops(page, back, "Chapter 2", ratio)                     # ... every press goes one page back, none wasted ...
    seen.insert(0, last)
    assert len(seen) == pages, "going back through the %d pages of chapter 2 took %d stops at ratio %s: %s" % (pages, len(seen), ratio, seen)
    assert here["chapter"] == "Chapter 1" and abs(abs(here["left"]) - last) <= 3     # ... and then chapter 1's last page


@pytest.mark.parametrize("ratio", [2.625, 1.5, 1])
def test_a_right_to_left_book_shows_every_page_too(site, tmp_path, ratio):   # noqa: F811
    """The same walk in a book that reads right to left (its scroll offsets are negative in Chrome): epub.js has branches of its own for it."""
    with zipfile.ZipFile(tmp_path / "lib" / "Long.epub") as src, zipfile.ZipFile(tmp_path / "lib" / "Rtl.epub", "w") as out:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename.endswith(".opf"):
                data = data.replace(b"<spine>", b"<spine page-progression-direction='rtl'>")
                assert b"page-progression-direction" in data
            out.writestr(item, data)
    with playwright_api.sync_playwright() as p:
        browser, page = open_book(p, site, ratio, 411, "Rtl.epub")
        try:
            assert page.evaluate("() => document.querySelector('.epub-container').dir") == "rtl"
            walk(page, ratio, "ArrowLeft", "ArrowRight")                   # in such a book the left arrow goes on
        finally:
            browser.close()

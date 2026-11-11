"""EPUB reader: one page turn at a time. epub.js queues every turn, and stepping into the next chapter is slow (the chapter is loaded and
laid out first), so a second tap made while it loaded used to run afterwards and skip the new chapter's first page. A turn asked for while
one is running is now dropped; taps made after a turn has finished still each turn a page.
Needs Playwright and Google Chrome; skipped where one is missing."""
import pytest

pytest.importorskip("playwright.sync_api")

from test_tap_zones_browser import page, site, tap                        # noqa: E402,F401  (fixtures)

AT = """() => { const f = document.querySelector('#viewer iframe'), h = f && f.contentDocument.querySelector('h1');
  return [h ? h.textContent : '', document.querySelector('.epub-container').scrollLeft]; }"""


def open_book(page):
    page.context.new_cdp_session(page).send("Emulation.setCPUThrottlingRate", {"rate": 6})     # a phone's speed: the chapter step is slow
    page.goto(page.site + "/read/lib/Long.epub?noshell=1")
    page.wait_for_function("() => { const f = document.querySelector('#viewer iframe'); return f && f.contentDocument.querySelector('p'); }")
    page.wait_for_timeout(1500)


def settle(page, before):
    page.wait_for_function("b => JSON.stringify((%s)()) !== JSON.stringify(b)" % AT, arg=before, timeout=15000)
    page.wait_for_timeout(1200)


def quick_taps(page, gap_ms):
    box = page.locator(".pl-reader-stage").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + 0.7 * box["height"]
    page.mouse.click(x, y)
    page.wait_for_timeout(gap_ms)
    page.mouse.click(x, y)


def test_a_second_tap_while_the_next_chapter_loads_does_not_skip_its_first_page(page):
    open_book(page)
    page.keyboard.press("ArrowRight")
    while page.evaluate(AT)[0] == "Chapter 1":                            # forward into chapter 2 ...
        before = page.evaluate(AT)
        page.keyboard.press("ArrowRight")
        settle(page, before)
    before = page.evaluate(AT)
    page.keyboard.press("ArrowLeft")                                       # ... and one back: chapter 1's last page
    settle(page, before)
    last = page.evaluate(AT)
    assert last[0] == "Chapter 1" and last[1] > 0
    quick_taps(page, 40)                                                   # the second tap lands while chapter 2 is loading
    page.wait_for_timeout(3000)
    assert page.evaluate(AT) == ["Chapter 2", 0]                           # chapter 2's first page, not its second


def test_taps_after_a_finished_turn_each_still_turn_a_page(page):
    open_book(page)
    start = page.evaluate(AT)
    quick_taps(page, 700)                                                  # inside a chapter, a turn is done well within this
    page.wait_for_timeout(1500)
    width = page.evaluate("() => document.querySelector('.epub-container').offsetWidth")
    here = page.evaluate(AT)
    assert here[0] == start[0] and abs(here[1] - start[1] - 2 * width) <= 2

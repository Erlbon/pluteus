"""EPUB reader: the opt-in tap and turn log (turnlog.js), the tool for finding a double page turn on a real phone.
It must be off and silent by default, record taps and turns when switched on, record what a single tap became (so a tap that arrives
twice shows), keep book text out of the log, and leave nothing behind when switched off again.
Needs Playwright and Google Chrome; skipped where one is missing."""
import re

import pytest

pytest.importorskip("playwright.sync_api")

from test_tap_zones_browser import page, site, tap                        # noqa: E402,F401  (fixtures)

BOOK_SHOWN = "() => { const f = document.querySelector('#viewer iframe'); return !!(f && f.contentDocument.querySelector('p')); }"
DUMP = "() => window.plTurnLog.dump()"
STORED = "() => sessionStorage.getItem('pluteus-turnlog-lines')"


def open_book(page):
    page.goto(page.site + "/read/lib/Long.epub?noshell=1")
    page.wait_for_function(BOOK_SHOWN)
    page.wait_for_timeout(500)


def switch_on(page):
    page.click("#settings-btn")
    page.check("#turnlog")
    page.click("#settings-btn")                                              # closes the menu again
    page.wait_for_selector(".pl-turnlog-btn")


def settled(page):
    page.wait_for_function("() => !document.querySelector('#reader.pl-turning')", timeout=15000)
    page.wait_for_timeout(300)


def test_the_log_is_off_by_default_and_records_nothing(page):
    open_book(page)
    page.keyboard.press("ArrowRight")
    settled(page)
    assert page.evaluate("() => window.plTurnLog.on()") is False
    assert page.evaluate(DUMP) == ""
    assert page.locator(".pl-turnlog-btn").count() == 0
    assert page.evaluate(STORED) is None
    assert page.evaluate("() => localStorage.getItem('pluteus-turnlog')") is None


def test_switched_on_it_records_taps_turns_and_where_the_page_was_scrolled(page):
    open_book(page)
    switch_on(page)
    page.keyboard.press("ArrowRight")
    settled(page)
    log = page.evaluate(DUMP)
    assert "env  ua=" in log and "dpr=" in log and "inner=" in log                      # the facts about the screen
    assert re.search(r"keydown  in=page.*key=ArrowRight", log)                          # the raw key
    assert re.search(r"turn  fwd=1 .*scroll=\d", log)                                  # the turn, with the scroll numbers
    assert "turn done  ms=" in log and "relocated  sec=" in log
    page.wait_for_function("() => !!sessionStorage.getItem('pluteus-turnlog-lines')", timeout=5000)     # kept for a reload of this tab


def test_one_tap_is_one_turn_and_the_log_says_how_it_was_decided(page):
    open_book(page)
    switch_on(page)
    page.evaluate("() => window.plTurnLog.clear()")
    tap(page, 0.7)
    settled(page)
    log = page.evaluate(DUMP)
    assert len(re.findall(r"  turn  fwd=", log)) == 1, log                           # one physical tap, one turn
    assert len(re.findall(r"tap decided  page forward", log)) == 1, log
    assert re.search(r"pointerdown  in=(page|book)", log) and re.search(r"click  in=(page|book)", log)
    assert "DROPPED" not in log


def test_a_turn_asked_for_while_one_runs_is_logged_as_dropped(page):
    open_book(page)
    switch_on(page)
    page.evaluate("() => window.plTurnLog.clear()")
    page.evaluate("() => { document.getElementById('next').click(); document.getElementById('next').click(); }")      # two at once
    settled(page)
    log = page.evaluate(DUMP)
    assert len(re.findall(r"  turn  fwd=", log)) == 1 and "turn DROPPED" in log, log


def test_mark_copy_clear_and_the_log_holds_no_book_text(page):
    open_book(page)
    switch_on(page)
    page.keyboard.press("ArrowRight")
    settled(page)
    page.click(".pl-turnlog-btn")
    page.click(".pl-turnlog .pl-btn:text('Mark')")
    assert "MARK  sec=" in page.evaluate(DUMP)
    page.click(".pl-turnlog .pl-btn:text('Copy')")
    assert page.locator(".pl-turnlog-text").input_value().count("\n") > 3
    assert "Words that fill" not in page.evaluate(DUMP) and "Chapter" not in page.evaluate(DUMP)    # the book's own words
    page.click(".pl-turnlog .pl-btn:text('Clear')")
    assert page.evaluate(DUMP) == ""


def test_switched_off_again_it_leaves_nothing_behind(page):
    open_book(page)
    switch_on(page)
    page.keyboard.press("ArrowRight")
    settled(page)
    page.click("#settings-btn")
    page.uncheck("#turnlog")
    assert page.locator(".pl-turnlog-btn").count() == 0
    assert page.evaluate(DUMP) == "" and page.evaluate(STORED) is None
    page.reload()
    page.wait_for_function(BOOK_SHOWN)
    assert page.evaluate("() => window.plTurnLog.on()") is False                        # and it stays off after a reload


def test_the_choice_and_the_lines_survive_a_reload_of_the_tab(page):
    open_book(page)
    switch_on(page)
    page.keyboard.press("ArrowRight")
    settled(page)
    page.reload()
    page.wait_for_function(BOOK_SHOWN)
    page.wait_for_selector(".pl-turnlog-btn")
    log = page.evaluate(DUMP)
    assert "  turn  fwd=1" in log and "page loaded" in log

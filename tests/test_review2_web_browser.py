"""Code review of 2026-11-12, the readers, the players and the shell in a real browser (cards 38 to 44 and 46):
a failed progress fetch does not wipe the saved place, data-confirm really asks, Next on a song page does not hand the old song to the
player bar, Alt/Ctrl keys stay the browser's, a pinch is no swipe, the shell forgets the pages it has left, and the page-count cache of
the ebook reader is bounded. Needs Playwright and Google Chrome; skipped where one is missing."""
import json
import math
import socket
import struct
import threading
import time
import wave
from urllib.parse import unquote

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

from conftest import FAST                                                  # noqa: E402
from test_login_web import make                                            # noqa: E402
from test_tap_zones_browser import PHONE, build_library                    # noqa: E402

from pluteus.auth import hash_password                                     # noqa: E402
from pluteus.config import UPLOADS_ROOT, parse_config                      # noqa: E402

BOOK_SHOWN = "() => { const f = document.querySelector('#viewer iframe'); return !!(f && f.contentDocument.querySelector('p')); }"
PCT = "() => document.getElementById('pct').textContent"


def quiet_song(path, seconds=20):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", int(300 * math.sin(i / 6.0))) for i in range(8000 * seconds)))


@pytest.fixture
def site(tmp_path):
    build_library(tmp_path)                                                # Long.epub, Comic.cbz, Paper.pdf, pics/1-3.png
    album = tmp_path / "lib" / "album"
    album.mkdir()
    for name in ("01 one.wav", "02 two.wav", "03 three.wav"):
        quiet_song(album / name)
    mine = tmp_path / "uploads" / "alice"
    mine.mkdir(parents=True)
    (mine / "keep.txt").write_text("a file of mine\n", encoding="utf-8")
    cfg = parse_config({"server": {"show_all_files": True}, "roots": [{"name": "lib", "path": str(tmp_path / "lib")}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw-alice", **FAST), "roots": ["*"]}]})
    _, app = make(cfg, tmp_path, uploads_dir=str(tmp_path / "uploads"))
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
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True, args=["--mute-audio"])
        except Exception as e:                                             # no Google Chrome on this machine
            pytest.skip("Google Chrome is needed: %s" % e)
        yield browser
        browser.close()


def sign_in(browser, site, **context):
    page = browser.new_context(viewport=PHONE, **context).new_page()
    page.goto(site + "/login")
    page.fill("input[name=username]", "alice")
    page.fill("input[name=password]", "pw-alice")
    page.click("button[type=submit]")
    page.wait_for_load_state()
    page.site = site
    return page


@pytest.fixture
def page(browser, site):
    return sign_in(browser, site)


@pytest.fixture
def touch_page(browser, site):
    return sign_in(browser, site, has_touch=True)


def settled(page):
    """Everything the page has asked of the server so far has been answered (a request made before this one is seen before it)."""
    page.evaluate("() => fetch(document.querySelector('link[rel=stylesheet]').href, {cache: 'no-store'}).then(r => r.status)")


# ---- card 39: data-confirm really asks -------------------------------------------------------------------------------------------------
def answer(page, accept):
    seen = []

    def on_dialog(dialog):
        seen.append(dialog.message)
        if accept:
            dialog.accept()
        else:
            dialog.dismiss()
    page.once("dialog", on_dialog)
    return seen


def test_delete_in_the_upload_folder_asks_once_and_no_means_no(page, tmp_path):
    kept = tmp_path / "uploads" / "alice" / "keep.txt"
    page.goto(page.site + "/browse/%s?noshell=1" % UPLOADS_ROOT)
    asked = answer(page, False)
    page.click("form[data-confirm] button")
    settled(page)
    assert asked == ["Delete keep.txt?"] and kept.exists()
    asked = answer(page, True)
    with page.expect_navigation():
        page.click("form[data-confirm] button")
    assert asked == ["Delete keep.txt?"] and not kept.exists()            # asked once (not once by each script), then done


def test_remove_in_the_note_editor_asks_before_the_note_goes(page, tmp_path):
    note = tmp_path / "uploads" / "alice" / "Notes" / "lib" / "pics" / "about.md"
    note.parent.mkdir(parents=True)
    note.write_text("My note about the pictures.\n", encoding="utf-8")
    page.goto(page.site + "/note/lib/pics?name=about.md&noshell=1")
    assert "My note about the pictures." in page.input_value("textarea[name=text]")
    asked = answer(page, False)
    page.click("button[value=remove]")                                     # a button with data-confirm: nothing asked before this fix
    settled(page)
    assert len(asked) == 1 and asked[0].startswith("Remove your note?") and note.exists()
    assert "/note/lib/pics" in page.url                                    # still in the editor
    answer(page, True)
    with page.expect_navigation():
        page.click("button[value=remove]")
    assert not note.exists() and "ok=noteremoved" in unquote(page.url)


# ---- card 38: a failed progress fetch does not wipe the saved place -------------------------------------------------------------------------
def failing_progress(page, status=500):
    """The server's answer to "where did I stop" fails; what the reader then saves is collected."""
    saved = []

    def handle(route):
        if route.request.method == "GET":
            if status:
                route.fulfill(status=status, content_type="application/json", body="{}")
            else:
                route.abort()                                              # no connection
        else:
            saved.append(json.loads(route.request.post_data))
            route.fulfill(status=200, content_type="application/json", body="{}")
    page.route("**/api/progress/**", handle)
    return saved


def leave(page):
    """What the browser tells the page when it is closed or put away: the reader sends a save it still had waiting."""
    page.evaluate("() => window.dispatchEvent(new Event('pagehide'))")
    settled(page)


@pytest.mark.parametrize("status", [500, 0])
def test_comic_a_failed_progress_fetch_saves_nothing_until_a_page_is_turned(page, status):
    saved = failing_progress(page, status)
    page.goto(page.site + "/read/lib/Comic.cbz?noshell=1")
    page.wait_for_function("() => document.getElementById('pct').textContent === '1 / 3'")
    leave(page)
    assert saved == []                                                     # page 1 was not written over the real place
    page.keyboard.press("ArrowRight")
    leave(page)
    assert [s["locator"] for s in saved] == ["page:1"]                     # the reader's own page turn is saved as before


def test_pdf_a_failed_progress_fetch_saves_nothing_until_a_page_is_turned(page):
    saved = failing_progress(page)
    page.goto(page.site + "/view/lib/Paper.pdf?noshell=1")
    page.wait_for_function("() => document.getElementById('pct').textContent === '1 / 3'")
    leave(page)
    assert saved == []
    page.keyboard.press("ArrowRight")
    page.wait_for_function("() => document.getElementById('pct').textContent === '2 / 3'")
    leave(page)
    assert [s["locator"] for s in saved] == ["page:1"]


def test_epub_a_failed_progress_fetch_saves_nothing_until_a_page_is_turned(page):
    saved = failing_progress(page)
    page.goto(page.site + "/read/lib/Long.epub?noshell=1")
    page.wait_for_function(BOOK_SHOWN)
    # the pages have been counted: by now the reader has had every reason it has to save on its own
    page.wait_for_function("() => Object.keys(localStorage).some(k => k.startsWith('pluteus-loc:'))", timeout=20000)
    leave(page)
    assert saved == []
    before = page.evaluate(PCT)
    page.keyboard.press("ArrowRight")
    page.wait_for_function("b => document.getElementById('pct').textContent !== b", arg=before)      # the reader knows its new place
    leave(page)
    assert len(saved) == 1 and saved[0]["locator"].startswith("epubcfi(")


def test_no_saved_place_yet_is_an_answer_and_the_reader_saves_as_before(page):
    saved = failing_progress(page, 404)
    page.goto(page.site + "/read/lib/Comic.cbz?noshell=1")
    page.wait_for_function("() => document.getElementById('pct').textContent === '1 / 3'")
    leave(page)
    assert [s["locator"] for s in saved] == ["page:0"]


# ---- card 41: Alt, Ctrl and the Windows / Command key stay the browser's -----------------------------------------------------------------------
def test_comic_keys_with_alt_or_ctrl_do_nothing_in_the_reader(page):
    page.goto(page.site + "/read/lib/Comic.cbz?noshell=1")
    page.wait_for_function("() => document.getElementById('pct').textContent === '1 / 3'")
    zoomed = "() => document.getElementById('stage').classList.contains('pl-fit-zoom')"
    for chord in ("Alt+ArrowRight", "Control+ArrowRight", "Meta+ArrowRight", "Control+Equal", "Control+End"):
        page.keyboard.press(chord)
        assert page.evaluate(PCT) == "1 / 3" and not page.evaluate(zoomed), chord
    page.keyboard.press("ArrowRight")                                      # the plain key still turns
    assert page.evaluate(PCT) == "2 / 3"


def test_pdf_keys_with_alt_or_ctrl_do_nothing_in_the_viewer(page):
    page.goto(page.site + "/view/lib/Paper.pdf?noshell=1")
    page.wait_for_function("() => document.getElementById('pct').textContent === '1 / 3'")
    for chord in ("Alt+ArrowRight", "Control+ArrowRight", "Control+End"):
        page.keyboard.press(chord)
        assert page.evaluate(PCT) == "1 / 3", chord
    page.keyboard.press("ArrowRight")
    assert page.evaluate(PCT) == "2 / 3"


def test_epub_keys_with_alt_or_ctrl_turn_no_page(page):
    page.goto(page.site + "/read/lib/Long.epub?noshell=1")
    page.wait_for_function(BOOK_SHOWN)
    turning = "() => document.getElementById('reader').classList.contains('pl-turning')"
    for chord in ("Alt+ArrowRight", "Control+ArrowRight"):
        page.keyboard.press(chord)
        assert not page.evaluate(turning) and page.evaluate("() => document.querySelector('.epub-container').scrollLeft") == 0, chord
    page.keyboard.press("ArrowRight")
    page.wait_for_function("() => document.querySelector('.epub-container').scrollLeft > 0")


def test_picture_keys_with_alt_open_no_other_picture(page):
    page.goto(page.site + "/view/lib/pics/2.png?noshell=1")
    page.wait_for_selector("#page")
    page.evaluate("() => { window.__same = true; }")                       # lost if another picture's page opens
    page.keyboard.press("Alt+ArrowRight")
    settled(page)
    assert page.evaluate("() => window.__same === true") and "pics/2.png" in unquote(page.url)
    page.keyboard.press("ArrowRight")
    page.wait_for_url(lambda url: "pics/3.png" in unquote(url))


# ---- card 42: a pinch, or a finger on a zoomed page, is no swipe ---------------------------------------------------------------------------
TOUCH = """(steps) => {
  const el = document.querySelector(steps.on), down = {};
  const mk = (id, x, y) => new Touch({identifier: id, target: el, clientX: x, clientY: y, screenX: x, screenY: y});
  for (const [type, id, x, y] of steps.do) {
    const t = mk(id, x, y);
    if (type === 'touchstart') down[id] = t; else delete down[id];
    const touches = Object.values(down);
    el.dispatchEvent(new TouchEvent(type, {touches, targetTouches: touches, changedTouches: [t], bubbles: true, cancelable: true}));
  }
}"""
SWIPE = [["touchstart", 1, 300, 400], ["touchend", 1, 80, 405]]                          # one finger, right to left: forward
# two fingers that close in on each other; the old code measured from where the SECOND finger came down to where the first one lifted
PINCH = [["touchstart", 1, 60, 400], ["touchstart", 2, 320, 400], ["touchend", 1, 160, 402], ["touchend", 2, 220, 400]]


def gesture(where, steps, on):
    where.evaluate(TOUCH, {"on": on, "do": steps})


def page_zoom(page, scale):
    """The browser's own pinch zoom, as the page sees it (visualViewport.scale)."""
    page.context.new_cdp_session(page).send("Emulation.setPageScaleFactor", {"pageScaleFactor": scale})
    page.wait_for_function("s => Math.abs(window.visualViewport.scale - s) < 0.01", arg=scale)


def test_comic_a_pinch_or_a_zoomed_page_turns_nothing_and_a_swipe_still_does(touch_page):
    page = touch_page
    page.goto(page.site + "/read/lib/Comic.cbz?noshell=1")
    page.wait_for_function("() => document.getElementById('pct').textContent === '1 / 3'")
    gesture(page, PINCH, "#stage")
    assert page.evaluate(PCT) == "1 / 3"
    page_zoom(page, 2)
    gesture(page, SWIPE, "#stage")                                         # the finger is moving the zoomed page
    assert page.evaluate(PCT) == "1 / 3"
    page_zoom(page, 1)
    gesture(page, SWIPE, "#stage")
    assert page.evaluate(PCT) == "2 / 3"


def test_pdf_a_pinch_turns_nothing_and_a_swipe_still_does(touch_page):
    page = touch_page
    page.goto(page.site + "/view/lib/Paper.pdf?noshell=1")
    page.wait_for_function("() => document.getElementById('pct').textContent === '1 / 3'")
    gesture(page, PINCH, ".pl-reader-stage")
    assert page.evaluate(PCT) == "1 / 3"
    gesture(page, SWIPE, ".pl-reader-stage")
    assert page.evaluate(PCT) == "2 / 3"


def test_picture_a_pinch_opens_no_other_picture_and_a_swipe_still_does(touch_page):
    page = touch_page
    page.goto(page.site + "/view/lib/pics/2.png?noshell=1")
    page.wait_for_selector("#page")
    page.evaluate("() => { window.__same = true; }")
    gesture(page, PINCH, ".pl-reader-stage")
    settled(page)
    assert page.evaluate("() => window.__same === true") and "pics/2.png" in unquote(page.url)
    gesture(page, SWIPE, ".pl-reader-stage")
    page.wait_for_url(lambda url: "pics/3.png" in unquote(url))


def test_epub_a_pinch_turns_nothing_and_a_swipe_still_does(touch_page):
    page = touch_page
    page.goto(page.site + "/read/lib/Long.epub?noshell=1")
    page.wait_for_function(BOOK_SHOWN)
    book = page.query_selector("#viewer iframe").content_frame()           # the book's text is a frame of its own
    left = "() => document.querySelector('.epub-container').scrollLeft"
    turning = "() => document.getElementById('reader').classList.contains('pl-turning')"
    gesture(book, PINCH, "body")
    assert not page.evaluate(turning) and page.evaluate(left) == 0
    gesture(book, SWIPE, "body")
    page.wait_for_function("() => document.querySelector('.epub-container').scrollLeft > 0")


# ---- cards 43 and 40: the shell ------------------------------------------------------------------------------------------------------------
def in_shell(page, path):
    page.goto(page.site + path)                                            # a signed-in page moves into the shell
    page.wait_for_selector("#shell-frame")
    return frame_at(page, path)


def frame_at(page, path):
    page.wait_for_function("p => { try { const l = document.getElementById('shell-frame').contentWindow.location;"
                           " return decodeURIComponent(l.pathname).endsWith(p) && document.getElementById('shell-frame').contentDocument.readyState === 'complete'; }"
                           " catch (e) { return false; } }", arg=unquote(path))
    return page.frame(name="shell-frame")


def test_the_shell_forgets_what_a_page_registered_once_the_frame_shows_another(page):
    frame = in_shell(page, "/browse/lib")
    page.evaluate("() => { window.__calls = []; }")
    for kind in ("onState", "fullscreen.onChange"):                        # both lists: the player's and full screen's
        frame.evaluate("k => { const s = window.parent.plShell, add = k === 'onState' ? s.onState : s.fullscreen.onChange;"
                       " add(() => window.parent.__calls.push(k)); }", kind)
    page.evaluate("() => window.plShell.stop()")                           # a change of the player: the page in the frame is told
    assert page.evaluate("() => window.__calls") == ["onState"]
    for step in ("/browse/lib/pics", "/browse/lib/album", "/browse/lib"):
        frame.goto(page.site + step)
        frame = frame_at(page, step)
    page.evaluate("() => { window.__calls = []; window.plShell.stop(); window.plShell.fullscreen.setMode('max');"
                  " document.dispatchEvent(new Event('fullscreenchange')); }")
    assert page.evaluate("() => window.__calls") == []                     # the pages that are gone are no longer called (nor kept)
    frame.evaluate("() => window.parent.plShell.onState(() => window.parent.__calls.push('new'))")
    page.evaluate("() => window.plShell.stop()")
    assert page.evaluate("() => window.__calls") == ["new"]                # the page shown now still is


PLAYING = "() => { const m = document.getElementById('media'); return !!m && !m.paused && m.currentTime > 0.2; }"


def test_next_on_a_song_page_does_not_hand_the_old_song_to_the_player_bar(page):
    frame = in_shell(page, "/view/lib/album/01%20one.wav")
    frame.wait_for_selector("#play")
    frame.click("#play")
    frame.wait_for_function(PLAYING)
    frame.evaluate("() => document.getElementById('next-song').click()")
    frame = frame_at(page, "/view/lib/album/02 two.wav")
    frame.wait_for_function(PLAYING)                                       # the next song plays in its own page ...
    assert page.evaluate("() => window.plShell.active()") is False         # ... and the bar did not take the first one
    assert page.evaluate("() => document.getElementById('sp').hidden") is True


def test_leaving_a_playing_song_for_another_page_still_carries_it_on_in_the_bar(page):
    frame = in_shell(page, "/view/lib/album/01%20one.wav")
    frame.wait_for_selector("#play")
    frame.click("#play")
    frame.wait_for_function(PLAYING)
    frame.wait_for_function("() => performance.getEntriesByType('resource').some(e => e.name.includes('/queue'))")   # the folder's list is in
    frame.goto(page.site + "/browse/lib/album")
    frame_at(page, "/browse/lib/album")
    page.wait_for_function("() => window.plShell.active() && decodeURIComponent(window.plShell.currentUrl()).includes('01 one.wav')")


# ---- card 46: the ebook page-count cache is bounded -------------------------------------------------------------------------------------------
def test_the_page_count_cache_keeps_the_newest_thirty(page):
    page.goto(page.site + "/login?noshell=1")
    page.evaluate("""() => { const index = {};
        for (let i = 0; i < 25; i++) { localStorage.setItem('pluteus-loc:new-' + i, 'x'.repeat(100)); index['pluteus-loc:new-' + i] = 1000 + i; }
        for (let i = 0; i < 15; i++) localStorage.setItem('pluteus-loc:old-' + i, 'x'.repeat(100));        // from before there was an index
        index['pluteus-loc:gone'] = 5;                                                                    // its count is no longer there
        localStorage.setItem('pluteus-loc-index', JSON.stringify(index));
        localStorage.setItem('pluteus-reader', '{"size": 110, "v": 2}'); }""")
    page.goto(page.site + "/read/lib/Long.epub?noshell=1")
    page.wait_for_function(BOOK_SHOWN)
    page.wait_for_function("() => { try { return Object.values(JSON.parse(localStorage.getItem('pluteus-loc-index'))).some(t => t > 1e12); }"
                           " catch (e) { return false; } }", timeout=20000)                               # this book's count is stored and stamped
    keys = page.evaluate("() => Object.keys(localStorage).filter(k => k.startsWith('pluteus-loc:'))")
    index = page.evaluate("() => JSON.parse(localStorage.getItem('pluteus-loc-index'))")
    assert len(keys) == 30 and sorted(keys) == sorted(index)
    mine = [k for k in keys if "new-" not in k and "old-" not in k]
    assert len(mine) == 1                                                  # this book's own count
    assert sum("new-" in k for k in keys) == 25 and sum("old-" in k for k in keys) == 4      # the unstamped old ones went first
    assert page.evaluate("() => JSON.parse(localStorage.getItem('pluteus-reader')).size") == 110            # other settings are left alone
    page.reload()                                                          # opened again: the count is read from the cache and stays
    page.wait_for_function(BOOK_SHOWN)
    stamp = index[mine[0]]
    page.wait_for_function("a => JSON.parse(localStorage.getItem('pluteus-loc-index'))[a[0]] > a[1]", arg=[mine[0], stamp], timeout=20000)
    assert len(page.evaluate("() => Object.keys(localStorage).filter(k => k.startsWith('pluteus-loc:'))")) == 30


def test_a_damaged_index_or_a_full_store_does_not_stop_the_reader(page):
    page.goto(page.site + "/login?noshell=1")
    page.evaluate("() => localStorage.setItem('pluteus-loc-index', '[not json')")
    page.goto(page.site + "/read/lib/Long.epub?noshell=1")
    page.wait_for_function(BOOK_SHOWN)
    page.wait_for_function("() => { try { return Object.keys(JSON.parse(localStorage.getItem('pluteus-loc-index'))).length === 1; }"
                           " catch (e) { return false; } }", timeout=20000)
    page.keyboard.press("ArrowRight")                                      # and the book reads on
    page.wait_for_function("() => document.querySelector('.epub-container').scrollLeft > 0")

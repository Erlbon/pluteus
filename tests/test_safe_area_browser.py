"""Chrome on Android 15 draws the page under the system's button bar (edge to edge): the reader's bottom bar and the last line of the
page were hidden behind it. The page now keeps clear of what the system covers (viewport-fit=cover and the safe-area insets), the shell
page once, a page in its frame not again. Chrome's own override of the insets stands in for the phone's button bar.
Needs Playwright and Google Chrome; skipped where one is missing."""
import pytest

from test_tap_zones_browser import playwright_api, site                    # noqa: F401  (site is a fixture)

PHONE = {"width": 393, "height": 760}
BUTTON_BAR = 46                                                            # Android's three-button bar on the owner's phone, in CSS pixels


@pytest.fixture
def phone(site):                                                           # noqa: F811
    with playwright_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True, args=["--mute-audio"])
        except Exception as e:                                             # no Google Chrome on this machine
            pytest.skip("Google Chrome is needed: %s" % e)
        context = browser.new_context(viewport=PHONE, is_mobile=True, has_touch=True)
        page = context.new_page()
        try:
            context.new_cdp_session(page).send("Emulation.setSafeAreaInsetsOverride", {"insets": {"bottom": BUTTON_BAR}})
        except Exception as e:                                             # an older Chrome
            pytest.skip("this Chrome cannot fake the system's insets: %s" % e)
        page.goto(site + "/login")
        page.fill("input[name=username]", "alice")
        page.fill("input[name=password]", "pw-alice")
        page.click("button[type=submit]")
        page.wait_for_load_state()
        page.site = site
        yield page
        browser.close()


def reader_frame(page):
    page.wait_for_function("() => { const f = document.getElementById('shell-frame'); return f && f.contentDocument &&"
                           " f.contentDocument.querySelector('#viewer iframe'); }")
    return [f for f in page.frames if "/read/" in f.url][0]


def assert_foot_just_above_the_button_bar(foot, zoom=1.0):
    bottom = foot["y"] + foot["height"]
    assert bottom <= PHONE["height"] - BUTTON_BAR + 0.5                     # not under the button bar ...
    assert bottom >= PHONE["height"] - BUTTON_BAR * zoom - 0.5              # ... and not lifted twice (shell and frame); zoom scales the inset


def test_the_reader_in_the_shell_keeps_its_bottom_bar_above_the_button_bar(phone):
    phone.goto(phone.site + "/shell?go=/read/lib/Long.epub")
    frame = reader_frame(phone)
    assert_foot_just_above_the_button_bar(frame.locator(".pl-reader-foot").bounding_box())
    stage = frame.locator(".pl-reader-stage").bounding_box()
    assert stage["y"] + stage["height"] <= PHONE["height"] - BUTTON_BAR     # the page's last line is not under it either


ZOOM = "() => { document.documentElement.style.zoom = '1.1'; }"                  # Chrome's page zoom (Accessibility) at 110%


@pytest.mark.parametrize("shell", [True, False])
def test_with_page_zoom_the_bottom_bar_stays_on_the_screen(phone, shell):
    """The reader was given the screen's height (100dvh), which page zoom makes 10% taller than the screen: the bottom bar went below
    its edge. It is now pinned to the screen's edges. In the shell the stand-in (CSS zoom) cannot zoom the frame's page the way Chrome's
    page zoom does (a zoomed page does not scale the page in its frame), so two things are checked: the shell's frame ends just above the
    button bar, and the reader fills its frame to the bottom. Zooming both pages, as before, only passed while the shell was too tall."""
    if shell:
        phone.goto(phone.site + "/shell?go=/read/lib/Long.epub")
        frame = reader_frame(phone)
        phone.evaluate(ZOOM)
        phone.wait_for_timeout(500)
        assert_foot_just_above_the_button_bar(phone.locator("#shell-frame").bounding_box(), zoom=1.1)
        foot_bottom, frame_height = frame.evaluate("() => [document.querySelector('.pl-reader-foot').getBoundingClientRect().bottom,"
                                                   " innerHeight]")
        assert abs(foot_bottom - frame_height) <= 1
        return
    phone.goto(phone.site + "/read/lib/Long.epub?noshell=1")
    phone.wait_for_selector("#viewer iframe")
    phone.evaluate(ZOOM)
    phone.wait_for_timeout(500)
    assert_foot_just_above_the_button_bar(phone.locator(".pl-reader-foot").bounding_box(), zoom=1.1)


ADDRESS_BAR = 56                                                           # Chrome's address bar on the owner's phone, in CSS pixels
# On a phone 100vh is the screen with the address bar slid away, 56 px taller than what shows while it is there; the page's own rule
# (body: min-height 100vh) is made that tall here, as Chrome on Android makes it
LARGE_VIEWPORT = ("px => { const s = document.createElement('style'); s.textContent = 'body { min-height: calc(100vh + ' + px + 'px); }';"
                  " document.head.appendChild(s); }")


def test_the_shell_is_no_taller_than_the_screen_with_the_address_bar_showing(phone):
    """0.9.24 pinned the shell to the screen's edges, but the page's min-height (100vh) won: on the phone the shell came out 56 px taller
    than the screen, and the reader's bottom bar was below its edge."""
    phone.goto(phone.site + "/shell?go=/read/lib/Long.epub")
    frame = reader_frame(phone)
    phone.evaluate(LARGE_VIEWPORT, ADDRESS_BAR)
    phone.wait_for_timeout(500)
    assert phone.evaluate("document.body.getBoundingClientRect().height") <= PHONE["height"] + 0.5
    assert_foot_just_above_the_button_bar(frame.locator(".pl-reader-foot").bounding_box())


def test_the_reader_on_its_own_keeps_its_bottom_bar_above_the_button_bar(phone):
    phone.goto(phone.site + "/read/lib/Long.epub?noshell=1")
    phone.wait_for_selector("#viewer iframe")
    assert_foot_just_above_the_button_bar(phone.locator(".pl-reader-foot").bounding_box())
    assert phone.evaluate("document.documentElement.scrollHeight") <= PHONE["height"]   # no page scroll from the padding

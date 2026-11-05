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


def assert_foot_just_above_the_button_bar(foot):
    bottom = foot["y"] + foot["height"]
    assert bottom <= PHONE["height"] - BUTTON_BAR + 0.5                     # not under the button bar ...
    assert bottom >= PHONE["height"] - BUTTON_BAR - 0.5                     # ... and not lifted twice (shell and frame)


def test_the_reader_in_the_shell_keeps_its_bottom_bar_above_the_button_bar(phone):
    phone.goto(phone.site + "/shell?go=/read/lib/Long.epub")
    frame = reader_frame(phone)
    assert_foot_just_above_the_button_bar(frame.locator(".pl-reader-foot").bounding_box())
    stage = frame.locator(".pl-reader-stage").bounding_box()
    assert stage["y"] + stage["height"] <= PHONE["height"] - BUTTON_BAR     # the page's last line is not under it either


def test_the_reader_on_its_own_keeps_its_bottom_bar_above_the_button_bar(phone):
    phone.goto(phone.site + "/read/lib/Long.epub?noshell=1")
    phone.wait_for_selector("#viewer iframe")
    assert_foot_just_above_the_button_bar(phone.locator(".pl-reader-foot").bounding_box())
    assert phone.evaluate("document.documentElement.scrollHeight") <= PHONE["height"]   # no page scroll from the padding

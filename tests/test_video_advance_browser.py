"""Videos in a folder play on by themselves in a real browser that wants a press before a video may play (Android's WebView: TV Bro on a
TV; Chrome's --autoplay-policy=user-gesture-required is the same rule). One press on the first file must carry the whole folder.
Needs Playwright, Google Chrome and ffmpeg (to make the videos); skipped where one is missing."""
import shutil
import socket
import subprocess
import threading
import time

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

from conftest import FAST                                                  # noqa: E402
from test_login_web import make                                            # noqa: E402

from pluteus.auth import hash_password                                     # noqa: E402
from pluteus.config import parse_config                                    # noqa: E402

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(not FFMPEG, reason="ffmpeg is needed to make the test videos")
MEDIA = "() => { const m = document.getElementById('media'); return {src: m.currentSrc, t: m.currentTime, paused: m.paused, muted: m.muted}; }"


@pytest.fixture
def site(tmp_path):
    folder = tmp_path / "lib" / "videos"
    folder.mkdir(parents=True)
    for name in ("a", "b", "c"):
        subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=25:duration=3",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", "-movflags", "+faststart", str(folder / (name + ".mp4"))], check=True)
    cfg = parse_config({"roots": [{"name": "lib", "path": str(tmp_path / "lib")}],
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
def frame_of(site):
    with playwright_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True,
                                        args=["--autoplay-policy=user-gesture-required", "--mute-audio"])
        except Exception as e:                                             # no Google Chrome on this machine
            pytest.skip("Google Chrome is needed: %s" % e)
        page = browser.new_page()
        page.goto(site + "/login")
        page.fill("input[name=username]", "alice")
        page.fill("input[name=password]", "pw-alice")
        page.click("button[type=submit]")
        page.wait_for_load_state()

        def open_in_shell(path):
            page.goto(site + path)                                         # a signed-in page moves into the shell, as on the TV
            page.wait_for_selector("#shell-frame")
            for _ in range(100):
                frame = page.frame(name="shell-frame")
                if frame and frame.url.endswith(path):
                    frame.wait_for_selector("#play")
                    return frame
                time.sleep(0.05)
            raise AssertionError("the file did not open in the shell's frame")
        yield open_in_shell
        browser.close()


def playing(frame, name, timeout=12.0):
    """Wait until the frame's player plays `name` by itself (no press); return its state, or the last state seen."""
    deadline, st = time.time() + timeout, None
    while time.time() < deadline:
        try:
            st = frame.evaluate(MEDIA)
        except Exception:                                                  # the frame is between pages
            st = None
        if st and st["src"].endswith(name) and not st["paused"] and st["t"] > 0.3:
            return st
        time.sleep(0.1)
    return st


def test_one_press_plays_the_whole_folder_of_videos(frame_of):
    frame = frame_of("/view/lib/videos/a.mp4")
    frame.click("#play")                                                   # the one press
    assert playing(frame, "a.mp4")["muted"] is False
    frame.click("#fs")                                                     # full screen (in the shell: the frame) must last the whole folder
    page = frame.page
    page.wait_for_function("() => !!document.fullscreenElement", timeout=5000)
    for name, at in (("b.mp4", "2 / 3"), ("c.mp4", "3 / 3")):
        frame.evaluate("() => { const m = document.getElementById('media'); m.currentTime = Math.max(0, m.duration - 0.4); }")
        st = playing(frame, name)
        assert st and st["src"].endswith(name) and not st["paused"], "%s did not play on by itself: %r" % (name, st)
        assert frame.url.endswith("/view/lib/videos/" + name)                         # the address follows, so a reload opens this file
        assert frame.inner_text(".pl-track-title") == name[0] and at in frame.inner_text(".pl-reader-foot")
        assert name[0] in frame.evaluate("() => document.title")
        assert page.evaluate("() => !!document.fullscreenElement"), "full screen ended at " + name
    assert frame.evaluate("() => document.getElementById('next-song').disabled")         # c is the last file


def test_the_next_and_previous_buttons_keep_playing_too(frame_of):
    """The buttons go through the same in-page change. (Chrome counts Playwright's script as a press, so unlike the test above this one
    passed with the old page-per-file code as well: it guards that the buttons still work, not the press rule.)"""
    frame = frame_of("/view/lib/videos/a.mp4")
    frame.click("#play")
    assert playing(frame, "a.mp4")
    frame.evaluate("() => document.getElementById('next-song').click()")
    assert playing(frame, "b.mp4"), "the Next button did not play b"
    frame.evaluate("() => document.getElementById('prev-song').click()")    # right after the start: goes back a file
    assert playing(frame, "a.mp4"), "the Previous button did not play a"

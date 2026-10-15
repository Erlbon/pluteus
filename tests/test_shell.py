"""The shell: the outer page that holds the player and shows the site in a frame; the queue of a folder for it."""
import pytest
from starlette.testclient import TestClient

from conftest import basic
from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import parse_config

HTML = {"Accept": "text/html"}


@pytest.fixture
def client(tmp_path):
    music = tmp_path / "music"
    (music / "Artist" / "Album One").mkdir(parents=True)
    (music / "Artist" / "Album Two").mkdir()
    (music / "Artist" / "Album One" / "02 b.mp3").write_bytes(b"ID3" + b"\x00" * 50)
    (music / "Artist" / "Album One" / "01 a.mp3").write_bytes(b"ID3" + b"\x00" * 50)
    (music / "Artist" / "Album One" / "cover.jpg").write_bytes(b"x")
    (music / "Artist" / "Album Two" / "01 c.flac").write_bytes(b"fLaC" + b"\x00" * 50)
    (music / "Artist" / "single.mp3").write_bytes(b"ID3" + b"\x00" * 50)
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "x.mp3").write_bytes(b"ID3")
    cfg = parse_config({"roots": [{"name": "music", "path": str(music)}, {"name": "other", "path": str(tmp_path / "other")}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]},
                                  {"name": "bob", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["music"]}]})
    return TestClient(create_app(cfg)), basic("alice", "pw"), basic("bob", "pw")


def test_the_shell_frames_the_site_and_never_sends_itself_into_a_shell(client):
    c, alice, _ = client
    page = c.get("/shell", headers={**alice, **HTML}).text
    assert "<iframe class='pl-shell-frame' id='shell-frame'" in page and "src='/'" in page
    assert "shell.js" in page and "id='sp-play'" in page
    csp = c.get("/shell", headers={**alice, **HTML}).headers["content-security-policy"]
    assert "media-src 'self' blob: http: https:" in csp and "connect-src 'self' http: https:" in csp    # radio streams and the sound of a TV channel
    assert "data-hls='/static/vendor/hls.min.js'" in page and "id='sp-link'" in page                     # hls.js is loaded by the shell only when needed
    assert "data-shell" not in page                                              # or it would redirect to itself forever
    assert "name='shell-frame'" in page and "href='/settings' target='shell-frame'" in page and "class='pl-brand' href='/' target='shell-frame'" in page
    assert "target='shell-frame'" not in c.get("/settings", headers={**alice, **HTML}).text      # an ordinary page keeps plain links
    assert c.get("/shell").status_code == 401


def test_the_frame_starts_where_asked_but_only_on_this_site(client):
    c, alice, _ = client
    start = lambda go: c.get("/shell", params={"go": go}, headers={**alice, **HTML}).text      # noqa: E731
    assert "src='/browse/music/Artist'" in start("/browse/music/Artist")
    for bad in ("https://evil.example/", "//evil.example/", "javascript:alert(1)", "/shell?go=/", "relative/path"):
        assert "src='/'" in start(bad), bad


def test_signed_in_pages_move_into_the_shell_and_the_login_does_not(client):
    c, alice, _ = client
    assert "data-shell='/shell'" in c.get("/", headers={**alice, **HTML}).text
    assert "data-shell='/shell'" in c.get("/browse/music", headers={**alice, **HTML}).text
    assert "data-shell" not in c.get("/login", headers=HTML).text


def test_the_queue_is_every_audio_file_below_a_folder_in_play_order(client):
    c, alice, _ = client
    data = c.get("/api/queue/music/Artist", headers=alice).json()
    assert [i["title"] for i in data["items"]] == ["single", "01 a", "02 b", "01 c"]          # its own files, then each folder
    assert data["items"][1]["url"] == "/raw/music/Artist/Album%20One/01%20a.mp3" and data["truncated"] is False
    assert [i["title"] for i in c.get("/api/queue/music", headers=alice).json()["items"]] == ["single", "01 a", "02 b", "01 c"]
    assert c.get("/api/queue/music/Artist/Album%20Two", headers=alice).json()["items"][0]["folder"] == "Album Two"


def test_the_queue_follows_the_users_access(client):
    c, alice, bob = client
    assert c.get("/api/queue/other", headers=alice).status_code == 200
    assert c.get("/api/queue/other", headers=bob).status_code == 404
    assert c.get("/api/queue/music/Nope", headers=alice).status_code == 404
    assert c.get("/api/queue/music").status_code == 401


def test_a_song_page_knows_its_folders_queue_for_the_hand_over(client):
    c, alice, _ = client
    page = c.get("/view/music/Artist/Album%20One/01%20a.mp3", headers={**alice, **HTML}).text
    assert "data-queue='/api/queue/music/Artist/Album%20One'" in page and "viewer-media.js" in page


def test_the_folder_page_offers_the_queue(client):
    c, alice, _ = client
    page = c.get("/browse/music/Artist", headers={**alice, **HTML}).text
    assert "data-queue='/api/queue/music/Artist'" in page and "queue.js" in page


def test_shuffle_leaves_out_marked_folders_and_audiobooks_but_play_all_does_not(client, tmp_path):
    c, alice, _ = client
    music = tmp_path / "music" / "Artist"
    (music / "Effects").mkdir()
    (music / "Effects" / "boom.mp3").write_bytes(b"ID3" + b"\x00" * 50)
    (music / "Effects" / "noshuffle.txt").write_text("", encoding="utf-8")
    (music / "Dotted").mkdir()
    (music / "Dotted" / "tick.mp3").write_bytes(b"ID3" + b"\x00" * 50)
    (music / "Dotted" / ".noshuffle").write_text("", encoding="utf-8")
    (music / "Album One" / "book.m4b").write_bytes(b"\x00" * 50)
    titles = lambda url: sorted(i["title"] for i in c.get(url, headers=alice).json()["items"])                # noqa: E731
    assert "book" in titles("/api/queue/music/Artist") and "tick" in titles("/api/queue/music/Artist")           # Play all: everything
    mixed = titles("/api/queue/music/Artist?mix=1")
    assert mixed == ["01 a", "01 c", "02 b", "single"]                                                         # no marked folder, no audiobook
    assert titles("/api/queue/music/Artist/Effects?mix=1") == ["boom"]                                          # pressed inside it: allowed


def test_the_subfolder_switch_in_settings_limits_play_all_and_shuffle(client):
    c, alice, _ = client
    origin = {**alice, "origin": "http://testserver", "content-type": "application/x-www-form-urlencoded"}
    titles = lambda url: sorted(i["title"] for i in c.get(url, headers=alice).json()["items"])                # noqa: E731
    assert titles("/api/queue/music/Artist") == ["01 a", "01 c", "02 b", "single"]
    assert "<option value='yes' selected>" in c.get("/settings", headers={**alice, **HTML}).text
    c.post("/settings", content="queue_subfolders=no", headers=origin)
    assert titles("/api/queue/music/Artist") == ["single"] and titles("/api/queue/music/Artist?mix=1") == ["single"]
    assert "<option value='no' selected>" in c.get("/settings", headers={**alice, **HTML}).text
    c.post("/settings", content="queue_subfolders=junk", headers=origin)                                       # junk changes nothing
    assert titles("/api/queue/music/Artist") == ["single"]
    c.post("/settings", content="queue_subfolders=yes", headers=origin)
    assert len(titles("/api/queue/music/Artist")) == 4


def test_tv_mode_is_a_per_browser_switch_with_its_own_script_and_the_player_merges_on_wide_screens(client):
    c, alice, _ = client
    settings = c.get("/settings", headers={**alice, **HTML}).text
    assert "TV mode" in settings and "/shell?tv=1" in settings and "/shell?tv=0" in settings           # links, no script on the page
    scheme = c.get("/static/scheme.js").text
    assert "pluteus-tv" in scheme and "pl-tv" in scheme and "tvnav.js" in scheme
    tv = c.get("/static/tvnav.js")
    assert tv.status_code == 200 and "plTvEnter" in tv.text and "pl-reader-page" in tv.text           # the readers keep their own keys
    assert "html.pl-tv" in c.get("/static/base.css").text
    shell = c.get("/static/shell.js").text
    assert "min-width: 1000px" in shell and "pl-merged" in shell
    assert "pl-merged" in c.get("/static/base.css").text


def test_full_screen_belongs_to_the_shell_frame_so_the_next_file_stays_full_screen(client):
    """The page's own player box used to go full screen, and opening the next file replaced the page, which ended it. In the shell the
    frame goes full screen (shell.js fullscreen service), the page only covers itself (pl-maxed) and a new page asks for the mode when it opens."""
    c = client[0] if isinstance(client, tuple) else client
    auth = client[1] if isinstance(client, tuple) else {}
    shell = c.get("/static/shell.js", headers=auth).text
    assert "frame.requestFullscreen" in shell and "fullscreen: fullscreen" in shell and "onChange" in shell
    common = c.get("/static/player-common.js", headers=auth).text
    assert "plShell.fullscreen" in common and "shellFs.enter(" in common and "pl-maxed" in common
    for name in ("viewer-media.js", "iptv.js"):                 # the pages use the shared code and carry no copy of it
        js = c.get("/static/" + name, headers=auth).text
        assert "plScreenModes" in js and "requestFullscreen" not in js and "shellFs" not in js, name
    css = c.get("/static/base.css", headers=auth).text
    assert ".pl-videobox.pl-maxed" in css and "position: fixed; inset: 0" in css


def test_the_other_screen_button_switches_the_mode_instead_of_leaving_full_screen(client):
    """Pressing Full screen while maximized (or the reverse) used to leave full screen. It switches the mode; only the same button leaves."""
    c, auth = client[0], client[1]
    common = c.get("/static/player-common.js", headers=auth).text
    assert "current !== wanted" in common and "shellFs.setMode(wanted)" in common
    assert "setMode: function" in c.get("/static/shell.js", headers=auth).text

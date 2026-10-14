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

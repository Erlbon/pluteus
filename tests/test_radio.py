"""Radio stations as .m3u / .pls / .url files: the parser, and the player page with its own media policy."""
import pytest
from starlette.testclient import TestClient

from conftest import basic
from pluteus import radio
from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import parse_config

HTML = {"Accept": "text/html"}
M3U = "#EXTM3U\n#EXTINF:-1,NRK P1\nhttps://example.org/p1.mp3\n#EXTINF:-1 tvg-id=x,Radio Two\nhttp://example.org:8000/two\nmusic/local.mp3\n"
PLS = "[playlist]\nFile1=https://example.org/a\nTitle1=Alpha\nFile2=ftp://example.org/b\nFile3=https://example.org/c\nNumberOfEntries=3\n"
URL = "[InternetShortcut]\nURL=https://example.org/live.aac\n"


def test_an_m3u_gives_names_and_http_addresses_only():
    assert radio.parse(M3U) == [("NRK P1", "https://example.org/p1.mp3"), ("Radio Two", "http://example.org:8000/two")]
    assert radio.parse("https://example.org/just-an-address\n") == [("", "https://example.org/just-an-address")]


def test_pls_and_url_files_are_read():
    assert radio.parse(PLS) == [("Alpha", "https://example.org/a"), ("", "https://example.org/c")]
    assert radio.parse(URL) == [("", "https://example.org/live.aac")]


def test_local_playlists_and_hls_manifests_hold_no_station():
    assert radio.parse("#EXTM3U\n#EXTINF:12,Song\nsong.mp3\nC:/music/other.mp3\n") == []
    assert radio.parse("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-STREAM-INF:BANDWIDTH=1\nhttps://example.org/a.m3u8\n") == []
    assert radio.parse("javascript:alert(1)\n") == []
    assert radio.is_radio_file("NRK.M3U") and not radio.is_radio_file("notes.txt")


@pytest.fixture
def client(tmp_path):
    folder = tmp_path / "radio"
    folder.mkdir()
    (folder / "NRK.m3u").write_text(M3U, encoding="utf-8")
    (folder / "One.pls").write_text("[playlist]\nFile1=https://example.org/a\nTitle1=<b>Alpha</b>\n", encoding="utf-8")
    (folder / "Local.m3u").write_text("song.mp3\n", encoding="utf-8")
    cfg = parse_config({"roots": [{"name": "radio", "path": str(folder)}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]}]})
    return TestClient(create_app(cfg)), basic("alice", "pw")


def test_a_station_file_opens_the_radio_player(client):
    c, auth = client
    r = c.get("/view/radio/NRK.m3u", headers={**auth, **HTML})
    assert r.status_code == 200 and "id='radio'" in r.text and "radio.js" in r.text
    assert r.text.count("<button type='button' class='pl-station") == 2 and "data-url='https://example.org/p1.mp3'" in r.text
    assert "media-src 'self' http: https:" in r.headers["content-security-policy"]


def test_a_single_station_has_no_list_and_names_are_escaped(client):
    c, auth = client
    page = c.get("/view/radio/One.pls", headers={**auth, **HTML}).text
    assert "id='radio'" in page and "pl-station" not in page.replace("pl-stations", "") and "<b>Alpha</b>" not in page
    assert "&lt;b&gt;Alpha&lt;/b&gt;" in page


def test_other_files_and_as_text_keep_the_text_viewer_and_the_normal_policy(client):
    c, auth = client
    local = c.get("/view/radio/Local.m3u", headers={**auth, **HTML})
    assert "id='radio'" not in local.text and "song.mp3" in local.text
    assert "media-src" not in local.headers["content-security-policy"]
    text = c.get("/view/radio/NRK.m3u?as=text", headers={**auth, **HTML})
    assert "id='radio'" not in text.text and "NRK P1" in text.text

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
    c = TestClient(create_app(cfg))
    c.folder = folder
    return c, basic("alice", "pw")


TV = ('#EXTM3U\n#EXTINF:-1 tvg-id="x" tvg-logo="https://example.org/l.png" group-title="News, Weather",Channel One\n'
      "https://example.org/one/index.m3u8\n")


def test_channel_lists_and_hls_addresses_are_tv_and_plain_streams_are_radio():
    assert radio.parse(TV) == [("Channel One", "https://example.org/one/index.m3u8")]          # a comma inside an attribute is not the name
    assert radio.looks_like_tv(TV, radio.parse(TV))
    assert radio.looks_like_tv("https://example.org/live/index.m3u8?x=1\n", radio.parse("https://example.org/live/index.m3u8?x=1\n"))
    assert not radio.looks_like_tv(M3U, radio.parse(M3U))


def test_a_channel_file_opens_the_video_page_with_its_own_policy(client):
    c, auth = client
    (c.folder / "Channel.m3u").write_text(TV, encoding="utf-8")
    r = c.get("/view/radio/Channel.m3u", headers={**auth, **HTML})
    assert "<video id='tv'" in r.text and "vendor/hls.min.js" in r.text and "iptv.js" in r.text and "id='radio'" not in r.text
    csp = r.headers["content-security-policy"]
    assert "connect-src 'self' http: https:" in csp and "media-src 'self' blob: http: https:" in csp
    assert "connect-src 'self';" in c.get("/view/radio/Local.m3u", headers={**auth, **HTML}).headers["content-security-policy"]
    assert c.get("/static/vendor/hls.min.js").status_code in (200, 401)


def test_the_tv_sound_switch_in_settings(client):
    c, auth = client
    (c.folder / "Channel.m3u").write_text(TV, encoding="utf-8")
    page = lambda: c.get("/view/radio/Channel.m3u", headers={**auth, **HTML}).text           # noqa: E731
    assert "data-keep-sound='1'" in page() and "data-page='/view/radio/Channel.m3u'" in page()          # on by default
    assert "<option value='keep' selected>" in c.get("/settings", headers={**auth, **HTML}).text
    form = {**auth, "origin": "http://testserver", "content-type": "application/x-www-form-urlencoded"}
    c.post("/settings", content="tv_sound=stop", headers=form)
    assert "data-keep-sound" not in page()
    assert "<option value='stop' selected>" in c.get("/settings", headers={**auth, **HTML}).text
    c.post("/settings", content="tv_sound=keep", headers=form)
    assert "data-keep-sound='1'" in page()
    c.post("/settings", content="tv_sound=junk", headers=form)                                    # anything else is ignored
    assert "data-keep-sound='1'" in page()


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


def test_a_transport_stream_address_opens_as_a_channel_without_any_marker():
    from pluteus import radio
    for address in ("http://tv.lan/proxy/ts/stream/46ae7592-3233-4e52-8ed5-cdf9154d45ed", "http://h/live/ch1.ts", "http://h/x/stream.M2TS?token=1", "https://h/a/b.m3u8"):
        assert radio.looks_like_tv("", [("C", address)]), address
    for address in ("http://h/stream", "https://h/radio.mp3", "http://h/listen/ts-radio", "http://h:8000/live.aac"):
        assert not radio.looks_like_tv("", [("R", address)]), address


def test_a_channel_page_has_subtitle_maximize_and_full_screen_buttons_and_the_script_handles_them(client):
    c, auth = client
    (c.folder / "Channel.m3u").write_text(TV, encoding="utf-8")
    page = c.get("/view/radio/Channel.m3u", headers={**auth, **HTML}).text
    assert "id='cc'" in page and "id='cc' class='pl-vol-btn pl-cc-btn' aria-label='Subtitles' aria-pressed='false' hidden" in page    # shown by the script only when the channel has subtitles
    assert page.index("id='cc'") < page.index("id='mx'") < page.index("id='fs'")
    assert "pl-i-maximize" in page and "pl-i-fullscreen" in page
    js = c.get("/static/iptv.js").text
    assert "subtitleTracks" in js and "SUBTITLE_TRACKS_UPDATED" in js and "textTracks" in js
    assert 'getElementById("mx")' in js and '"pl-full"' in js and '"pl-idle"' in js
    css = c.get("/static/base.css").text
    assert ".pl-videobox.pl-iptv.pl-full video" in css and "object-fit: contain" in css and "pl-idle" in css
    assert c.get("/theme/night/icons/maximize.svg").status_code == 200

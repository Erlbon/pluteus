"""Importing an IPTV playlist (Dispatcharr and the like) into the TV picker: parsing, the server-side fetch, and how a saved TS channel opens."""
import http.server
import json
import threading

import pytest
from test_relay import signed_in
from test_stations import JSON, post  # noqa: F401
from test_uploads import ORIGIN, up  # noqa: F401

from pluteus import fetchimage, m3uimport

PLAYLIST = """#EXTM3U
#EXTINF:-1 tvg-id="nrk1.no" tvg-name="NRK 1 HD" tvg-logo="http://tv.lan/api/channels/logos/1/cache/" group-title="Norway, News",NRK 1
http://tv.lan/proxy/ts/stream/46ae7592-3233-4e52-8ed5-cdf9154d45ed
#EXTINF:-1 tvg-name="Only Attr Name",
http://tv.lan/proxy/ts/stream/aaaaaaaa-3233-4e52-8ed5-cdf9154d45ed
#EXTVLCOPT:network-caching=1000
#EXTINF:-1 group-title="Sport",Bad Scheme
rtmp://example.org/live
http://tv.lan/proxy/ts/stream/orphan-without-extinf
#EXTINF:-1,Plain\ttab\x00name
https://example.org/live.m3u8
"""


def test_the_playlist_is_read_into_channels():
    got = m3uimport.parse(PLAYLIST)
    names = [s["name"] for s in got["stations"]]
    assert names == ["NRK 1", "Only Attr Name", "Plain tab name"]
    first = got["stations"][0]
    assert first == {"name": "NRK 1", "id": "nrk1.no", "group": "Norway, News", "logo": "http://tv.lan/api/channels/logos/1/cache/",
                     "url": "http://tv.lan/proxy/ts/stream/46ae7592-3233-4e52-8ed5-cdf9154d45ed"}
    assert got["skipped"] == 2                                                        # the rtmp:// channel and the address with no #EXTINF before it


def test_junk_and_huge_playlists_are_bounded():
    assert m3uimport.parse("not a playlist")["stations"] == []
    many = "#EXTM3U\n" + "".join("#EXTINF:-1,C%d\nhttp://h.example/%d\n" % (i, i) for i in range(m3uimport.MAX_ENTRIES + 50))
    got = m3uimport.parse(many)
    assert len(got["stations"]) == m3uimport.MAX_ENTRIES and got["skipped"] == 50
    odd = m3uimport.parse('#EXTINF:-1 tvg-logo="javascript:alert(1)" group-title="' + "x" * 500 + '",N\nhttp://h.example/1\n')["stations"][0]
    assert odd["logo"] == "" and len(odd["group"]) == 60


def parse(me, body):
    return me.post("/api/stations/parse", content=json.dumps(body), headers=JSON)


def test_the_endpoint_parses_pasted_text_and_refuses_what_is_not_a_playlist(up):
    me, app, folder = up
    r = parse(me, {"text": PLAYLIST})
    assert r.status_code == 200 and len(r.json()["stations"]) == 3
    assert parse(me, {"text": "hello"}).status_code == 400 and parse(me, {}).status_code == 400
    assert me.post("/api/stations/parse", content=b"[1", headers=JSON).status_code == 400
    assert me.post("/api/stations/parse", content=json.dumps({"text": PLAYLIST})).status_code in (401, 403)       # a same-site post only


class Serve(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = PLAYLIST.encode()
        self.send_response(200)
        self.send_header("Content-Type", "audio/x-mpegurl")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def lan_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Serve)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    fetchimage.set_allowed(())


def test_the_server_fetches_a_playlist_only_with_the_relay_and_only_from_a_listed_private_host(cfg, tmp_path, lan_server):
    address = "http://127.0.0.1:%d/output/m3u" % lan_server
    me, _ = signed_in(cfg, tmp_path / "off")
    assert parse(me, {"url": address}).status_code == 400 and "relay" in parse(me, {"url": address}).json()["error"]
    me, _ = signed_in(cfg, tmp_path / "on", relay=True)
    refused = parse(me, {"url": address})
    assert refused.status_code == 502 or refused.status_code == 400
    assert "public" in refused.json()["error"]                                         # a private host that is not listed
    me, _ = signed_in(cfg, tmp_path / "listed", relay=True, relay_allow=("127.0.0.1:%d" % lan_server,))
    ok = parse(me, {"url": address})
    assert ok.status_code == 200 and len(ok.json()["stations"]) == 3


def test_a_saved_ts_channel_opens_as_a_channel_not_as_radio(up):
    me, app, folder = up
    body = {"kind": "tv", "stations": [{"name": "NRK 1", "url": "http://tv.lan/proxy/ts/stream/46ae7592-3233-4e52-8ed5-cdf9154d45ed",
                                        "id": "nrk1.no", "group": "News"}]}
    assert me.post("/api/stations", content=json.dumps(body), headers=JSON).json()["saved"] == 1
    saved = (folder / "alice" / "IPTV" / "NRK 1.m3u").read_text(encoding="utf-8")
    assert 'pluteus-kind="tv"' in saved and "/proxy/ts/stream/" in saved
    page = me.get("/view/uploads/IPTV/NRK%201.m3u", headers={"Accept": "text/html"}).text
    assert "id='tv'" in page and "pl-iptv" in page


def test_the_tv_picker_has_the_single_channel_form_and_the_import(up):
    me, app, folder = up
    page = me.get("/stations/tv", headers={"Accept": "text/html"}).text
    assert "id='oneAdd'" in page.replace('"', "'") and "id='m3uFile'" in page.replace('"', "'")
    js = me.get("/static/pickers/tv-picker.js").text
    assert "oneAdd" in js and "unshift" in js

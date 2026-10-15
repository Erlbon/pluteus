"""MPEG-TS channels (what an IPTV panel such as Dispatcharr serves): carried by the relay under its limits, and opened on the channel page with mpegts.js."""
import http.server
import threading

import pytest
from test_relay import fresh_cache, signed_in  # noqa: F401

from pluteus import fetchimage

TS_BYTES = (b"\x47" + bytes(187)) * 40                       # forty transport stream packets


class Panel(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/proxy/ts/stream/stall"):                 # a source that goes quiet in the middle
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.end_headers()
            self.wfile.write(TS_BYTES)
            self.wfile.flush()
            import time
            time.sleep(0.7)
            self.wfile.write(TS_BYTES)
            return
        self.send_response(200)
        if self.path.startswith("/proxy/ts/stream/typed"):
            self.send_header("Content-Type", "video/MP2T")
        elif self.path.startswith("/proxy/ts/stream/html"):
            self.send_header("Content-Type", "text/html")
        self.end_headers()                                    # (no type at all for the others: a panel often sends none)
        self.wfile.write(TS_BYTES if "html" not in self.path else b"<html>not a stream</html>")


@pytest.fixture
def panel():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Panel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    fetchimage.set_allowed(())


def put_tv(folder, name, *addresses):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("#EXTM3U\n" + "".join('#EXTINF:-1 pluteus-kind="tv",C%d\n%s\n' % (i, a) for i, a in enumerate(addresses)), encoding="utf-8")


def test_the_relay_carries_a_transport_stream_with_or_without_a_type(cfg, tmp_path, panel):
    host = "127.0.0.1:%d" % panel
    me, folder = signed_in(cfg, tmp_path, relay=True, relay_allow=(host,))
    put_tv(folder, "tv.m3u", "http://%s/proxy/ts/stream/typed" % host, "http://%s/proxy/ts/stream/untyped" % host, "http://%s/proxy/ts/stream/html" % host)
    for number in (0, 1):
        r = me.get("/relay/uploads/Radio/tv.m3u?i=%d&ts=1" % number)
        assert r.status_code == 200 and r.content == TS_BYTES and r.headers["content-type"] == "video/mp2t"
    bad = me.get("/relay/uploads/Radio/tv.m3u?i=2&ts=1")
    assert bad.status_code == 415 and "transport stream" in bad.text
    audio = me.get("/relay/uploads/Radio/tv.m3u?i=1")                                          # without ts=1 it is still the audio relay
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mpeg"


def test_the_stream_counts_against_the_limits_and_shows_as_a_tv_stream(cfg, tmp_path, panel):
    host = "127.0.0.1:%d" % panel
    me, folder = signed_in(cfg, tmp_path, relay=True, relay_allow=(host,))
    put_tv(folder, "tv.m3u", "http://%s/proxy/ts/stream/typed" % host)
    me.get("/relay/uploads/Radio/tv.m3u?i=0&ts=1")
    assert me.app.state.relay_limits.totals().get("alice") == len(TS_BYTES)                    # carried, counted per user
    assert me.app.state.relay_limits.snapshot() == {}                                        # and the slot is free again


def test_the_channel_page_offers_mpegts_and_a_ts_relay_address(cfg, tmp_path):
    me, folder = signed_in(cfg, tmp_path, relay=True)
    put_tv(folder, "tv.m3u", "http://tv.example/proxy/ts/stream/46ae7592-3233-4e52-8ed5-cdf9154d45ed", "https://tv.example/live/index.m3u8")
    page = me.get("/view/uploads/Radio/tv.m3u", headers={"Accept": "text/html"}).text
    assert "id='tv'" in page and "data-mpegts='/static/vendor/mpegts.min.js'" in page
    assert "tv.m3u?i=0&amp;ts=1" in page and "tv.m3u?i=1&amp;hls=1" in page                  # a TS channel goes to the TS relay, an HLS one to the HLS relay
    js = me.get("/static/iptv.js").text
    assert "Play through the server instead" in js and "Play directly instead" in js and "Stream info" in page          # the A/B switch in the info panel
    assert me.get("/static/vendor/mpegts.min.js").status_code == 200 and me.get("/static/vendor/LICENSE-mpegtsjs.txt").status_code in (200, 404)
    shell = me.get("/shell", headers={"Accept": "text/html"}).text
    assert "data-mpegts='/static/vendor/mpegts.min.js'" in shell


def test_a_source_that_goes_quiet_is_counted_as_a_stall_and_reported(cfg, tmp_path, panel, monkeypatch):
    from pluteus import relay
    monkeypatch.setattr(relay, "STALL_SECONDS", 0.3)
    host = "127.0.0.1:%d" % panel
    me, folder = signed_in(cfg, tmp_path, relay=True, relay_allow=(host,))
    me.app.state.debuglog.enable()
    put_tv(folder, "tv.m3u", "http://%s/proxy/ts/stream/stall" % host)
    r = me.get("/relay/uploads/Radio/tv.m3u?i=0&ts=1")
    assert r.status_code == 200 and r.content == TS_BYTES * 2
    text = " | ".join(t for _, t in me.app.state.debuglog.lines())
    assert "RELAY uploads/Radio/tv.m3u#0 -> ended" in text and "source stalls 1 (longest 0." in text and "slow browser connection 0 times" in text
    assert "opened in " in text and "average " in text

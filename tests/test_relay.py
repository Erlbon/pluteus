"""The stream relay: radio stations through the server, guarded, limited, off by default."""
import http.server
import socket
import threading
import time

import pytest
from starlette.testclient import TestClient
from test_login_web import HTML, make
from test_uploads import ORIGIN

from pluteus import fetchimage, relay

AUDIO = b"ID3" + bytes(range(256)) * 200            # about 50 KB of "audio"
hits: list = []                                       # the paths the test radio server was asked for


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("icy-name", "Test")
            self.end_headers()
            self.wfile.write(AUDIO)
        elif self.path == "/notype":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(AUDIO)
        elif self.path == "/page":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>not audio</html>")
        elif self.path == "/hls":
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.end_headers()
            self.wfile.write(b"#EXTM3U\n")
        elif self.path == "/gone":
            self.send_response(404)
            self.end_headers()
        elif self.path == "/inside":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/secret")
            self.end_headers()
        elif self.path == "/hop":
            self.send_response(302)
            self.send_header("Location", "/stream")
            self.end_headers()
        elif self.path.startswith("/icy"):               # a station that sends the current title inside the stream when asked (ICY)
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            if self.path != "/icy-none" and self.headers.get("Icy-MetaData") == "1":
                self.send_header("icy-metaint", "1000000" if self.path == "/icy-huge" else "16")
            self.end_headers()
            titles = {"/icy": "Kaizers Orchestra - Ompa til du dør".encode("utf-8"), "/icy-latin": "Bjørn - Små Ting".encode("latin-1"),
                      "/icy-station": b"Test Radio", "/icy-plain": b"Just a title", "/icy-empty": b""}
            raw = titles.get(self.path, b"")
            text = b"StreamTitle='" + raw + b"';StreamUrl='';" if raw else b""
            block = text + b"\x00" * (-len(text) % 16)
            try:
                self.wfile.write(b"a" * 16 + bytes([len(block) // 16]) + block + b"b" * 5000)
            except OSError:
                pass
        elif self.path.startswith("/hls/"):              # a small HLS channel: a master playlist, a media playlist, segments and a key
            name = self.path[5:].split("?")[0]
            port = self.server.server_address[1]
            if name == "master.m3u8":
                body = (b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\nlow/index.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=2000000\n"
                        b"http://relay.test:%d/hls/hi/index.m3u8\n" % port)
                kind = "application/vnd.apple.mpegurl"
            elif name.endswith("index.m3u8"):
                body = (b"#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-KEY:METHOD=AES-128,URI=\"../key.bin\"\n#EXT-X-MAP:URI=\"init.mp4\"\n"
                        b"#EXTINF:4,\nseg1.ts\n#EXTINF:4,\nseg2.ts\n#EXTINF:4,\nhttp://127.0.0.1:1/private.ts\n")
                kind = "text/plain"                      # the type is not trusted: the content decides
            elif name.endswith(".ts"):
                body, kind = (b"\x47" + b"\x00" * 187) * 3, "application/octet-stream"
            elif name.endswith("init.mp4"):
                body, kind = b"\x00\x00\x00\x18ftypiso5" + b"\x00" * 50, "video/mp4"
            elif name == "key.bin":
                body, kind = bytes(range(16)), "application/octet-stream"
            elif name == "notlist.m3u8":
                body, kind = b"<html>login</html>", "text/html"
            else:
                body, kind = b"", ""
            if not body:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/slow":                       # a stream that stays open for a while (about 1.5 seconds), then ends
            self.send_response(200)
            self.send_header("Content-Type", "audio/ogg")
            self.end_headers()
            try:
                for _ in range(50):
                    self.wfile.write(b"x" * 1024)
                    time.sleep(0.03)
            except OSError:
                pass


@pytest.fixture
def station(monkeypatch):
    """A radio server on this machine, and the guard told that the name relay.test (this machine, this port) is fine."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    real = fetchimage._public_addresses

    def allow(host, p):
        if host == "relay.test" and p == port:
            return [(socket.AF_INET, ("127.0.0.1", port))]
        return real(host, p)
    monkeypatch.setattr(fetchimage, "_public_addresses", allow)
    yield "http://relay.test:%d" % port
    server.shutdown()


def signed_in(cfg, tmp_path, **server):
    client, app = make(cfg, tmp_path, uploads_dir=str(tmp_path / "up"), **server)
    me = TestClient(app, follow_redirects=False)
    me.post("/login", content="username=alice&password=pw-alice", headers={"content-type": "application/x-www-form-urlencoded", **ORIGIN})
    return me, tmp_path / "up" / "alice" / "Radio"


def put(folder, name, *addresses):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("".join("#EXTINF:-1,S%d\n%s\n" % (i, a) for i, a in enumerate(addresses)), encoding="utf-8")


def test_it_is_off_by_default(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path)
    put(folder, "a.m3u", station + "/stream")
    assert me.get("/relay/uploads/Radio/a.m3u?i=0").status_code == 404
    assert "data-relay=''" in me.get("/view/uploads/Radio/a.m3u", headers=HTML).text


def test_a_station_is_passed_on_with_its_audio_type(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path, relay=True)
    put(folder, "a.m3u", station + "/stream", station + "/notype", station + "/hop")
    r = me.get("/relay/uploads/Radio/a.m3u?i=0")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content == AUDIO
    assert r.headers["cache-control"] == "no-store" and "icy-name" not in r.headers          # only what we state, nothing of the source's headers
    assert me.get("/relay/uploads/Radio/a.m3u?i=1").content == AUDIO                          # a server that sends no type: treated as mp3
    assert me.get("/relay/uploads/Radio/a.m3u?i=2").content == AUDIO                          # a redirect to a public address is followed


def test_what_is_not_an_audio_stream_or_not_reachable_is_refused_with_a_reason(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path, relay=True)
    put(folder, "a.m3u", station + "/page", station + "/hls", station + "/gone", station + "/inside", "http://127.0.0.1:9/x", "http://localhost/x")
    answers = [me.get("/relay/uploads/Radio/a.m3u?i=%d" % i) for i in range(6)]
    assert [a.status_code for a in answers] == [415, 415, 502, 502, 502, 502]
    assert "not an audio stream" in answers[0].text and "404" in answers[2].text
    assert "not a public address" in answers[4].text and "not a public address" in answers[5].text       # the private network is never reached


def test_only_a_station_of_the_users_own_file_is_asked_for(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path, relay=True)
    put(folder, "a.m3u", station + "/stream")
    (folder / "notes.txt").write_text(station + "/stream\n", encoding="utf-8")
    (folder / "local.m3u").write_text("song.mp3\n", encoding="utf-8")
    for url in ("/relay/uploads/Radio/a.m3u?i=1", "/relay/uploads/Radio/a.m3u?i=-1", "/relay/uploads/Radio/a.m3u?i=x",
                "/relay/uploads/Radio/notes.txt?i=0", "/relay/uploads/Radio/local.m3u?i=0", "/relay/uploads/Radio/missing.m3u?i=0",
                "/relay/uploads/Radio/a.m3u?url=http://127.0.0.1/", "/relay/lib/x.m3u"):
        status = me.get(url).status_code
        assert status in (404, 200) and (status == 404 or "url=" in url), url                  # an address in the request is simply ignored
    anon = TestClient(me.app, follow_redirects=False)
    assert anon.get("/relay/uploads/Radio/a.m3u?i=0").status_code in (401, 303)


def test_streams_at_once_are_limited_and_the_slot_comes_back(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path, relay=True, relay_streams_user=1, relay_streams_total=3)
    put(folder, "a.m3u", station + "/slow", station + "/stream")
    first = {}
    worker = threading.Thread(target=lambda: first.update(r=me.get("/relay/uploads/Radio/a.m3u?i=0")))
    worker.start()
    time.sleep(0.5)                                                                            # the first stream is open now
    second = me.get("/relay/uploads/Radio/a.m3u?i=1")                                          # one stream per user: this one has to wait
    assert second.status_code == 429 and "Too many" in second.text and second.headers["retry-after"] == "10"
    worker.join(10)
    assert first["r"].status_code == 200 and first["r"].headers["content-type"] == "audio/ogg" and len(first["r"].content) == 50 * 1024
    assert me.get("/relay/uploads/Radio/a.m3u?i=1").status_code == 200                         # the stream ended: the slot is free again


def test_users_without_an_upload_folder_cannot_use_it(cfg, tmp_path, station):
    client, app = make(cfg, tmp_path, relay=True)                       # uploads off
    me = TestClient(app, follow_redirects=False)
    me.post("/login", content="username=alice&password=pw-alice", headers={"content-type": "application/x-www-form-urlencoded", **ORIGIN})
    assert me.get("/relay/uploads/Radio/a.m3u?i=0").status_code == 404


def test_the_radio_page_offers_each_station_its_relay_address_only_when_it_is_on(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path, relay=True)
    put(folder, "a.m3u", station + "/stream", station + "/notype")
    page = me.get("/view/uploads/Radio/a.m3u", headers=HTML).text
    assert "data-relay='/relay/uploads/Radio/a.m3u?i=0|/relay/uploads/Radio/a.m3u?i=1'" in page


def test_the_limiter_counts_per_user_and_in_total():
    lim = relay.Limiter(per_user=2, total=3)
    assert lim.acquire("a") and lim.acquire("a") and not lim.acquire("a")
    assert lim.acquire("b") and not lim.acquire("c")                                          # three in all
    lim.release("a")
    assert lim.acquire("c") and lim.snapshot() == {"a": 1, "b": 1, "c": 1}
    for user in ("a", "b", "c", "c", "x"):
        lim.release(user)                                                                      # releasing too often never goes negative
    assert lim.snapshot() == {}


@pytest.mark.parametrize("url", ["ftp://example.org/a", "file:///etc/passwd", "https://user:pw@example.org/a", "http:///a", "http://example.org:0/a",
                                 "http://example.org:99999/a", "javascript:alert(1)"])
def test_only_plain_web_addresses_are_taken(url):
    with pytest.raises(relay.Refused):
        relay._split(url)


# ---- now playing (ICY titles) ------------------------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def fresh_cache():
    relay._cache.clear()
    hits.clear()


def test_the_title_block_is_parsed():
    block = b"StreamTitle='Don't Stop - Me Now';StreamUrl='http://x';" + b"\x00" * 7
    assert relay.parse_stream_title(block) == "Don't Stop - Me Now"
    assert relay.parse_stream_title("StreamTitle='Bjørn';".encode("latin-1")) == "Bjørn"                 # old stations send Latin-1
    assert relay.parse_stream_title(b"StreamUrl='x';") == "" and relay.parse_stream_title(b"") == ""
    assert relay.parse_stream_title(b"StreamTitle='a\x01b\nc';") == "a b c"                                           # control characters are removed
    assert relay.split_title("Artist - Title") == {"text": "Artist - Title", "artist": "Artist", "title": "Title"}
    assert relay.split_title("Just a title") == {"text": "Just a title"} and relay.split_title(" - x") == {"text": " - x"}


def test_the_title_is_read_without_carrying_the_audio(station):
    assert relay.now_playing(station + "/icy") == {"text": "Kaizers Orchestra - Ompa til du dør", "artist": "Kaizers Orchestra",
                                                   "title": "Ompa til du dør"}
    assert relay.now_playing(station + "/icy-latin")["artist"] == "Bjørn"
    assert relay.now_playing(station + "/icy-plain") == {"text": "Just a title"}
    assert relay.now_playing(station + "/icy-station", "Test Radio") == {}                                            # a station that repeats its own name
    for path in ("/icy-empty", "/icy-none", "/icy-huge", "/page", "/gone", "/inside"):                                # nothing useful: empty, never an error
        assert relay.now_playing(station + path) == {}, path
    assert relay.now_playing("http://127.0.0.1:9/x") == {}


def test_answers_are_kept_for_ten_seconds(station):
    relay.now_playing(station + "/icy")
    relay.now_playing(station + "/icy")
    assert hits.count("/icy") == 1                                                                                    # one look-up for any number of listeners


def test_the_page_asks_through_the_server_and_the_answer_is_json(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path, relay=True)
    put(folder, "a.m3u", station + "/icy", station + "/icy-none")
    got = me.get("/relay/uploads/Radio/a.m3u?i=0&now=1")
    assert got.status_code == 200 and got.json()["title"] == "Ompa til du dør" and got.headers["cache-control"] == "no-store"
    assert me.get("/relay/uploads/Radio/a.m3u?i=1&now=1").json() == {}
    assert me.get("/relay/uploads/Radio/a.m3u?i=5&now=1").status_code == 404
    page = me.get("/view/uploads/Radio/a.m3u", headers=HTML).text
    assert "id='now-playing' hidden" in page


def test_now_playing_is_off_with_the_relay_and_for_other_files(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path)
    put(folder, "a.m3u", station + "/icy")
    assert me.get("/relay/uploads/Radio/a.m3u?i=0&now=1").status_code == 404


# ---- HLS: TV channels through the server -------------------------------------------------------------------------------------------
def token_of(line: str) -> str:
    return line.rsplit("/relay-piece/", 1)[1].split('"')[0]


def pieces(text: str) -> list:
    return [token_of(line) for line in text.splitlines() if "/relay-piece/" in line]


def test_the_playlist_is_rewritten_so_that_every_piece_goes_through_the_server(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path, relay=True)
    put(folder, "tv.m3u", station + "/hls/master.m3u8")
    r = me.get("/relay/uploads/Radio/tv.m3u?i=0&hls=1")
    assert r.status_code == 200 and r.headers["content-type"] == "application/vnd.apple.mpegurl" and r.headers["cache-control"] == "no-store"
    assert "relay.test" not in r.text and "127.0.0.1" not in r.text and r.text.startswith("#EXTM3U")           # no address of the source is shown
    low, high = pieces(r.text)
    assert relay.verify(low, "alice")[0] == station + "/hls/low/index.m3u8"                                   # relative addresses resolved
    assert relay.verify(high, "alice")[0] == station + "/hls/hi/index.m3u8"


def test_variant_playlists_segments_keys_and_maps_come_through_with_their_own_type(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path, relay=True)
    put(folder, "tv.m3u", station + "/hls/master.m3u8")
    low = pieces(me.get("/relay/uploads/Radio/tv.m3u?i=0&hls=1").text)[0]
    media = me.get("/relay-piece/" + low)
    assert media.status_code == 200 and media.headers["content-type"] == "application/vnd.apple.mpegurl"       # text/plain upstream: the content decides
    assert "relay.test" not in media.text and media.text.count("/relay-piece/") == 5
    key, init, seg1, seg2, private = [token_of(line) for line in media.text.splitlines() if "/relay-piece/" in line]
    assert relay.verify(key, "alice")[0] == station + "/hls/key.bin"                                          # "../key.bin" resolved against the playlist
    got = me.get("/relay-piece/" + seg1)
    assert got.status_code == 200 and got.headers["content-type"] == "video/mp2t" and len(got.content) == 564
    assert me.get("/relay-piece/" + seg2).headers["content-type"] == "video/mp2t"
    assert me.get("/relay-piece/" + init).headers["content-type"] == "video/mp4"
    assert me.get("/relay-piece/" + key).content == bytes(range(16)) and me.get("/relay-piece/" + key).headers["content-type"] == "application/octet-stream"
    assert me.get("/relay-piece/" + private).status_code == 502                                               # an address in the private network is never fetched


def test_a_client_cannot_name_an_address_of_its_own(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path, relay=True)
    put(folder, "tv.m3u", station + "/hls/master.m3u8")
    token = pieces(me.get("/relay/uploads/Radio/tv.m3u?i=0&hls=1").text)[0]
    assert relay.verify(token, "bob") is None                                                                 # another user's token is worthless
    flipped = token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB")
    expired = relay.sign(station + "/hls/key.bin", "alice", "k", now=1000)
    forged = relay.sign(station + "/hls/key.bin", "alice", "k") + "x"
    for bad in (flipped, expired, forged, "http://127.0.0.1/secret", "x.y", "", "a" * 3000):
        assert me.get("/relay-piece/" + bad).status_code == 404, bad[:30]
    assert me.get("/relay/uploads/Radio/tv.m3u?i=0&hls=1&url=http://127.0.0.1:1/x").status_code == 200       # an address in the request is ignored
    anon = TestClient(me.app, follow_redirects=False)
    assert anon.get("/relay-piece/" + token).status_code in (401, 303)


def test_what_is_not_an_hls_playlist_is_refused_with_a_reason(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path, relay=True)
    put(folder, "tv.m3u", station + "/hls/notlist.m3u8", station + "/hls/missing.m3u8", station + "/stream")
    answers = [me.get("/relay/uploads/Radio/tv.m3u?i=%d&hls=1" % i) for i in range(3)]
    assert [a.status_code for a in answers] == [415, 502, 415] and "not an HLS playlist" in answers[0].text and "404" in answers[1].text


def test_an_hls_channel_holds_a_lease_that_counts_against_the_stream_limit(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path, relay=True, relay_streams_user=1)
    put(folder, "tv.m3u", station + "/hls/master.m3u8", station + "/hls/master.m3u8?second")
    assert me.get("/relay/uploads/Radio/tv.m3u?i=0&hls=1").status_code == 200
    assert me.get("/relay/uploads/Radio/tv.m3u?i=0&hls=1").status_code == 200                                 # the same channel again: the lease is renewed
    other = me.get("/relay/uploads/Radio/tv.m3u?i=1&hls=1")                                                   # a second channel: over the limit
    assert other.status_code == 429 and "Too many" in other.text


def test_leases_lapse_and_pieces_are_limited():
    lim = relay.Limiter(per_user=1, total=2)
    assert lim.lease("a", "k1", ttl=0.05) and lim.lease("a", "k1", ttl=0.05) and not lim.lease("a", "k2")      # renewing is not a second stream
    time.sleep(0.12)
    assert lim.lease("a", "k2")                                                                                 # the first one lapsed
    assert lim.lease("b", "k3") and not lim.lease("c", "k4")                                                    # two in all
    for _ in range(8):
        assert lim.acquire_piece("a")
    assert not lim.acquire_piece("a") and lim.acquire_piece("b")
    lim.release_piece("a")
    assert lim.acquire_piece("a")


def test_rewriting_and_sniffing():
    seen = []
    out = relay.rewrite_playlist('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k.bin",IV=0x1\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="data:text/plain;base64,AAAA"\n'
                                 '#EXTINF:4,\nseg.ts\n\nhttps://cdn.example.org/abs.ts?t=1\n', "https://tv.example.org/live/index.m3u8",
                                 lambda target: (seen.append(target), "/P/%d" % len(seen))[1])
    assert seen == ["https://tv.example.org/live/k.bin", "https://tv.example.org/live/seg.ts", "https://cdn.example.org/abs.ts?t=1"]
    assert 'URI="/P/1",IV=0x1' in out and 'URI="data:text/plain;base64,AAAA"' in out and "\n/P/2\n" in out and out.endswith("/P/3\n")
    assert relay.piece_type(b"\x47" + b"\x00" * 187 + b"\x47", "x") == "video/mp2t" and relay.piece_type(b"\x00\x00\x00\x20ftyp", "x") == "video/mp4"
    assert relay.piece_type(b"ID3\x04", "x") == "audio/aac" and relay.piece_type(b"WEBVTT\n", "x") == "text/vtt" and relay.piece_type(b"<html>", "x") == "application/octet-stream"
    assert relay.is_playlist(b"\xef\xbb\xbf#EXTM3U\n") and not relay.is_playlist(b"<html>#EXTM3U")


def test_the_tv_page_offers_each_channel_its_playlist_through_the_server(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path, relay=True)
    put(folder, "tv.m3u", station + "/hls/master.m3u8", station + "/hls/master.m3u8?b")
    page = me.get("/view/uploads/Radio/tv.m3u", headers=HTML).text
    assert "data-relay='/relay/uploads/Radio/tv.m3u?i=0&amp;hls=1'" in page and "data-relay='/relay/uploads/Radio/tv.m3u?i=1&amp;hls=1'" in page
    plain, folder2 = signed_in(cfg, tmp_path / "plain")
    put(folder2, "tv.m3u", station + "/hls/master.m3u8")
    assert "data-relay=''" in plain.get("/view/uploads/Radio/tv.m3u", headers=HTML).text


def test_admin_stop_ends_a_running_stream_and_frees_its_slot(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path, relay=True, relay_streams_user=1)
    put(folder, "a.m3u", station + "/slow")
    limits = me.app.state.relay_limits if hasattr(me.app.state, "relay_limits") else me.app.app.state.relay_limits
    got = {}
    worker = threading.Thread(target=lambda: got.update(r=me.get("/relay/uploads/Radio/a.m3u?i=0")))
    worker.start()
    time.sleep(0.4)
    running = limits.streams()
    assert len(running) == 1 and running[0]["user"] == "alice" and running[0]["label"] == "uploads/Radio/a.m3u#0"
    assert limits.stop(running[0]["id"])
    worker.join(10)
    assert 0 < len(got["r"].content) < 50 * 1024                                              # cut short by the Stop
    assert limits.streams() == [] and limits.totals()["alice"] == len(got["r"].content) and limits.snapshot() == {}

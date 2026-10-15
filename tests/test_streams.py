"""Stored logins and headers for protected streams: the My streams page, the relay using them only for the hosts they name, and never showing them."""
import base64
import http.server
import json
import socket
import threading

import pytest
from starlette.testclient import TestClient
from test_login_web import HTML, make
from test_uploads import ORIGIN

from pluteus import fetchimage, relay, vault

AUDIO = b"ID3" + bytes(range(256)) * 20
GOOD = "Basic " + base64.b64encode(b"alice:pw-secret").decode()
seen: list = []                                      # (path, Authorization, Referer) of every request the protected server got
FORM = {"content-type": "application/x-www-form-urlencoded", **ORIGIN}


class Protected(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    def do_GET(self):
        auth, referer = self.headers.get("Authorization"), self.headers.get("Referer")
        seen.append((self.path, auth, referer))
        if self.path.startswith("/open/"):                                   # no login needed
            path = self.path
        elif auth != GOOD or referer != "https://example.org/":
            self.send_response(401)
            self.end_headers()
            return
        else:
            path = self.path
        port = self.server.server_address[1]
        if path.endswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "http://other.test:%d/p/stream" % port)
            self.end_headers()
        elif path.endswith("/stream") or path.endswith("/icy"):
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            if path.endswith("/icy") and self.headers.get("Icy-MetaData") == "1":
                self.send_header("icy-metaint", "16")
            self.end_headers()
            block = b"StreamTitle='Band - Song';"
            block += b"\x00" * (-len(block) % 16)
            self.wfile.write(b"a" * 16 + bytes([len(block) // 16]) + block + AUDIO if path.endswith("/icy") else AUDIO)
        elif path.endswith("/master.m3u8"):
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.end_headers()
            self.wfile.write(b"#EXTM3U\n#EXTINF:4,\nseg1.ts\n")
        elif path.endswith(".ts"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write((b"\x47" + b"\x00" * 187) * 2)
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def station(monkeypatch):
    """A protected radio server on this machine; the guard is told that relay.test and other.test (this machine, this port) are fine."""
    seen.clear()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Protected)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    real = fetchimage._public_addresses

    def allow(host, p):
        if host in ("relay.test", "other.test") and p == port:
            return [(socket.AF_INET, ("127.0.0.1", port))]
        return real(host, p)
    monkeypatch.setattr(fetchimage, "_public_addresses", allow)
    relay._cache.clear()
    yield "http://relay.test:%d" % port
    server.shutdown()


def signed_in(cfg, tmp_path, **server):
    server.setdefault("relay", True)
    client, app = make(cfg, tmp_path, uploads_dir=str(tmp_path / "up"), **server)           # make() also puts the state in tmp_path/data
    me = TestClient(app, follow_redirects=False)
    me.post("/login", content="username=alice&password=pw-alice", headers=FORM)
    return me, tmp_path / "up" / "alice" / "Radio"


def save(me, name="Sub", login="basic", user="alice", password="pw-secret", headers="Referer: https://example.org/", hosts="relay.test", token=""):
    body = "&".join("%s=%s" % (k, v) for k, v in (("action", "save"), ("name", name), ("login", login), ("user", user), ("password", password),
                                                   ("token", token), ("headers", headers.replace(" ", "+").replace("\n", "%0A")), ("hosts", hosts)))
    return me.post("/settings/streams", content=body, headers=FORM)


def put(folder, name, *lines):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("#EXTM3U\n" + "".join(lines), encoding="utf-8")


def station_line(address, stream="Sub", title="Protected"):
    return '#EXTINF:-1 pluteus-stream="%s",%s\n%s\n' % (stream, title, address) if stream else "#EXTINF:-1,%s\n%s\n" % (title, address)


# ---- the page --------------------------------------------------------------------------------------------------------------------
def test_the_page_stores_an_entry_and_never_shows_or_keeps_a_secret_in_clear(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path)
    assert "href='/settings/streams'" in me.get("/settings", headers=HTML).text
    r = save(me)
    assert r.status_code == 200 and "Saved" in r.text
    page = me.get("/settings/streams", headers=HTML).text
    assert "<strong>Sub</strong>" in page and "a login" in page and "headers: Referer" in page and "relay.test" in page
    for secret in ("pw-secret", "alice:", "example.org/", GOOD):
        assert secret not in page and secret not in r.text
    blob, key_id = me.app.state.state.stream_get("alice", "Sub")
    assert b"pw-secret" not in blob and b"Referer" not in blob and b"relay.test" not in blob and len(key_id) == 12


def test_bad_input_is_refused_with_a_reason_and_nothing_is_stored(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path)
    for bad, words in ((dict(hosts=""), "stream's server"), (dict(headers="Host:+evil"), "cannot be set"), (dict(user="a:b"), "colon"),
                       (dict(login="none", headers=""), "at least one header"), (dict(name="a%22b"), "quotation")):
        r = save(me, **bad)
        assert r.status_code == 200 and words in r.text, bad
    assert me.app.state.state.stream_list("alice") == []
    assert "Nothing stored yet" in me.get("/settings/streams", headers=HTML).text
    save(me)
    deleted = me.post("/settings/streams", content="action=delete&name=Sub", headers=FORM)
    assert "Deleted" in deleted.text and me.app.state.state.stream_list("alice") == []


def test_it_is_off_without_the_relay_or_an_upload_folder_and_needs_a_login(cfg, tmp_path, station):
    plain, _ = signed_in(cfg, tmp_path, relay=False)
    assert plain.get("/settings/streams", headers=HTML).status_code == 404 and "My streams" not in plain.get("/settings", headers=HTML).text
    client, app = make(cfg, tmp_path / "x", relay=True)                              # uploads off
    me = TestClient(app, follow_redirects=False)
    me.post("/login", content="username=alice&password=pw-alice", headers=FORM)
    assert me.get("/settings/streams", headers=HTML).status_code == 404
    assert TestClient(app, follow_redirects=False).get("/settings/streams").status_code in (401, 303)


# ---- the relay uses them --------------------------------------------------------------------------------------------------------
def test_a_station_that_names_a_stored_stream_plays_with_its_login_and_headers(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path)
    put(folder, "p.m3u", station_line(station + "/p/stream"))
    no_entry = me.get("/relay/uploads/Radio/p.m3u?i=0")
    assert no_entry.status_code == 409 and 'needs the stored stream "Sub"' in no_entry.text and "My streams" in no_entry.text and seen == []
    save(me)
    got = me.get("/relay/uploads/Radio/p.m3u?i=0")
    assert got.status_code == 200 and got.content == AUDIO and got.headers["content-type"] == "audio/mpeg"
    assert seen[-1] == ("/p/stream", GOOD, "https://example.org/")
    page = me.get("/view/uploads/Radio/p.m3u", headers=HTML).text
    assert "data-always='1'" in page and "data-relay='/relay/uploads/Radio/p.m3u?i=0'" in page


def test_the_login_goes_only_to_the_hosts_the_entry_names(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path)
    put(folder, "p.m3u", station_line(station + "/p/stream"), station_line(station + "/p/redirect", title="Redirects"))
    save(me, hosts="other.test")                                                      # not the host of the station
    refused = me.get("/relay/uploads/Radio/p.m3u?i=0")
    assert refused.status_code == 502 and "401" in refused.text and seen[-1][1] is None and seen[-1][2] is None       # nothing was sent
    seen.clear()
    save(me, hosts="relay.test")                                                      # the station's host, but it redirects to another host
    redirected = me.get("/relay/uploads/Radio/p.m3u?i=1")
    assert redirected.status_code == 502 and "401" in redirected.text
    assert seen[0] == ("/p/redirect", GOOD, "https://example.org/") and seen[1] == ("/p/stream", None, None)             # dropped at the second host


def test_an_entry_belongs_to_one_user_and_a_station_without_one_works_as_before(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path)
    put(folder, "p.m3u", station_line(station + "/p/stream"), station_line(station + "/open/stream", stream="", title="Open"))
    me.app.state.state.stream_put("bob", "Sub", vault.Vault(bytes(32)).seal({"auth": {"type": "none"}, "headers": {"Referer": "x"}, "hosts": ["relay.test"]}), "x")
    assert me.get("/relay/uploads/Radio/p.m3u?i=0").status_code == 409                # bob's entry of that name is not alice's
    assert me.get("/relay/uploads/Radio/p.m3u?i=1").status_code == 200                # no stored stream needed: plain relay
    assert me.app.state.state.stream_get("alice", "Sub") is None


def test_hls_pieces_name_the_stored_stream_never_its_secrets(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path)
    put(folder, "tv.m3u", station_line(station + "/p/master.m3u8", title="TV"))
    save(me)
    playlist = me.get("/relay/uploads/Radio/tv.m3u?i=0&hls=1")
    assert playlist.status_code == 200 and "relay.test" not in playlist.text
    token = [line for line in playlist.text.splitlines() if "/relay-piece/" in line][0].rsplit("/", 1)[1]
    body = token.split(".")[0]
    payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
    assert json.loads(payload)["s"] == "Sub" and "pw-secret" not in payload and "alice:" not in payload and "example.org" not in payload
    got = me.get("/relay-piece/" + token)
    assert got.status_code == 200 and got.headers["content-type"] == "video/mp2t" and seen[-1][1] == GOOD       # the piece is fetched with the entry's login
    me.post("/settings/streams", content="action=delete&name=Sub", headers=FORM)
    assert me.get("/relay-piece/" + token).status_code == 409                                                     # the entry is gone: no piece


def test_now_playing_works_for_a_protected_station(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path)
    put(folder, "p.m3u", station_line(station + "/p/icy"))
    assert me.get("/relay/uploads/Radio/p.m3u?i=0&now=1").status_code == 409
    save(me)
    assert me.get("/relay/uploads/Radio/p.m3u?i=0&now=1").json() == {"text": "Band - Song", "artist": "Band", "title": "Song"}


# ---- the key ----------------------------------------------------------------------------------------------------------------------
def test_the_key_comes_from_the_data_folder_stays_the_same_and_a_changed_key_is_reported(cfg, tmp_path, station):
    me, folder = signed_in(cfg, tmp_path)
    save(me)
    assert (tmp_path / "data" / "relay.key").exists() and "relay.key" in me.get("/settings/streams", headers=HTML).text
    again, _ = signed_in(cfg, tmp_path)                                               # a restart: the same key, the entry still opens
    put(folder, "p.m3u", station_line(station + "/p/stream"))
    assert again.get("/relay/uploads/Radio/p.m3u?i=0").status_code == 200
    other, _ = signed_in(cfg, tmp_path, relay_key="a different passphrase")           # the config now names another key
    page = other.get("/settings/streams", headers=HTML).text
    assert "stored with another key" in page and "relay_key in the config" in page
    changed = other.get("/relay/uploads/Radio/p.m3u?i=0")
    assert changed.status_code == 409 and "another key" in changed.text


def test_a_config_key_wins_and_a_bad_key_file_turns_stored_streams_off(cfg, tmp_path, station):
    me, _ = signed_in(cfg, tmp_path, relay_key="my passphrase")
    assert save(me).status_code == 200 and not (tmp_path / "data" / "relay.key").exists()
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "relay.key").write_text("garbage")
    broken, _ = signed_in(cfg, tmp_path / "again", relay=True)
    (tmp_path / "again" / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "again" / "data" / "relay.key").write_text("garbage")
    off, _ = signed_in(cfg, tmp_path / "again")
    assert off.get("/settings/streams", headers=HTML).status_code == 404 and (tmp_path / "again" / "data" / "relay.key").read_text() == "garbage"


def test_the_entries_of_a_user_that_is_gone_are_deleted(cfg, tmp_path, station):
    me, _ = signed_in(cfg, tmp_path)
    save(me)
    state = me.app.state.state
    state.stream_put("ghost", "Old", b"x" * 60, "k")
    state.purge_appkeys({"alice"})
    assert state.stream_list("ghost") == [] and [n for n, _, _ in state.stream_list("alice")] == ["Sub"]

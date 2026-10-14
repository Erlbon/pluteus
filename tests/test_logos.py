"""Station logos: the guarded fetch of a picture from a request's address, the picture saved beside the .m3u, and where it shows."""
import http.server
import io
import json
import threading

import pytest
from PIL import Image
from test_login_web import HTML
from test_uploads import ORIGIN, up  # noqa: F401  (the signed-in client with uploads on)

from pluteus import fetchimage, stationsave

JSON = {**ORIGIN, "content-type": "application/json"}


def png_bytes(size=(600, 300), color=(200, 20, 40, 255)):
    out = io.BytesIO()
    Image.new("RGBA", size, color).save(out, "PNG")
    return out.getvalue()


# ---- the guard ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("url", ["ftp://example.org/a.png", "file:///etc/passwd", "javascript:alert(1)", "https://user:pw@example.org/a.png",
                                 "https://example.org:8443/a.png", "http://example.org:22/a.png", "//example.org/a.png", "https:///a.png"])
def test_only_plain_web_addresses_on_the_usual_ports_are_taken(url):
    with pytest.raises(fetchimage.Refused):
        fetchimage._split(url)


@pytest.mark.parametrize("answer", ["127.0.0.1", "10.1.2.3", "192.168.1.99", "172.16.0.5", "169.254.169.254", "0.0.0.0", "::1", "fe80::1", "fc00::1"])
def test_a_name_that_resolves_to_a_private_address_is_refused(monkeypatch, answer):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answer, port))])
    with pytest.raises(fetchimage.Refused):
        fetchimage._public_addresses("logo.example.org", 443)


def test_one_private_answer_among_public_ones_refuses_the_name(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)), (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", port))])
    with pytest.raises(fetchimage.Refused):
        fetchimage._public_addresses("rebind.example.org", 443)


def test_a_public_name_is_accepted(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))])
    assert fetchimage._public_addresses("example.org", 443)[0][1][0] == "93.184.216.34"


# ---- the picture -------------------------------------------------------------------------------------------------------------
def test_a_picture_is_made_small_and_rewritten():
    data, ext = fetchimage.small_picture(png_bytes((1200, 600)))
    assert ext == "png"
    image = Image.open(io.BytesIO(data))
    assert max(image.size) == fetchimage.MAX_SIDE and image.format == "PNG"
    out = io.BytesIO()
    Image.new("RGB", (300, 300), (1, 2, 3)).save(out, "JPEG")
    assert fetchimage.small_picture(out.getvalue())[1] == "jpg"


@pytest.mark.parametrize("junk", [b"", b"not a picture", b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>",
                                  b"GIF89a", b"\x89PNG\r\n\x1a\n" + b"0" * 20, png_bytes((4, 4))])
def test_anything_that_is_not_a_real_picture_is_refused(junk):
    with pytest.raises(fetchimage.Refused):
        fetchimage.small_picture(junk)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/logo.png":
            body = png_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")                    # the reply's own claim is not trusted
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/gone":
            self.send_response(404)
            self.end_headers()
        elif self.path == "/inside":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/secret")           # a redirect into the private network
            self.end_headers()
        elif self.path == "/big":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x" * 100_000)


@pytest.fixture
def local(monkeypatch):
    """A web server on this machine, and the guard told that this one address is fine (it is the test's own)."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    import socket
    real = fetchimage._public_addresses

    def allow(host, p):
        if host == "logos.test" and p == port:
            return [(socket.AF_INET, ("127.0.0.1", port))]
        return real(host, p)
    monkeypatch.setattr(fetchimage, "_public_addresses", allow)
    monkeypatch.setitem(fetchimage.PORTS, "http", (80, port))
    yield "http://logos.test:%d" % port
    server.shutdown()


def test_a_logo_is_downloaded_checked_and_returned_as_a_fresh_picture(local):
    data, ext = fetchimage.fetch_logo(local + "/logo.png")
    assert ext == "png" and Image.open(io.BytesIO(data)).size == (256, 128)


def test_bad_replies_give_no_logo_and_never_an_error(local, monkeypatch):
    assert fetchimage.fetch_logo(local + "/gone") is None
    assert fetchimage.fetch_logo(local + "/inside") is None                   # the redirect target is checked again and refused
    monkeypatch.setattr(fetchimage, "MAX_BYTES", 1000)
    assert fetchimage.fetch_logo(local + "/big") is None
    assert fetchimage.fetch_logo("http://127.0.0.1:1/x") is None


# ---- saving beside the .m3u ---------------------------------------------------------------------------------------------------
def post(client, body):
    return client.post("/api/stations", content=json.dumps(body), headers=JSON)


def test_the_logo_is_saved_beside_the_station_file_and_named_in_it(up, monkeypatch):
    me, app, folder = up
    pic = png_bytes((64, 64))
    asked = []
    monkeypatch.setattr(fetchimage, "fetch_logo", lambda url: (asked.append(url), (pic, "png"))[1] if "good" in url else None)
    r = post(me, {"kind": "radio", "stations": [
        {"name": "NRK P3", "url": "https://example.org/p3", "logo": "https://logos.example.org/good.png"},
        {"name": "No logo", "url": "https://example.org/n", "logo": "https://logos.example.org/bad.png"},
        {"name": "Hostile", "url": "https://example.org/h", "logo": "javascript:alert(1)"},
        {"name": "Quoted", "url": "https://example.org/q", "logo": 'https://x.example.org/a"b.png'}]})
    assert r.status_code == 200 and r.json()["saved"] == 4 and r.json()["logos"] == 1
    mine = folder / "alice" / "Radio"
    assert (mine / "NRK P3.png").read_bytes() == pic and not (mine / "No logo.png").exists() and not (mine / "Hostile.png").exists()
    assert 'tvg-logo="https://logos.example.org/good.png"' in (mine / "NRK P3.m3u").read_text(encoding="utf-8")
    assert "tvg-logo" not in (mine / "Hostile.m3u").read_text(encoding="utf-8") and "tvg-logo" not in (mine / "Quoted.m3u").read_text(encoding="utf-8")
    assert "javascript" not in "".join(asked)                                 # a non-web address is never even tried


def test_an_existing_picture_is_kept_and_a_replaced_file_gets_the_new_one(up, monkeypatch):
    me, app, folder = up
    monkeypatch.setattr(fetchimage, "fetch_logo", lambda url: (png_bytes((32, 32)), "png"))
    body = {"kind": "tv", "stations": [{"name": "One", "url": "https://example.org/one.m3u8", "logo": "https://l.example.org/1.png"}]}
    post(me, body)
    target = folder / "alice" / "IPTV" / "One.png"
    target.write_bytes(b"my own picture")
    again = post(me, body).json()
    assert again["skipped"] == 1 and again["logos"] == 0 and target.read_bytes() == b"my own picture"
    forced = post(me, {**body, "overwrite": True}).json()
    assert forced["logos"] == 1 and target.read_bytes() != b"my own picture"


def test_the_picture_shows_on_the_station_page_the_cover_route_and_nowhere_else(up, monkeypatch):
    me, app, folder = up
    pic = png_bytes((64, 64))
    monkeypatch.setattr(fetchimage, "fetch_logo", lambda url: (pic, "png"))
    post(me, {"kind": "radio", "stations": [{"name": "NRK P3", "url": "https://example.org/p3", "logo": "https://l.example.org/1.png"},
                                            {"name": "Plain", "url": "https://example.org/p", "logo": ""}]})
    page = me.get("/view/uploads/Radio/NRK%20P3.m3u", headers=HTML).text
    assert "<img class='pl-cover-img pl-logo-img' src='/cover/uploads/Radio/NRK%20P3.m3u'" in page and "data-logo='/cover/uploads/Radio/NRK%20P3.m3u'" in page
    cover = me.get("/cover/uploads/Radio/NRK%20P3.m3u")
    assert cover.status_code == 200 and cover.headers["content-type"] == "image/png" and cover.content == pic
    assert me.get("/cover/uploads/Radio/Plain.m3u").status_code == 404
    assert "pl-logo-img" not in me.get("/view/uploads/Radio/Plain.m3u", headers=HTML).text
    (folder / "alice" / "Radio" / "TV.m3u").write_text("https://example.org/tv/index.m3u8\n", encoding="utf-8")
    (folder / "alice" / "Radio" / "TV.png").write_bytes(pic)                     # a picture put there by hand counts too
    tv = me.get("/view/uploads/Radio/TV.m3u", headers=HTML).text
    assert "<img class='pl-tv-logo' src='/cover/uploads/Radio/TV.m3u'" in tv and "data-logo='/cover/uploads/Radio/TV.m3u'" in tv


def test_the_station_save_survives_slow_or_failing_logo_hosts(up, monkeypatch):
    me, app, folder = up
    monkeypatch.setattr(fetchimage, "fetch_logo", lambda url: 1 / 0)             # even an unexpected failure only costs the logo
    r = post(me, {"kind": "radio", "stations": [{"name": "Boom", "url": "https://example.org/b", "logo": "https://l.example.org/b.png"}]})
    assert r.status_code == 200 and r.json()["saved"] == 1 and r.json()["logos"] == 0
    assert stationsave.LOGO_BUDGET > 0


# ---- logos for station files that are already there ------------------------------------------------------------------------------
def make_files(folder):
    radio = folder / "alice" / "Radio"
    (radio / "Norway").mkdir(parents=True)
    (radio / "a.m3u").write_text("https://example.org/a\n", encoding="utf-8")
    (radio / "Norway" / "b.m3u").write_text('#EXTM3U\n#EXTINF:-1 tvg-logo="https://logos.example.org/b.png",B Station\nhttps://example.org/b\n', encoding="utf-8")
    (radio / "c.m3u").write_text("#EXTM3U\n#EXTINF:-1,C\nhttps://example.org/c\n", encoding="utf-8")
    (radio / "c.png").write_bytes(png_bytes((16, 16)))                                    # c already has a picture
    (radio / "d.pls").write_text("[playlist]\nFile1=https://example.org/d\nTitle1=D\n", encoding="utf-8")
    (radio / "e.m3u").write_text("https://example.org/e\n", encoding="utf-8")
    (radio / "local.m3u").write_text("song.mp3\n", encoding="utf-8")                      # not a station
    (radio / "notes.txt").write_text("https://example.org/x\n", encoding="utf-8")
    return radio


def test_the_files_without_a_logo_are_listed_with_what_the_file_already_says(up):
    me, app, folder = up
    assert me.get("/api/stations/missing?kind=radio").json() == {"items": []}             # no Radio folder yet
    make_files(folder)
    items = me.get("/api/stations/missing?kind=radio").json()["items"]
    assert sorted((i["path"], i["url"], i["logo"]) for i in items) == [
        ("Norway/b.m3u", "https://example.org/b", "https://logos.example.org/b.png"),
        ("a.m3u", "https://example.org/a", ""), ("d.pls", "https://example.org/d", ""), ("e.m3u", "https://example.org/e", "")]
    assert me.get("/api/stations/missing?kind=nope").status_code == 400
    assert me.get("/api/stations/missing?kind=tv").json() == {"items": []}


def test_the_found_logos_are_fetched_saved_and_named_in_the_file(up, monkeypatch):
    me, app, folder = up
    radio = make_files(folder)
    pic = png_bytes((48, 48))
    monkeypatch.setattr(fetchimage, "fetch_logo", lambda url: (pic, "png") if "good" in url else None)
    r = me.post("/api/stations/logos", content=json.dumps({"kind": "radio", "items": [
        {"path": "a.m3u", "logo": "https://l.example.org/good-a.png"},
        {"path": "Norway/b.m3u", "logo": "https://logos.example.org/good-b.png"},
        {"path": "d.pls", "logo": "https://l.example.org/good-d.png"},
        {"path": "c.m3u", "logo": "https://l.example.org/good-c.png"},                  # already has a picture: kept
        {"path": "../../x.m3u", "logo": "https://l.example.org/good-x.png"},              # outside: refused
        {"path": "gone.m3u", "logo": "https://l.example.org/good-g.png"},
        {"path": "notes.txt", "logo": "https://l.example.org/good-n.png"},                # not a station file
        {"path": "e.m3u", "logo": "https://l.example.org/bad.png"}]}), headers=JSON)                # the host gives no picture: counted, not fatal
    assert r.status_code == 200 and r.json() == {"fetched": 3, "failed": 1, "skipped": 4}
    assert (radio / "a.png").read_bytes() == pic and (radio / "Norway" / "b.png").read_bytes() == pic and (radio / "d.png").read_bytes() == pic
    assert (radio / "c.png").read_bytes() != pic and not (radio / "gone.png").exists()
    assert (radio / "a.m3u").read_text(encoding="utf-8") == '#EXTINF:-1 tvg-logo="https://l.example.org/good-a.png",a\nhttps://example.org/a\n'
    assert 'tvg-logo="https://logos.example.org/b.png"' in (radio / "Norway" / "b.m3u").read_text(encoding="utf-8")      # it already named one
    assert (radio / "d.pls").read_text(encoding="utf-8") == "[playlist]\nFile1=https://example.org/d\nTitle1=D\n"        # a .pls is left as it was
    assert [i["path"] for i in me.get("/api/stations/missing?kind=radio").json()["items"]] == ["e.m3u"]                  # only the failed one is left


def test_the_logo_calls_need_a_signed_in_user_with_an_upload_folder(up, cfg, tmp_path):
    from starlette.testclient import TestClient
    from test_login_web import make
    me, app, folder = up
    anon = TestClient(app, follow_redirects=False)
    assert anon.get("/api/stations/missing?kind=radio").status_code in (401, 303)
    assert anon.post("/api/stations/logos", content="{}", headers=JSON).status_code in (401, 303)
    assert me.post("/api/stations/logos", content="not json", headers=JSON).status_code == 400
    assert me.post("/api/stations/logos", content=json.dumps({"kind": "radio", "items": "x"}), headers=JSON).status_code == 400
    client, plain = make(cfg, tmp_path)                                                   # uploads off
    off = TestClient(plain, follow_redirects=False)
    off.post("/login", content="username=alice&password=pw-alice", headers={"content-type": "application/x-www-form-urlencoded", **ORIGIN})
    assert off.get("/api/stations/missing?kind=radio").status_code == 404

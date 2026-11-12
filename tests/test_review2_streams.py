"""Second code review (2026-11-12), the streams batch: cards 9 (relay and station save), 22 (c), 31, 49 (WebDAV and OPDS), 50 and 51.

Every upstream server here is a small socket on 127.0.0.1 (port 0, read back); nothing leaves this machine."""
import json
import os
import socket
import sys
import threading
import time
import types
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest
from test_logos import JSON, png_bytes
from test_uploads import up  # noqa: F401  (the signed-in client with uploads on)

from pluteus import castdevices, fetchimage, opds, opds2, radio, relay, stationsave, streams, uploads, webdav
from pluteus.config import CastDevice
from pluteus.fs import Entry
from pluteus.updatecheck import UpdateChecker
from pluteus.urls import Urls

D = "{DAV:}"
ATOM = "{http://www.w3.org/2005/Atom}"
ODD = "caf\udce9.epub"                # what os.scandir hands over on Linux for the Latin-1 file name b"caf\xe9.epub"
CONTROL = "a\x01b.epub"


# ---- small upstream servers --------------------------------------------------------------------------------------------------
class RawServer:
    """A socket that hands every connection (after reading the request's header) to `serve(connection, request, stop)`."""

    def __init__(self, serve):
        self.serve, self.stop = serve, threading.Event()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(connection,), daemon=True).start()

    def _one(self, connection):
        try:
            request = b""
            while b"\r\n\r\n" not in request:
                more = connection.recv(4096)
                if not more:
                    return
                request += more
            self.serve(connection, request, self.stop)
        except OSError:
            pass
        finally:
            connection.close()

    def close(self):
        self.stop.set()
        self.sock.close()


@pytest.fixture
def source(monkeypatch):
    """start(serve) -> the address of a new upstream server, under a name the address guard is told is fine (relay.test)."""
    servers = []
    real = fetchimage._public_addresses

    def allow(host, port):
        if host == "relay.test" and port in [s.port for s in servers]:
            return [(socket.AF_INET, ("127.0.0.1", port))]
        return real(host, port)
    monkeypatch.setattr(fetchimage, "_public_addresses", allow)

    def start(serve):
        servers.append(RawServer(serve))
        return "http://relay.test:%d" % servers[-1].port
    yield start
    for server in servers:
        server.close()


def trickle(head: bytes, piece: bytes = b"x", pause: float = 0.05):
    """A source that sends `head` at once and then one small piece at a time, for as long as anyone listens."""
    def serve(connection, request, stop):
        connection.sendall(head)
        while not stop.wait(pause):
            connection.sendall(piece)
    return serve


# ---- card 9: the relay's small fetches have a limit for the whole fetch -------------------------------------------------------
def test_a_playlist_gets_ten_seconds_and_a_piece_thirty():
    assert relay.deadline_for(relay.MAX_PLAYLIST) == 10.0 and relay.deadline_for(relay.MAX_PIECE) == 30.0
    assert relay.deadline_for(4_000_000) == 30.0                             # a station list being imported is a large download too


def test_a_quick_source_is_fetched_as_before(source):
    url = source(lambda c, request, stop: c.sendall(b"HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\n\r\n#EXTM3U\n"))
    assert relay.fetch_body(url + "/list", relay.MAX_PLAYLIST) == (b"#EXTM3U\n", "text/plain", url + "/list")
    assert relay.fetch_body(url + "/list", relay.MAX_PLAYLIST, None, 5.0)[0] == b"#EXTM3U\n"


@pytest.mark.parametrize("head", [
    b"HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\n\r\n",          # the body comes one byte at a time, each well inside the read limit
    b"HTTP/1.0 200 OK\r\nX-Slow: ",                                   # the header itself never ends
    b""])                                                              # not even a status line
def test_a_source_that_trickles_is_given_up_after_the_deadline(source, head):
    url = source(trickle(head, b"x" if head else b""))
    began = time.monotonic()
    with pytest.raises(relay.Refused):
        relay.fetch_body(url + "/slow", relay.MAX_PIECE, deadline=0.6)
    assert time.monotonic() - began < 5.0                                # not the days a byte every few seconds would buy


def test_the_deadline_leaves_no_thread_behind(source):
    url = source(trickle(b"HTTP/1.0 200 OK\r\n\r\n"))
    before = set(threading.enumerate())
    with pytest.raises(relay.Refused):
        relay.fetch_body(url + "/slow", relay.MAX_PIECE, deadline=0.4)
    assert relay.fetch_body(source(lambda c, r, s: c.sendall(b"HTTP/1.0 200 OK\r\n\r\nok")) + "/", 100)[0] == b"ok"
    for _ in range(200):
        left = [t for t in threading.enumerate() if t not in before and t.name.startswith("pluteus-relay")]
        if not left:
            break
        time.sleep(0.02)
    assert not left                                                      # the watch ends with the fetch, early or late


# ---- card 9: the logo fetch of a station save leaves no stuck thread ----------------------------------------------------------
def test_slow_logo_hosts_cost_the_logos_and_no_thread_outlives_its_fetch(monkeypatch):
    release, calls = threading.Event(), []

    def slow(url):
        calls.append(url)
        release.wait(30)
        return (b"picture", "png")
    monkeypatch.setattr(fetchimage, "fetch_logo", slow)
    monkeypatch.setattr(stationsave, "LOGO_BUDGET", 0.3)
    before = set(threading.enumerate())
    began = time.monotonic()
    found = stationsave._fetch_logos([{"logo": "https://l.example.org/%d.png" % n} for n in range(20)])
    assert found == {} and time.monotonic() - began < 5.0
    started = [t for t in threading.enumerate() if t not in before]
    assert started and all(t.daemon for t in started)                    # none of them can hold up a shutdown
    release.set()
    for _ in range(250):
        if not any(t.is_alive() for t in started):
            break
        time.sleep(0.02)
    assert not any(t.is_alive() for t in started)
    assert len(calls) <= 6                                               # after the budget no waiting logo is started


def test_logos_are_still_fetched_several_at_a_time(monkeypatch):
    monkeypatch.setattr(fetchimage, "fetch_logo", lambda url: (url.encode(), "png") if "good" in url else (None if "bad" in url else 1 / 0))
    entries = [{"logo": "https://l.example.org/good-%d.png" % n} for n in range(15)]
    entries += [{"logo": "https://l.example.org/bad.png"}, {"logo": "https://l.example.org/boom.png"}, {"logo": "javascript:alert(1)"}, "junk"]
    found = stationsave._fetch_logos(entries)
    assert sorted(found) == list(range(15)) and found[3] == (b"https://l.example.org/good-3.png", "png")
    assert stationsave._fetch_logos([]) == {} and stationsave._fetch_logos([{"logo": ""}]) == {}


# ---- card 22 (c): two saves of the same station file do not share a temporary name --------------------------------------------
def test_a_save_does_not_use_a_fixed_temporary_name(tmp_path):
    (tmp_path / ".NRK P3.m3u.tmp").mkdir()                               # whatever sits at the old fixed name is no longer in the way
    stationsave._write(str(tmp_path), "NRK P3.m3u", b"one")
    assert (tmp_path / "NRK P3.m3u").read_bytes() == b"one"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".NRK P3.m3u.tmp", "NRK P3.m3u"]          # and it leaves nothing of its own


def test_two_saves_of_the_same_file_at_once_both_succeed(tmp_path):
    texts = [(b"%d" % n) * 20000 for n in range(8)]
    failures = []
    go = threading.Barrier(len(texts))

    def save(raw):
        go.wait(10)
        for _ in range(25):
            try:
                stationsave._write(str(tmp_path), "Station.m3u", raw)
            except uploads.UploadError as e:
                failures.append(e)
    threads = [threading.Thread(target=save, args=(raw,)) for raw in texts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not failures
    assert (tmp_path / "Station.m3u").read_bytes() in texts              # one whole file, never a mix
    assert [p.name for p in tmp_path.iterdir()] == ["Station.m3u"]


def test_a_failed_write_is_reported_and_leaves_no_temporary_file(tmp_path):
    (tmp_path / "Taken.m3u").mkdir()                                     # a folder where the file should go: the replace fails
    with pytest.raises(uploads.UploadError):
        stationsave._write(str(tmp_path), "Taken.m3u", b"x")
    assert [p.name for p in tmp_path.iterdir()] == ["Taken.m3u"]


# ---- card 31: the stream-folder helpers do not list folders twice -------------------------------------------------------------
@pytest.fixture
def scans(monkeypatch):
    """The folders streams.py lists, in order."""
    seen = []
    real = streams._scan
    monkeypatch.setattr(streams, "_scan", lambda path: (seen.append(os.path.basename(path)), real(path))[1])
    return seen


def station(path, address="https://example.org/a"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#EXTM3U\n#EXTINF:-1,S\n%s\n" % address, encoding="utf-8")


def listing(folder):
    """What Library.list_dir hands the page for this folder (names only)."""
    return [Entry(e.name, e.name, e.is_dir(), 0, 0.0, (e.name,), "" if e.is_dir() else e.path, e.path, False) for e in os.scandir(folder)]


def test_a_listing_that_was_read_already_is_not_read_again(tmp_path, scans):
    station(tmp_path / "Radio" / "NRK.m3u")
    (tmp_path / "Radio" / "NRK.png").write_bytes(b"p")
    books = tmp_path / "Books"
    books.mkdir()
    for n in range(4):
        (books / ("b%d.epub" % n)).write_bytes(b"x")
    station(books / "one.m3u")
    for folder, expected in ((tmp_path / "Radio", True), (books, False)):
        assert streams.is_stream_folder(str(folder), listing(folder)) is expected                   # the page's own listing
        assert streams.is_stream_folder(str(folder), list(os.scandir(folder))) is expected          # directory entries work too
        assert streams.is_stream_folder(str(folder)) is expected
    assert scans == ["Radio", "Books"]                                   # only the two calls that brought no listing
    assert streams.is_stream_folder(str(tmp_path / "Empty"), []) is False and scans == ["Radio", "Books"]


def test_a_folder_of_country_folders_looks_into_a_few_of_them_only(tmp_path, scans):
    for country in ("Norway", "Sweden", "Denmark", "Finland", "Iceland"):
        station(tmp_path / "Radio" / country / "One.m3u")
    assert streams.is_stream_folder(str(tmp_path / "Radio"), listing(tmp_path / "Radio"))
    assert scans == ["Denmark", "Finland", "Iceland"]                    # never Radio itself: its listing came with the call
    station(tmp_path / "IPTV" / "Europe" / "Norway" / "NRK1.m3u")       # a country folder and one more below it
    assert streams.is_stream_folder(str(tmp_path / "IPTV"), listing(tmp_path / "IPTV"))


def test_a_deep_tree_of_folders_is_not_followed_down(tmp_path, scans):
    deep = tmp_path / "Library"
    (deep / "a" / "b" / "c" / "d" / "e" / "f").mkdir(parents=True)
    station(deep / "a" / "b" / "c" / "d" / "e" / "f" / "far.m3u")
    assert streams.is_stream_folder(str(deep), listing(deep)) is False
    assert scans == ["a", "b"]                                           # as deep as a station grid reaches, and no further
    del scans[:]
    assert streams.is_stream_folder(str(deep)) is False and scans == ["Library", "a", "b"]


def test_the_count_for_the_home_page_is_kept_for_a_minute(tmp_path, scans, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(streams, "_clock", lambda: now[0])
    streams.forget_counts()
    station(tmp_path / "Radio" / "Norway" / "One.m3u")
    station(tmp_path / "Radio" / "Two.m3u")
    assert streams.count(str(tmp_path / "Radio")) == 2 and len(scans) == 2
    station(tmp_path / "Radio" / "Three.m3u")
    now[0] += 59
    assert streams.count(str(tmp_path / "Radio")) == 2 and len(scans) == 2          # the same answer, and the disk is left alone
    assert streams.count(str(tmp_path / "Radio" / "Norway")) == 1                   # another folder is another answer
    now[0] += 2
    assert streams.count(str(tmp_path / "Radio")) == 3                              # a minute later it is counted again
    station(tmp_path / "Radio" / "Four.m3u")
    streams.forget_counts()
    assert streams.count(str(tmp_path / "Radio")) == 4


def test_saving_stations_shows_in_the_count_at_once(up):  # noqa: F811
    me, app, folder = up
    for n, expected in ((1, "1 station<"), (2, "2 stations<")):
        r = me.post("/api/stations", content=json.dumps({"kind": "radio", "stations": [{"name": "S%d" % n, "url": "https://example.org/%d" % n}]}),
                    headers=JSON)
        assert r.status_code == 200
        assert expected in me.get("/", headers={"accept": "text/html"}).text


def test_collect_takes_the_times_from_the_listing(tmp_path, monkeypatch):
    station(tmp_path / "IPTV" / "Norway" / "NRK1.m3u", "https://example.org/nrk1/index.m3u8")
    station(tmp_path / "IPTV" / "Radio.m3u")

    def refuse(path):
        raise AssertionError("one more call per station for %s" % path)
    monkeypatch.setattr(os.path, "getmtime", refuse)
    found, countries, cut = streams.collect(str(tmp_path / "IPTV"))
    assert [(s["name"], s["kind"], s["country"]) for s in found] == [("NRK1", "tv", "Norway"), ("Radio", "radio", "")]
    assert countries == ["Norway"] and not cut


def test_a_changed_station_file_is_read_again(tmp_path):
    path = tmp_path / "One.m3u"
    station(path)
    assert streams.collect(str(tmp_path))[0][0]["kind"] == "radio"
    station(path, "https://example.org/tv/index.m3u8")
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert streams.collect(str(tmp_path))[0][0]["kind"] == "tv" and streams.kind_of(str(path)) == "tv"


# ---- card 49: one odd file name does not break a WebDAV listing or an OPDS feed -----------------------------------------------
class FakeLibrary:
    """Stands in for fs.Library: a folder whose names no Windows disk can hold."""

    def __init__(self, names, folders=()):
        self.entries = [Entry(n, n, True, 0, 0.0, (n,), "", n, False) for n in folders]
        self.entries += [Entry(n, n, False, 0, 0.0, (n,), n, n, False) for n in names]
        self.listed, self.stats = [], []

    def has_root(self, user, root):
        return True

    def list_dir(self, user, root, parts, all_files=False, stat=True):
        self.listed.append(stat)
        return list(self.entries)

    def with_stats(self, entries):
        self.stats.append([e.name for e in entries])
        return [Entry(e.name, e.display, e.is_dir, 0 if e.is_dir else 7, 86400.0, e.parts, e.path, e.src, True) for e in entries]


def dav(library):
    return webdav.Dav(SimpleNamespace(show_all_files=True), library, Urls(""))


def test_propfind_survives_names_that_are_not_text(tmp_path):
    library = FakeLibrary(["ok.epub", ODD, CONTROL], folders=["Dir\udcff"])
    answer = dav(library).propfind(None, ("books",), 1)
    assert answer.status_code == 207
    found = ET.fromstring(answer.body).findall(D + "response")           # it parses: no character XML refuses
    assert [r.find(D + "href").text for r in found] == ["/dav/books/", "/dav/books/ok.epub", "/dav/books/a%01b.epub"]
    assert [r.find(".//" + D + "displayname").text for r in found] == ["books", "ok.epub", "a�b.epub"]
    # a name that cannot be written as UTF-8 cannot be asked for either (the address would not lead back to it): it is left out


def test_propfind_reads_sizes_and_dates_only_for_what_it_answers(monkeypatch):
    library = FakeLibrary(["a.epub", "b.epub", "c.epub", ODD])
    monkeypatch.setattr(webdav, "MAX_ENTRIES", 2)
    answer = dav(library).propfind(None, ("books",), 1)
    assert library.listed == [False] and library.stats == [["a.epub", "b.epub"]]
    sizes = [e.text for e in ET.fromstring(answer.body).iter(D + "getcontentlength")]
    assert sizes == ["7", "7"]                                           # read for the two that are returned
    nothing = FakeLibrary(["a.epub"])
    assert dav(nothing).propfind(None, ("books",), 0).status_code == 207
    assert nothing.listed == [False] and nothing.stats == []             # Depth 0: the folder alone, no file is looked at


def test_propfind_cleans_the_name_in_the_address_it_was_asked_for():
    answer = dav(FakeLibrary([])).propfind(None, ("books", "odd\x02name"), 0)
    [only] = ET.fromstring(answer.body).findall(D + "response")
    assert only.find(D + "href").text == "/dav/books/odd%02name/" and only.find(".//" + D + "displayname").text == "odd�name"


def test_an_opds_feed_survives_names_and_tags_that_are_not_text():
    entries = [opds.nav_entry("Dir\udcff", "urn:pluteus:books:Dir\udcff", "/opds/books/Dir", 0.0),
               opds.book_entry(CONTROL, "urn:pluteus:books:" + CONTROL, "/dl/books/a%01b.epub", "application/epub+zip", 0.0, 7,
                               {"title": "T\x02itle", "authors": ("Au\udce9thor",), "language": "n\x00o", "year": "20\x0111", "series": "Sa\x0bga",
                                "series_index": "3"})]
    body = opds.build_feed(title="Li\x01brary\udce9", feed_id="urn:pluteus:books:\udce9", self_href="/opds/books", start_href="/opds",
                           updated=0.0, entries=entries)
    feed = ET.fromstring(body)                                           # it encodes, and it parses
    titles = [e.find(ATOM + "title").text for e in feed.findall(ATOM + "entry")]
    assert titles == ["Dir�", "T�itle"] and feed.find(ATOM + "title").text == "Li�brary�"
    assert feed.find(".//" + ATOM + "author/" + ATOM + "name").text == "Au�thor"
    assert feed.find(".//" + ATOM + "content").text == "Sa�ga #3"


def test_an_opds2_feed_survives_names_and_tags_that_are_not_text():
    nav = [opds2.nav_link("Dir\udcff", "/opds/books/Dir")]
    books = [opds2.publication(ODD, "urn:pluteus:books:" + ODD, "/dl/books/x.epub", "application/epub+zip", 0.0,
                               {"authors": ("Au\udce9thor",), "series": "Sa\udce9ga", "language": "no\udce9", "year": "2011\udce9"})]
    data = json.loads(opds2.feed(title="Li\udce9brary", self_href="/opds/books", start_href="/opds", navigation=nav, publications=books).decode("utf-8"))
    assert data["metadata"]["title"] == "Li�brary" and data["navigation"][0]["title"] == "Dir�"
    info = data["publications"][0]["metadata"]
    assert info["title"] == "caf�.epub" and info["identifier"] == "urn:pluteus:books:caf�.epub"
    assert info["author"] == [{"name": "Au�thor"}] and info["belongsTo"]["series"][0]["name"] == "Sa�ga"
    assert opds2.publication("Plain", "urn:x", "/dl/x", "application/pdf", 0.0)["metadata"]["title"] == "Plain"


def test_entries_whose_address_could_not_lead_back_are_left_out_of_a_feed():
    library = FakeLibrary(["ok.epub", ODD, CONTROL], folders=["Dir\udcff", "Fine"])
    assert [e.name for e in opds.linkable(library.entries)] == ["Fine", "ok.epub", CONTROL]
    assert opds.linkable([]) == []
    for e in opds.linkable(library.entries):
        Urls("").download("books", e.parts)                              # every address that is left can be built


@pytest.mark.skipif(os.name == "nt", reason="NTFS cannot hold these names; a Linux file system can")
def test_a_real_folder_with_such_names_still_answers(tmp_path):
    from conftest import basic
    from starlette.testclient import TestClient

    from pluteus.app import create_app
    from pluteus.auth import hash_password
    from pluteus.config import parse_config
    books = tmp_path / "books"
    books.mkdir()
    (books / "ok.epub").write_bytes(b"x")
    try:
        with open(os.path.join(os.fsencode(str(books)), b"caf\xe9.epub"), "wb") as f:
            f.write(b"x")
        with open(os.path.join(os.fsencode(str(books)), b"a\x01b.epub"), "wb") as f:
            f.write(b"x")
    except OSError:
        pytest.skip("this file system refuses such names")
    cfg = parse_config({"server": {"webdav": True}, "roots": [{"name": "books", "path": str(books)}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]}]})
    client = TestClient(create_app(cfg))
    answer = client.request("PROPFIND", "/dav/books/", headers={**basic("alice", "pw"), "Depth": "1"})
    assert answer.status_code == 207
    found = [r.find(D + "href").text for r in ET.fromstring(answer.content).findall(D + "response")]
    assert "/dav/books/ok.epub" in found and "/dav/books/a%01b.epub" in found and len(found) == 3


# ---- card 50: the Chromecast that is configured, not the first one found ------------------------------------------------------
class Cast:
    def __init__(self, host, fails=False):
        self.cast_info = SimpleNamespace(host=host, port=8009)
        self.waited, self.fails = None, fails

    def wait(self, timeout=None):
        self.waited = timeout
        if self.fails:
            raise RuntimeError("the device went away")


@pytest.fixture
def chromecasts(monkeypatch):
    """A stand-in `pychromecast` on a network with several Chromecasts: discovery returns all of them, the asked-for one not first."""
    module = types.ModuleType("pychromecast")
    module.casts, module.browsers = [Cast("192.168.1.60"), Cast("192.168.1.50"), Cast("192.168.1.70")], []

    def get_chromecasts(known_hosts=None, timeout=None, **kw):
        browser = SimpleNamespace(stopped=0)
        browser.stop_discovery = lambda: setattr(browser, "stopped", browser.stopped + 1)
        module.browsers.append(browser)
        return list(module.casts), browser
    module.get_chromecasts = get_chromecasts
    monkeypatch.setitem(sys.modules, "pychromecast", module)
    return module


def test_the_cast_at_the_configured_address_is_the_one_that_is_used(chromecasts):
    backend = castdevices.CastBackend()
    backend.connect(CastDevice("Kids TV", "192.168.1.50"))
    assert backend._casts["Kids TV"] is chromecasts.casts[1] and chromecasts.casts[1].waited == 15
    assert chromecasts.casts[0].waited is None and chromecasts.casts[2].waited is None        # the living room is left alone
    assert chromecasts.browsers[0].stopped == 0                           # kept while the device is in use
    backend.close()
    assert chromecasts.browsers[0].stopped == 1


def test_another_chromecast_is_never_taken_in_its_place(chromecasts):
    backend = castdevices.CastBackend()
    with pytest.raises(ConnectionError, match="no Chromecast answered at 192.168.1.99"):
        backend.connect(CastDevice("Den", "192.168.1.99"))
    assert not backend.connected("Den") and all(c.waited is None for c in chromecasts.casts)
    assert chromecasts.browsers[0].stopped == 1


def test_the_discovery_is_stopped_when_the_device_does_not_come_up(chromecasts):
    chromecasts.casts[1].fails = True
    backend = castdevices.CastBackend()
    for _ in range(3):                                                    # the manager tries again every 30 seconds
        with pytest.raises(RuntimeError):
            backend.connect(CastDevice("Kids TV", "192.168.1.50"))
    assert [b.stopped for b in chromecasts.browsers] == [1, 1, 1] and not backend.connected("Kids TV")
    assert backend._browsers == []                                        # nothing is kept for a device that is not connected


def test_a_device_listed_by_name_matches_the_address_the_name_stands_for(chromecasts, monkeypatch):
    monkeypatch.setattr(castdevices.socket, "getaddrinfo", lambda host, *a, **kw: (
        [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.70", 0))] if host == "tv.lan" else (_ for _ in ()).throw(OSError("unknown"))))
    backend = castdevices.CastBackend()
    backend.connect(CastDevice("Bedroom", "tv.lan"))
    assert backend._casts["Bedroom"] is chromecasts.casts[2]
    with pytest.raises(ConnectionError):
        backend.connect(CastDevice("Nowhere", "gone.lan"))


# ---- card 51 (a): Fetch missing logos keeps the file's own letters ------------------------------------------------------------
def test_naming_the_logo_in_a_file_keeps_its_encoding(up, monkeypatch):  # noqa: F811
    me, app, folder = up
    mine = folder / "alice" / "Radio"
    mine.mkdir(parents=True)
    old = b"#EXTM3U\n#EXTINF:-1,Caf\xe9 Radio\nhttps://example.org/a\n"                     # cp1252, as an old Windows program saved it
    new = "#EXTM3U\n#EXTINF:-1,Café Radio — Øst\nhttps://example.org/b\n".encode("utf-8")
    marked = b"\xef\xbb\xbf#EXTINF:-1,Marked\nhttps://example.org/c\n"                      # UTF-8 with a byte-order mark
    broken = b"#EXTM3U\n#EXTINF:-1,Odd \x81 \xc3\x28\nhttps://example.org/d\n"              # neither UTF-8 nor cp1252
    for name, raw in (("old.m3u", old), ("new.m3u", new), ("marked.m3u", marked), ("broken.m3u", broken)):
        (mine / name).write_bytes(raw)
    pic = png_bytes((48, 48))
    monkeypatch.setattr(fetchimage, "fetch_logo", lambda url: (pic, "png"))
    logo = "https://l.example.org/logo.png"
    r = me.post("/api/stations/logos", content=json.dumps({"kind": "radio", "items": [
        {"path": name, "logo": logo} for name in ("old.m3u", "new.m3u", "marked.m3u", "broken.m3u")]}), headers=JSON)
    assert r.status_code == 200 and r.json() == {"fetched": 4, "failed": 0, "skipped": 0}
    attribute = b' tvg-logo="https://l.example.org/logo.png"'
    assert (mine / "old.m3u").read_bytes() == old.replace(b"#EXTINF:-1", b"#EXTINF:-1" + attribute)       # the e-acute is still the byte it was
    assert (mine / "new.m3u").read_bytes() == new.replace(b"#EXTINF:-1", b"#EXTINF:-1" + attribute)
    assert (mine / "marked.m3u").read_bytes() == marked.replace(b"#EXTINF:-1", b"#EXTINF:-1" + attribute)
    assert (mine / "broken.m3u").read_bytes() == broken                   # not readable as text: the picture is saved, the file is left alone
    for name in ("old", "new", "marked", "broken"):
        assert (mine / (name + ".png")).read_bytes() == pic
        assert b"\xef\xbf\xbd" not in (mine / (name + ".m3u")).read_bytes()                # never a replacement mark
    assert [n for n, _ in radio.read_file(str(mine / "old.m3u"))] == ["Café Radio"]


def test_the_station_reader_and_the_logo_writer_read_a_file_the_same_way():
    assert radio.decode_text(b"\xef\xbb\xbfabc") == ("abc", "utf-8-sig") and radio.decode_text("é".encode("utf-8")) == ("é", "utf-8")
    assert radio.decode_text(b"caf\xe9") == ("café", "cp1252")
    assert radio.decode_text(b"\x81\xe9")[0] == "�é"           # for reading, an odd byte costs one letter
    with pytest.raises(UnicodeDecodeError):
        radio.decode_text(b"\x81\xe9", strict=True)                      # for writing back, it stops the rewrite


# ---- card 51 (b): a Shoutcast server that answers "ICY 200 OK" ----------------------------------------------------------------
def shoutcast(connection, request, stop):
    title = b"StreamTitle='Kaizers Orchestra - Ompa til du d\xc3\xb8r';"
    block = title + b"\x00" * (-len(title) % 16)
    head = b"ICY 200 OK\r\nicy-name: Old Radio\r\ncontent-type: audio/mpeg\r\n"
    if b"icy-metadata: 1" in request.lower():
        connection.sendall(head + b"icy-metaint: 16\r\n\r\n" + b"a" * 16 + bytes([len(block) // 16]) + block + b"b" * 4000)
    else:
        connection.sendall(head + b"\r\n" + b"ID3" + b"a" * 4000)


def test_a_shoutcast_status_line_is_taken_as_http(source):
    url = source(shoutcast)
    connection, response, kind = relay.open_stream(url + "/")
    try:
        assert response.status == 200 and kind == "audio/mpeg" and response.getheader("icy-name") == "Old Radio"
        data = b""
        while len(data) < 4003:
            chunk = relay.read_chunk(response)
            if not chunk:
                break
            data += chunk
        assert data == b"ID3" + b"a" * 4000
    finally:
        connection.close()
    assert relay.fetch_body(url + "/", 10000)[0] == b"ID3" + b"a" * 4000


def test_now_playing_reads_a_shoutcast_server_too(source):
    relay._cache.clear()
    url = source(shoutcast)
    assert relay.now_playing(url + "/") == {"text": "Kaizers Orchestra - Ompa til du dør", "artist": "Kaizers Orchestra", "title": "Ompa til du dør"}
    relay._cache.clear()


@pytest.mark.parametrize("line", [b"ICY banana OK\r\n\r\n", b"ICY\r\n\r\n", b"GARBAGE 200 OK\r\n\r\n", b"ICY 404 Not Found\r\n\r\n"])
def test_other_odd_status_lines_are_still_refused(source, line):
    with pytest.raises(relay.Refused):
        relay.open_stream(source(lambda c, request, stop: c.sendall(line)) + "/")


# ---- card 51 (c): the update check finds the newest release however many tags there are ---------------------------------------
def settle(checker):
    for _ in range(200):
        state = checker.status()
        if state["state"] != "checking":
            return state
        time.sleep(0.02)
    raise AssertionError("the check never finished")


def test_the_newest_release_is_asked_for_by_name():
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("/releases/latest"):
            return {"tag_name": "v0.9.30", "name": "anything"}
        if "/tags" in url:
            return [{"name": "v0.1.%d" % n} for n in range(100)]         # the first hundred tags are all old ones
        return []
    state = settle(UpdateChecker("o/r", "0.9.27", fetch))
    assert state["state"] == "update" and state["latest"] == "0.9.30"
    assert calls[0] == "https://api.github.com/repos/o/r/releases/latest" and not any("/tags" in c for c in calls)


def test_without_a_release_every_page_of_tags_is_read():
    calls = []
    pages = {1: ["v0.1.%d" % n for n in range(100)], 2: ["v0.2.%d" % n for n in range(100)], 3: ["v0.9.30", "v0.9.27", "junk"]}

    def fetch(url):
        calls.append(url)
        if url.endswith("/releases/latest"):
            raise OSError("404")                                         # a fork that only tags
        if "/tags" in url:
            number = int(url.rpartition("page=")[2])
            return [{"name": t} for t in pages.get(number, [])]
        return []
    state = settle(UpdateChecker("o/r", "0.9.27", fetch))
    assert state["state"] == "update" and state["latest"] == "0.9.30"
    assert [c for c in calls if "/tags" in c] == ["https://api.github.com/repos/o/r/tags?per_page=100&page=%d" % n for n in (1, 2, 3)]


def test_the_pages_of_tags_have_an_end():
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("/releases/latest"):
            return {"tag_name": "nightly"}                               # not a version: the tags decide
        if "/tags" in url:
            return [{"name": "v0.1.0"}] * 100                             # a source that never runs out
        return []
    state = settle(UpdateChecker("o/r", "0.9.27", fetch))
    assert state["state"] == "ok" and state["latest"] == "0.1.0"
    assert 1 < len([c for c in calls if "/tags" in c]) <= 20

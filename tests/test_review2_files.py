"""Code review of 2026-11-12, the file layer: cards 5, 6, 9 (logo fetch), 12 to 19, 22 (a, b), 29 and 75.

Every crafted file is built here, in pytest's tmp_path. No network: sockets and name look-ups are stubbed."""
import dataclasses
import errno
import io
import os
import socket
import struct
import threading
import time
import zipfile
import zlib

import pytest
from epub_factory import CONTAINER, make_epub

from pluteus import aboutfiles, cbz, fetchimage, markdown, mediainfo, meta, thumbs, uploads, ziputil
from pluteus import fs as fs_module
from pluteus.fs import Library, NotFound
from pluteus.urls import Urls


# ---- helpers --------------------------------------------------------------------------------------------------------------
def _zip(path, members, method=zipfile.ZIP_DEFLATED):
    with zipfile.ZipFile(str(path), "w", method) as z:
        for name, data in members:
            z.writestr(name, data)
    return str(path)


OPF = ('<?xml version="1.0" encoding="%s"?>%s<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
       '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">%s</metadata><manifest/><spine/></package>')


def _epub(path, opf, extra=(), method=zipfile.ZIP_DEFLATED):
    path.parent.mkdir(parents=True, exist_ok=True)
    return _zip(path, [("mimetype", "application/epub+zip"), ("META-INF/container.xml", CONTAINER % "content.opf"),
                       ("content.opf", opf)] + list(extra), method)


def _stored_as_deflate(path):
    """Damage an archive written with ZIP_STORED: every entry now claims to be deflated, so reading one raises zlib.error."""
    with open(path, "rb") as f:
        data = bytearray(f.read())
    for signature, offset in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):
        at = data.find(signature)
        while at >= 0:
            data[at + offset:at + offset + 2] = (8).to_bytes(2, "little")
            at = data.find(signature, at + 4)
    with open(path, "wb") as f:
        f.write(bytes(data))
    return path


def _damaged_epub(path):
    _epub(path, OPF % ("utf-8", "", "<dc:title>Broken</dc:title>"), method=zipfile.ZIP_STORED)
    _stored_as_deflate(str(path))
    with zipfile.ZipFile(str(path)) as z, pytest.raises(zlib.error):          # the crafted file really is the damage of card 12
        z.read("META-INF/container.xml")
    return str(path)


def _link(target, link):
    """A folder link: a junction on Windows (needs no special right), a symlink elsewhere."""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(str(target), str(link), target_is_directory=True)


@pytest.fixture(autouse=True)
def _fresh_caches():
    markdown.clear_cache() if hasattr(markdown, "clear_cache") else None
    mediainfo._folder_cache.clear()
    yield


# ---- card 5: a marker probe that fails closes the folder, and that answer is not kept ----------------------------------------
@pytest.mark.parametrize("marker, open_for", [(".adult", ("bob",)), (".private", ())])
def test_card5_a_failed_marker_probe_closes_the_folder_and_is_not_cached(cfg, library, monkeypatch, marker, open_for):
    lib = Library(cfg)
    real, broken = os.stat, [True]

    def stat(path, *args, **kw):
        if broken[0] and str(path).endswith(marker):
            raise OSError(errno.EIO, "the share dropped out")
        return real(path, *args, **kw)
    monkeypatch.setattr(fs_module.os, "stat", stat)
    for name in ("alice", "bob", "carol"):
        user = cfg.users[name]
        if name in open_for:
            assert lib.list_dir(user, "comics", ("Plain",), all_files=True)
        else:
            with pytest.raises(NotFound):
                lib.list_dir(user, "comics", ("Plain",), all_files=True)
    broken[0] = False                                                          # the share is back: nothing was remembered
    for name in ("alice", "bob", "carol"):
        assert lib.list_dir(cfg.users[name], "comics", ("Plain",), all_files=True)


def test_card5_an_absent_marker_is_still_absent_and_cached(cfg, library, monkeypatch):
    lib = Library(cfg)
    alice = cfg.users["alice"]
    assert lib.list_dir(alice, "comics", ("Plain",), all_files=True)
    calls = []
    real = os.stat
    monkeypatch.setattr(fs_module.os, "stat", lambda p, *a, **k: (calls.append(str(p)), real(p, *a, **k))[1])
    assert lib.list_dir(alice, "comics", ("Plain",), all_files=True)
    assert not [c for c in calls if c.endswith((".adult", ".private", ".hidden"))]


# ---- card 6: links -------------------------------------------------------------------------------------------------------
def test_card6_a_link_to_a_folder_below_an_adult_marker_is_closed_like_the_folder(cfg, library):
    comics = library / "comics"
    _link(comics / "Adult Pub" / "Naughty (2000)", comics / "Plain" / "New arrivals")
    lib = Library(cfg)
    alice, bob = cfg.users["alice"], cfg.users["bob"]
    assert "New arrivals" not in [e.name for e in lib.list_dir(alice, "comics", ("Plain",), all_files=True)]
    with pytest.raises(NotFound):
        lib.list_dir(alice, "comics", ("Plain", "New arrivals"))
    with pytest.raises(NotFound):
        lib.file_info(alice, "comics", ("Plain", "New arrivals", "n.cbz"))
    with pytest.raises(NotFound):
        lib.folder_path(alice, "comics", ("Plain", "New arrivals"))
    assert "New arrivals" in [e.name for e in lib.list_dir(bob, "comics", ("Plain",), all_files=True)]      # the adult user still gets in
    assert [e.name for e in lib.list_dir(bob, "comics", ("Plain", "New arrivals"))] == ["n.cbz"]
    assert lib.file_info(bob, "comics", ("Plain", "New arrivals", "n.cbz")).size == 1


def test_card6_a_link_inside_the_library_still_works(cfg, library):
    comics = library / "comics"
    _link(comics / "3 - Marvel (1961-2012)" / "Spider-Man (1963-)", comics / "Plain" / "Spidey")
    lib = Library(cfg)
    alice = cfg.users["alice"]
    assert "Spidey" in [e.name for e in lib.list_dir(alice, "comics", ("Plain",), all_files=True)]
    assert len(lib.list_dir(alice, "comics", ("Plain", "Spidey"))) == 4


def test_card6_a_link_that_leaves_the_root_is_not_listed(cfg, library):
    outside = library / "elsewhere"
    outside.mkdir()
    (outside / "x.cbz").write_bytes(b"x")
    _link(outside, library / "comics" / "Plain" / "Away")
    lib = Library(cfg)
    for name in ("alice", "bob"):
        assert "Away" not in [e.name for e in lib.list_dir(cfg.users[name], "comics", ("Plain",), all_files=True)]


def test_card6_a_linked_file_outside_the_root_is_not_listed_so_its_metadata_is_never_read(cfg, library):
    outside = make_epub(str(library / "elsewhere" / "Secret.epub"), title="Secret Title")
    try:
        os.symlink(outside, str(library / "books" / "Good Author" / "Linked.epub"))
    except (OSError, NotImplementedError):
        pytest.skip("this account may not make file links")
    lib = Library(cfg)
    entries = lib.list_dir(cfg.users["alice"], "books", ("Good Author",))
    assert "Linked.epub" not in [e.name for e in entries] and "Real Book.epub" in [e.name for e in entries]
    found = meta.MetaService(meta.MetaCache()).lookup_many(entries)
    assert not any(v.get("title") == "Secret Title" for v in found.values())


# ---- card 9: one deadline for the whole logo fetch ---------------------------------------------------------------------------
class _Clock:
    def __init__(self):
        self.now = 5000.0

    def __call__(self):
        return self.now


class _Raw(io.RawIOBase):
    def __init__(self, sock):
        self.sock = sock

    def readable(self):
        return True

    def readinto(self, b):
        return self.sock.recv_into(b)


class _DripSocket:
    """A server that answers one byte at a time, each byte taking `step` seconds of the (fake) clock."""
    made = 0

    def __init__(self, clock, reply, step):
        self.clock, self.reply, self.step, self.pos, self.timeouts = clock, reply, step, 0, []
        _DripSocket.made += 1

    def settimeout(self, value):
        self.timeouts.append(value)

    def connect(self, address):
        pass

    def sendall(self, data):
        pass

    def recv_into(self, buffer, nbytes=0, flags=0):
        self.clock.now += self.step
        if self.pos >= len(self.reply):
            return 0
        buffer[0] = self.reply[self.pos]
        self.pos += 1
        return 1

    def makefile(self, mode="rb", *args, **kw):
        return io.BufferedReader(_Raw(self))

    def close(self):
        pass


@pytest.fixture
def fake_net(monkeypatch):
    clock = _Clock()
    made = []
    reply = b"HTTP/1.1 200 OK\r\nX-Pad: " + b"a" * 200 + b"\r\n\r\nbody"

    def make(*args, **kw):
        made.append(_DripSocket(clock, reply, 5.0))
        return made[-1]
    monkeypatch.setattr(fetchimage.time, "monotonic", clock)
    monkeypatch.setattr(fetchimage.socket, "socket", make)
    monkeypatch.setattr(fetchimage, "_public_addresses", lambda host, port: [(socket.AF_INET, ("93.184.216.34", port))])
    return clock, made


def test_card9_slow_headers_end_at_the_overall_deadline(fake_net):
    clock, made = fake_net
    start = clock.now
    with pytest.raises(fetchimage.Refused):
        fetchimage.download("http://logo.example.org/logo.png")
    assert clock.now - start <= fetchimage.DEADLINE + 10.0            # not the 1,000 seconds the dripped headers would take
    timeouts = made[0].timeouts
    assert timeouts and all(0 < t <= fetchimage.TIMEOUT for t in timeouts)
    assert min(timeouts) < fetchimage.TIMEOUT                         # the time left, not a fresh allowance for every step


def test_card9_time_spent_on_the_name_counts(fake_net, monkeypatch):
    clock, made = fake_net

    def slow_name(host, port):
        clock.now += fetchimage.DEADLINE + 1
        return [(socket.AF_INET, ("93.184.216.34", port))]
    monkeypatch.setattr(fetchimage, "_public_addresses", slow_name)
    with pytest.raises(fetchimage.Refused):
        fetchimage.download("http://logo.example.org/logo.png")
    assert not made                                                   # nothing was connected to after the deadline


def test_card9_a_name_look_up_that_hangs_is_given_up(monkeypatch):
    release = threading.Event()

    def hang(*args, **kw):
        release.wait(8)
        raise OSError("no answer")
    monkeypatch.setattr(socket, "getaddrinfo", hang)
    monkeypatch.setattr(fetchimage, "DEADLINE", 0.3)
    began = time.perf_counter()
    try:
        assert fetchimage.fetch_logo("http://slow-name.example.org/logo.png") is None
        assert time.perf_counter() - began < 4
    finally:
        release.set()


# ---- card 75 ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("url", ["http://host.example.org:abc/x", "http://[::1/x", "http://host.example.org:99999/x", "http://[abc]/y"])
def test_card75_a_malformed_address_is_refused_not_raised(url):
    with pytest.raises(fetchimage.Refused):
        fetchimage._split(url)
    assert fetchimage.fetch_logo(url) is None


def test_card75_the_nat64_prefix_is_not_public(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, **kw: [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("64:ff9b::c0a8:101", port, 0, 0))])
    with pytest.raises(fetchimage.Refused):
        fetchimage._public_addresses("nat64.example.org", 443)


# ---- card 12: a damaged file never fails more than itself -------------------------------------------------------------------
def test_card12_a_damaged_epub_reads_as_no_metadata_and_no_cover(tmp_path):
    bad = _damaged_epub(tmp_path / "bad.epub")
    assert meta.read_epub_metadata(bad) is None
    assert thumbs.epub_cover(bad) is None


def test_card12_one_damaged_epub_does_not_fail_its_folder(client, library, alice):
    _damaged_epub(library / "books" / "Damaged" / "bad.epub")
    make_epub(str(library / "books" / "Damaged" / "good.epub"), title="Fine")
    assert client.get("/browse/books/Damaged", headers=alice).status_code == 200
    assert client.get("/opds/books/Damaged", headers=alice).status_code == 200


def test_card12_any_failure_is_cached_as_empty(tmp_path, monkeypatch):
    calls = []

    def boom(path):
        calls.append(path)
        raise TypeError("an error nobody listed")
    monkeypatch.setattr(meta, "read_epub_metadata", boom)
    service = meta.MetaService(meta.MetaCache())
    entry = fs_module.Entry("x.epub", "x.epub", False, 10, 1.0, ("x.epub",), str(tmp_path / "x.epub"))
    assert service.for_file(entry) == {} and service.lookup_many([entry]) == {entry.path: {}}
    assert len(calls) == 1


def test_card12_zip_and_comic_readers_answer_with_their_own_error(tmp_path):
    import lzma
    path = _zip(tmp_path / "a.zip", [("p1.png", b"<" * 500), ("p2.png", b"<" * 500)], zipfile.ZIP_STORED)
    _stored_as_deflate(path)
    with pytest.raises(ziputil.ZipError):
        ziputil.read_entry(path, 0, 10_000)
    with pytest.raises(ziputil.ZipError):
        ziputil.read_head(path, 0, 100)
    with pytest.raises(ziputil.ZipError):
        list(ziputil.stream_entry(path, 0))
    with pytest.raises(cbz.CbzError):
        cbz.read_page(path, "p1.png")
    for errors in (ziputil._ERRORS, cbz._ERRORS):
        assert zlib.error in errors and lzma.LZMAError in errors


def test_card12_stream_entry_of_a_file_that_is_no_zip_is_a_zip_error(tmp_path):
    path = tmp_path / "not.zip"
    path.write_bytes(b"this is no archive at all")
    with pytest.raises(ziputil.ZipError):
        list(ziputil.stream_entry(str(path), 0))


@pytest.mark.parametrize("text", ["nan", "inf", "-inf", "1e999", "NaN"])
def test_card12_odd_series_numbers_do_not_raise(tmp_path, text):
    meta._number(text)
    path = _epub(tmp_path / "n.epub", OPF % ("utf-8", "", '<dc:title>T</dc:title><meta name="calibre:series" content="Saga"/>'
                                             '<meta name="calibre:series_index" content="%s"/>' % text))
    assert meta.read_epub_metadata(path)["series"] == "Saga"
    assert meta._number("3.0") == "3" and meta._number("2.5") == "2.5" and meta._number("III") == "III"


# ---- card 14: EPUB metadata: no entities, capped lengths -------------------------------------------------------------------
BOMB = '<!DOCTYPE package [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-16-le", "utf-16-be"])
def test_card14_an_opf_that_declares_entities_is_refused(tmp_path, encoding):
    text = OPF % ("utf-16" if encoding.startswith("utf-16") else "utf-8", BOMB, "<dc:title>&b;</dc:title>")
    path = _epub(tmp_path / "bomb.epub", text.encode(encoding))
    assert meta.read_epub_metadata(path) is None
    assert thumbs.epub_cover(path) is None


def test_card14_a_doctype_alone_or_in_the_container_is_refused(tmp_path):
    plain = _epub(tmp_path / "d.epub", OPF % ("utf-8", "<!DOCTYPE package>", "<dc:title>T</dc:title>"))
    assert meta.read_epub_metadata(plain) is None
    container = (CONTAINER % "content.opf").replace("<container", "<!DOCTYPE container><container")
    path = _zip(tmp_path / "c.epub", [("META-INF/container.xml", container), ("content.opf", OPF % ("utf-8", "", "<dc:title>T</dc:title>"))])
    assert meta.read_epub_metadata(path) is None


@pytest.mark.parametrize("encoding, declared", [("utf-16", "utf-16"), ("utf-8-sig", "utf-8"), ("latin-1", "iso-8859-1"), ("utf-8", "utf-8")])
def test_card14_honest_files_in_other_encodings_still_read(tmp_path, encoding, declared):
    path = _epub(tmp_path / "ok.epub", (OPF % (declared, "", "<dc:title>Blåbær</dc:title>")).encode(encoding))
    assert meta.read_epub_metadata(path) == {"title": "Blåbær"}


def test_card14_lengths_are_capped(tmp_path):
    fields = ("<dc:title>%s</dc:title>" % ("T" * 5000) + "".join("<dc:creator>Author %d</dc:creator>" % i for i in range(50))
              + "<dc:creator>%s</dc:creator>" % ("N" * 5000) + "<dc:language>%s</dc:language>" % ("l" * 5000)
              + '<meta name="calibre:series" content="%s"/><meta name="calibre:series_index" content="%s"/>' % ("S" * 5000, "9" * 200))
    found = meta.read_epub_metadata(_epub(tmp_path / "long.epub", OPF % ("utf-8", "", fields)))
    assert len(found["title"]) == 300 and len(found["series"]) == 300 and len(found["language"]) == 300
    assert len(found["authors"]) == 20 and all(len(a) <= 300 for a in found["authors"])
    assert 0 < len(found["series_index"]) <= 20
    collection = ('<dc:title>T</dc:title><meta property="belongs-to-collection" id="c1">%s</meta>'
                  '<meta refines="#c1" property="group-position">%s</meta>' % ("C" * 5000, "x" * 5000))
    found = meta.read_epub_metadata(_epub(tmp_path / "coll.epub", OPF % ("utf-8", "", collection)))
    assert len(found["series"]) == 300 and len(found["series_index"]) <= 20


# ---- card 15: the .nfo entity check works on the text, whatever the encoding -------------------------------------------------
NFO = '<?xml version="1.0" encoding="%s"?>%s<artist><biography>%s</biography></artist>'


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-16-le", "utf-16-be"])
def test_card15_an_nfo_that_declares_entities_is_refused_in_any_encoding(encoding):
    declared = "utf-16" if encoding.startswith("utf-16") else "utf-8"
    raw = (NFO % (declared, '<!DOCTYPE artist [<!ENTITY e "EXPANDED">]>', "&e; text")).encode(encoding)
    assert aboutfiles.summary("artist.nfo", raw) == ""
    assert aboutfiles.summary("artist.nfo", (NFO % (declared, "<!DOCTYPE artist>", "A band.")).encode(encoding)) == ""


@pytest.mark.parametrize("encoding, declared", [("utf-16", "utf-16"), ("utf-8", "utf-8"), ("latin-1", "ISO-8859-1"), ("cp1252", "windows-1252")])
def test_card15_honest_nfo_files_still_read(encoding, declared):
    assert aboutfiles.summary("artist.nfo", (NFO % (declared, "", "Blå band.")).encode(encoding)) == "Blå band."


def test_card15_an_encoding_nobody_needs_is_refused():
    raw = b'<?xml version="1.0" encoding="cp037"?>' + "<artist><biography>text</biography></artist>".encode("cp037")
    assert aboutfiles.summary("artist.nfo", raw) == ""


# ---- card 16: picture sizes are checked by us, before anything is decoded ---------------------------------------------------
def _picture(mode, size, kind, **options):
    from PIL import Image
    out = io.BytesIO()
    Image.new(mode, size).save(out, kind, **options)
    return out.getvalue()


def test_card16_a_logo_with_too_many_pixels_is_refused_before_it_is_decoded():
    pytest.importorskip("PIL")
    data = _picture("1", (7000, 7000), "PNG")                         # 49 megapixels in a few kilobytes
    assert len(data) < 200_000
    with pytest.raises(fetchimage.Refused):
        fetchimage.small_picture(data)


def test_card16_a_progressive_jpeg_gets_the_small_limit():
    pytest.importorskip("PIL")
    size = (5000, 4100)                                               # 20.5 megapixels
    assert thumbs.scaled(_picture("L", size, "JPEG", quality=20, progressive=True), 96) is False
    assert thumbs.scaled(_picture("L", size, "JPEG", quality=20), 96)             # a plain JPEG of that size is decoded reduced


def test_card16_the_process_wide_pillow_limit_is_left_alone(monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 89_000_001)
    assert thumbs.scaled(_picture("RGB", (300, 200), "JPEG"), 96)
    assert fetchimage.small_picture(_picture("RGB", (300, 200), "PNG"))
    assert Image.MAX_IMAGE_PIXELS == 89_000_001


# ---- card 17: the size of a zip's file list is checked before it is read -----------------------------------------------------
@pytest.fixture
def no_listing(monkeypatch):
    """Counts how often zipfile reads a central directory."""
    calls = []
    real = zipfile.ZipFile._RealGetContents
    monkeypatch.setattr(zipfile.ZipFile, "_RealGetContents", lambda self: (calls.append(1), real(self))[1])
    return calls


def test_card17_an_archive_declaring_too_many_entries_is_refused_unread(tmp_path, no_listing):
    path = _zip(tmp_path / "many.cbz", (("%d.png" % i, b"") for i in range(ziputil.MAX_ENTRIES + 1)), zipfile.ZIP_STORED)
    del no_listing[:]
    with pytest.raises(ziputil.ZipError):
        ziputil.list_entries(path)
    with pytest.raises(ziputil.ZipError):
        ziputil.entry_facts(path, 0)
    with pytest.raises(cbz.CbzError):
        cbz.list_pages(path)
    assert meta.read_epub_metadata(path) is None
    assert not no_listing


def test_card17_a_file_list_that_is_too_big_is_refused(tmp_path, monkeypatch, no_listing):
    path = _zip(tmp_path / "wide.zip", (("entry-%03d.txt" % i, b"x") for i in range(50)))
    assert ziputil.list_entries(path)[1] == 50
    del no_listing[:]
    monkeypatch.setattr(ziputil, "MAX_DIRECTORY_BYTES", 1000)
    with pytest.raises(ziputil.ZipError):
        ziputil.list_entries(path)
    assert not no_listing


def test_card17_an_archive_that_lies_about_its_count_is_still_refused(tmp_path, monkeypatch, no_listing):
    path = _zip(tmp_path / "liar.zip", (("entry-%03d.txt" % i, b"") for i in range(300)), zipfile.ZIP_STORED)
    with open(path, "rb") as f:
        data = bytearray(f.read())
    at = data.rfind(b"PK\x05\x06")
    data[at + 8:at + 12] = struct.pack("<2H", 5, 5)                    # "five entries"
    with open(path, "wb") as f:
        f.write(bytes(data))
    monkeypatch.setattr(ziputil, "MAX_ENTRIES", 100)
    del no_listing[:]
    with pytest.raises(ziputil.ZipError):
        ziputil.list_entries(path)
    assert not no_listing


def test_card17_every_zip_reader_goes_through_the_check(tmp_path, monkeypatch):
    opf = OPF % ("utf-8", "", "<dc:title>T</dc:title><dc:description>About this book.</dc:description>")
    book = _epub(tmp_path / "b.epub", opf, extra=[("a.png", b"x"), ("b.png", b"x")])
    comic = _zip(tmp_path / "c.cbz", [("%d.png" % i, b"x") for i in range(5)])
    assert aboutfiles.from_book(book, "b.epub") == "About this book."
    assert meta.read_epub_metadata(book) == {"title": "T"}
    assert len(cbz.list_pages(comic)) == 5 and cbz.read_page(comic, "1.png") == b"x"
    assert ziputil.read_entry(comic, 0, 10) == b"x" and ziputil.read_head(comic, 0, 10) == (b"x", False)
    assert list(ziputil.stream_entry(comic, 0)) == [b"x"]
    monkeypatch.setattr(ziputil, "MAX_ENTRIES", 2)
    assert aboutfiles.from_book(book, "b.epub") == ""
    assert meta.read_epub_metadata(book) is None and thumbs.epub_cover(book) is None
    for call in (lambda: cbz.list_pages(comic), lambda: cbz.read_page(comic, "1.png")):
        with pytest.raises(cbz.CbzError):
            call()
    for call in (lambda: ziputil.list_entries(comic), lambda: ziputil.entry_facts(comic, 0), lambda: ziputil.read_entry(comic, 0, 10),
                 lambda: ziputil.read_head(comic, 0, 10), lambda: list(ziputil.stream_entry(comic, 0))):
        with pytest.raises(ziputil.ZipError):
            call()


# ---- cards 18 and 19: the Matroska and MP4 readers ---------------------------------------------------------------------------
def _el(ident, payload=b""):
    """One EBML element with an eight-byte size field."""
    return ident.to_bytes((ident.bit_length() + 7) // 8, "big") + b"\x01" + len(payload).to_bytes(7, "big") + payload


def _mkv(path, *segment_children):
    path.write_bytes(_el(0x1A45DFA3) + _el(0x18538067, b"".join(segment_children)))
    return str(path)


INFO, SCALE, DURATION, TITLE, VOID = 0x1549A966, 0x2AD7B1, 0x4489, 0x7BA9, 0xEC
ATTACHMENTS, ATTACHED, FILE_NAME, FILE_DATA = 0x1941A469, 0x61A7, 0x466E, 0x465C


def test_card18_a_huge_scale_element_does_not_raise(tmp_path):
    path = _mkv(tmp_path / "a.mkv", _el(INFO, _el(TITLE, b"Film") + _el(SCALE, b"\xff" * 200) + _el(DURATION, struct.pack(">d", 1000.0))))
    found = mediainfo.read_matroska(path)
    assert found["title"] == "Film" and found.get("duration") == 1.0          # the absurd scale is ignored, the usual one stands
    assert mediainfo.read_info(path)["title"] == "Film"


@pytest.mark.parametrize("value", [float("inf"), float("nan"), 1e308])
def test_card18_a_duration_that_is_not_a_finite_number_is_left_out(tmp_path, value):
    path = _mkv(tmp_path / "b.mkv", _el(INFO, _el(SCALE, (10 ** 18).to_bytes(8, "big")) + _el(DURATION, struct.pack(">d", value)) + _el(TITLE, b"Film")))
    found = mediainfo.read_matroska(path)
    assert "duration" not in found and found["title"] == "Film"


def test_card18_an_ordinary_file_still_gives_its_duration(tmp_path):
    path = _mkv(tmp_path / "c.mkv", _el(INFO, _el(SCALE, (1_000_000).to_bytes(3, "big")) + _el(DURATION, struct.pack(">f", 90_000.0))))
    assert mediainfo.read_matroska(path)["duration"] == 90.0


def test_card19_the_walk_without_an_index_is_short(tmp_path):
    path = _mkv(tmp_path / "walk.mkv", _el(VOID) * 600, _el(INFO, _el(TITLE, b"Far away")))
    assert mediainfo._MAX_WALK <= 500
    assert "title" not in mediainfo.read_matroska(path)
    near = _mkv(tmp_path / "near.mkv", _el(VOID) * 20, _el(INFO, _el(TITLE, b"Near")))
    assert mediainfo.read_matroska(near)["title"] == "Near"


def test_card19_an_attachment_stuffed_with_tiny_elements_is_not_walked_to_the_end(tmp_path):
    cover = _el(FILE_NAME, b"cover.jpg") + _el(FILE_DATA, b"\xff\xd8\xff" + b"0" * 50)
    stuffed = _mkv(tmp_path / "s.mkv", _el(ATTACHMENTS, _el(ATTACHED, _el(VOID) * 1000 + cover)))
    assert not mediainfo.read_matroska(stuffed).get("has_cover")
    honest = _mkv(tmp_path / "h.mkv", _el(ATTACHMENTS, _el(ATTACHED, cover)))
    assert mediainfo.read_matroska(honest, cover=True)["cover"].startswith(b"\xff\xd8\xff")


def _atom(kind, payload=b""):
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


CHAPTERS = _atom(b"moov", _atom(b"udta", _atom(b"chpl", b"\x00" * 4 + bytes([1]) + (0).to_bytes(8, "big") + bytes([5]) + b"Intro")))


def test_card19_mp4_chapters_stop_after_many_atoms_and_after_moov(tmp_path):
    plain = tmp_path / "ok.m4b"
    plain.write_bytes(_atom(b"ftyp", b"M4B ") + CHAPTERS)
    assert mediainfo.read_chapters(str(plain)) == [(0.0, "Intro")]
    many = tmp_path / "many.m4b"
    many.write_bytes(_atom(b"ftyp", b"M4B ") + _atom(b"free") * 3000 + CHAPTERS)
    assert mediainfo.read_chapters(str(many)) == []
    second = tmp_path / "second.m4b"
    second.write_bytes(_atom(b"ftyp", b"M4B ") + _atom(b"moov", _atom(b"udta")) + CHAPTERS)
    assert mediainfo.read_chapters(str(second)) == []


# ---- card 22: leftovers and temp names -------------------------------------------------------------------------------------
def test_card22_old_upload_leftovers_go_when_the_folder_is_first_used(cfg, tmp_path):
    base = tmp_path / "up"
    mine = base / "alice"
    (mine / "sub").mkdir(parents=True)
    old = time.time() - 2 * 24 * 3600
    stale = [mine / ".up-0123456789abcdef.part", mine / "sub" / ".up-fedcba9876543210.part"]
    kept = [mine / ".up-00000000000000aa.part", mine / "holiday.part", mine / "sub" / ".up-notes.part", mine / "sub" / "song.mp3"]
    for path in stale + kept:
        path.write_bytes(b"left over")
    for path in stale + kept[1:]:
        os.utime(str(path), (old, old))                                # kept[0] is a fresh upload still arriving
    lib = Library(dataclasses.replace(cfg, uploads_dir=str(base)))
    alice = cfg.users["alice"]
    lib.upload_base(alice)
    assert not any(p.exists() for p in stale) and all(p.exists() for p in kept)
    again = mine / ".up-1111111111111111.part"
    again.write_bytes(b"x")
    os.utime(str(again), (old, old))
    lib.upload_base(alice)
    lib.list_dir(alice, "uploads", ())
    assert again.exists()                                              # once per process: later requests do not walk the folder again
    assert uploads.sweep_stale_parts(str(mine)) == 1 and not again.exists()


def test_card22_thumbnail_temp_names_are_random_and_an_empty_file_is_a_miss(tmp_path, monkeypatch):
    cache = thumbs.ThumbCache(str(tmp_path))
    temps = []
    real = os.replace
    monkeypatch.setattr(thumbs.os, "replace", lambda src, dst: (temps.append(src), real(src, dst))[1])
    cache.put("key", b"picture")
    cache.put("key", b"picture")
    assert len(set(temps)) == 2 and cache.get("key") == b"picture"
    assert os.listdir(cache.dir) == [os.path.basename(cache._file("key"))]             # no temp file left behind
    with open(cache._file("empty"), "wb"):
        pass
    assert cache.get("empty") is None
    cache.put("nothing", b"")
    assert not os.path.exists(cache._file("nothing"))


# ---- card 29: the folder picture -------------------------------------------------------------------------------------------
def test_card29_one_listing_finds_the_folder_picture_in_any_letter_case(tmp_path, monkeypatch):
    album = tmp_path / "Album"
    album.mkdir()
    (album / "FOLDER.JPG").write_bytes(b"\xff\xd8\xff")
    (album / "front.png").write_bytes(b"x")
    (album / "Cover.webp").mkdir()                                      # a folder with a cover's name is not a picture
    probes = []
    real = os.path.isfile
    monkeypatch.setattr(mediainfo.os.path, "isfile", lambda p: (probes.append(p), real(p))[1])
    found = mediainfo.folder_cover(str(album))
    assert os.path.basename(found) == "FOLDER.JPG" and not probes        # "folder" comes before "front" in the order of names
    assert mediainfo.read_folder_cover(str(album)) is not None


def test_card29_a_folder_without_a_picture_is_remembered_as_long_as_one_with(tmp_path, monkeypatch):
    album = tmp_path / "Bare"
    album.mkdir()
    (album / "01 song.mp3").write_bytes(b"x")
    old = time.time() - 3600
    os.utime(str(album), (old, old))
    scans = []
    real = os.scandir
    monkeypatch.setattr(mediainfo.os, "scandir", lambda p: (scans.append(p), real(p))[1])
    clock = [time.monotonic()]
    monkeypatch.setattr(mediainfo.time, "monotonic", lambda: clock[0])
    assert mediainfo.folder_cover(str(album)) == ""
    assert len(scans) == 1
    clock[0] += 120                                                     # two minutes later: still remembered, the folder has not changed
    assert mediainfo.folder_cover(str(album)) == "" and len(scans) == 1
    (album / "cover.jpg").write_bytes(b"\xff\xd8\xff")                  # a new file changes the folder's date: seen at once
    assert os.path.basename(mediainfo.folder_cover(str(album))) == "cover.jpg"


# ---- card 13: Markdown -----------------------------------------------------------------------------------------------------
def _render(text, releases=None):
    return markdown.render(text, Urls(""), "comics", (), releases=releases)


@pytest.fixture
def mistune_calls(monkeypatch):
    calls = []
    real = markdown.mistune.create_markdown
    monkeypatch.setattr(markdown.mistune, "create_markdown", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    return calls


def test_card13_a_text_is_rendered_once_and_then_served_from_the_cache(mistune_calls):
    text = "# Title\n\nSome *words* and a [link](other.md).\n"
    first = _render(text)
    assert "<em>words</em>" in first and _render(text) == first and len(mistune_calls) == 1
    full = markdown.render_full(text, Urls(""), "comics", ())
    again = markdown.render_full(text, Urls(""), "comics", ())
    assert full.html == again.html and full.title == "Title" and len(mistune_calls) == 2
    again.meta["changed"] = True
    assert "changed" not in markdown.render_full(text, Urls(""), "comics", ()).meta
    assert "/books/" in markdown.render(text, Urls(""), "books", ()) and len(mistune_calls) == 3      # another library: other links


def test_card13_release_badges_are_filled_in_on_every_call_from_one_rendering(mistune_calls):
    text = "## Albums\n\n- Speak & Spell (1981)\n- Violator\n"
    have = {"speak and spell": "[1981] Speak & Spell"}
    first = _render(text, releases=have.get)
    assert first.count("pl-have") == 1 and first.count("pl-missing") == 1 and "[1981] Speak &amp; Spell" in first
    have["violator"] = "Violator <1990>"
    second = _render(text, releases=have.get)
    assert second.count("pl-have") == 2 and "Violator &lt;1990&gt;" in second and len(mistune_calls) == 1
    assert "pl-have" not in _render(text) and "\x00" not in first + second + _render(text)
    forged = _render("## Albums\n\n- Violator \x00pl-rel:nothing\x00 text\n", releases=have.get)
    assert "\x00" not in forged and "pl-rel:nothing" in forged and forged.count("pl-missing") == 1      # a marker in the file is just text


def test_card13_a_text_that_takes_too_long_is_shown_as_plain_text(monkeypatch):
    monkeypatch.setattr(markdown, "RENDER_SECONDS", 0.3)

    def endless_markdown(*args, **kwargs):
        """A parser that would run for ten seconds of plain Python calls. (A real slow text, such as 8000 times "[a](", is not slow
        on every Python: 3.15's regular expressions finish it at once. A stand-in makes the test mean the same everywhere.)"""
        def parse(body):
            def step():
                return None
            end = time.monotonic() + 10
            while time.monotonic() < end:
                step()
            return "<p>finished</p>"
        return parse
    monkeypatch.setattr(markdown.mistune, "create_markdown", endless_markdown)
    text = "[a](" * 50 + "<b>&"
    began = time.perf_counter()
    shown = _render(text)
    assert time.perf_counter() - began < 4                               # not the ten seconds it takes to finish
    assert shown.startswith("<pre") and "[a]([a](" in shown and "&lt;b&gt;&amp;" in shown and "<b>" not in shown
    began = time.perf_counter()
    assert _render(text) == shown and time.perf_counter() - began < 0.2  # and the answer is remembered
    with pytest.raises(markdown.TooLarge):                               # a whole page: the plain text viewer takes over
        markdown.render_full(text, Urls(""), "comics", ())


def test_card13_only_one_text_is_rendered_at_a_time(monkeypatch):
    monkeypatch.setattr(markdown, "RENDER_SECONDS", 0.2)
    assert markdown._RENDER.acquire(timeout=5)
    try:
        busy = _render("# Waiting\n\n*text*")
    finally:
        markdown._RENDER.release()
    assert busy.startswith("<pre") and "*text*" in busy
    assert "<em>text</em>" in _render("# Waiting\n\n*text*")             # "busy" was not remembered


def test_card13_long_lines_do_not_stall_the_headings_and_release_keys():
    began = time.perf_counter()
    assert markdown.release_key("(" * 60_000 + " Speak & Spell") is not None
    assert markdown.release_key("Speak & Spell (1981)") == markdown.release_key("[1981] - Speak And Spell") == "speak and spell"
    html = _render("# " + "( " * 30_000 + "\n\n## Studio  Albums (remastered)\n\n- " + "a " + " " * 20_000 + "- b\n",
                   releases=lambda key: None)
    assert time.perf_counter() - began < 3
    assert "<h1>" in html and "pl-missing" in html


def test_card13_the_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(markdown, "CACHE_ITEMS", 5)
    for i in range(20):
        _render("text number %d" % i)
    assert len(markdown._cache) <= 5
    assert "number 19" in _render("text number 19")


def test_card6_a_junction_is_told_apart_on_python_3_11_by_its_reparse_tag(monkeypatch):
    """Python 3.12 has DirEntry.is_junction(); 3.11 has not, and a junction there has to be told by the reparse tag of the listing."""
    from pluteus import fs

    class Stat:
        def __init__(self, tag):
            self.st_reparse_tag = tag

    class Old311Entry:                                    # a DirEntry as 3.11 has it: no is_junction
        def __init__(self, tag, symlink=False):
            self._tag, self._symlink = tag, symlink

        def is_symlink(self):
            return self._symlink

        def stat(self, follow_symlinks=True):
            assert follow_symlinks is False               # the link itself, never what it leads to
            return Stat(self._tag)

    monkeypatch.setattr(fs, "_NT", True)
    assert fs._is_link(Old311Entry(0xA0000003)) is True        # a junction
    assert fs._is_link(Old311Entry(0)) is False                                            # a folder
    assert fs._is_link(Old311Entry(0x9000001A)) is False                                   # a OneDrive placeholder is not a link
    assert fs._is_link(Old311Entry(0, symlink=True)) is True
    monkeypatch.setattr(fs, "_NT", False)
    assert fs._is_link(Old311Entry(0xA0000003)) is False       # elsewhere there are no junctions

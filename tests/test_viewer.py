"""The universal viewer: file kinds, safe inline serving (/raw), downloads of any type, and the image/text/media/info pages."""
import dataclasses
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pluteus.icons import OUTLINE_SPRITE
from cbz_factory import make_cbz, png
from starlette.testclient import TestClient
from test_login_web import HTML, login, make

from pluteus import kinds, textview
from pluteus.app import create_app
from pluteus.config import ConfigError, load_config, parse_config

STATIC = Path(__file__).resolve().parent.parent / "pluteus" / "static"
RED, GREEN, BLUE = png((255, 0, 0)), png((0, 255, 0)), png((0, 0, 255))
XSS = "<script>alert('x')</script> & <b>bold</b>"


@pytest.fixture
def docs(library):
    d = library / "books" / "Docs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "a.png").write_bytes(RED)
    (d / "b.png").write_bytes(GREEN)
    (d / "c10.png").write_bytes(BLUE)
    (d / "c2.png").write_bytes(BLUE)
    (d / "pic.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", encoding="utf-8")
    (d / "note.txt").write_bytes(b"hello\nworld\n")                                   # exact bytes: no CRLF translation
    (d / "evil.html").write_text("<html><script>alert(1)</script>" + XSS + "</html>", encoding="utf-8")
    (d / "evil.txt").write_text(XSS, encoding="utf-8")
    (d / "latin.txt").write_bytes("café crème".encode("cp1252"))
    (d / "wide.txt").write_bytes("héllo wörld".encode("utf-16"))
    (d / "empty.txt").write_bytes(b"")
    (d / "README").write_text("plain text without any extension\n", encoding="utf-8")
    (d / "data.xyz").write_text("some unknown extension but plainly text\n", encoding="utf-8")
    (d / "blob.bin").write_bytes(b"\x00\x01\x02\x03" * 100 + b"\xff\xfe")
    (d / "binary.log").write_bytes(b"log\x00\x00\x00\x00 not really text")
    (d / "report.docx").write_bytes(b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + bytes(range(256)) * 2 + b" fake word document")
    (d / "song1.mp3").write_bytes(b"ID3" + b"\x00" * 200)
    (d / "song2.mp3").write_bytes(b"ID3" + b"\x01" * 200)
    (d / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200)
    (d / "doc.pdf").write_bytes(b"%PDF-1.4 fake")
    (d / "disguised.png").write_text("<html><script>alert(1)</script></html>", encoding="utf-8")
    (d / "big.txt").write_bytes(b"line of text\n" * 4000)                              # 52,000 bytes
    make_cbz(d / "x.cbz", [("p1.png", RED)])
    return d


@pytest.fixture
def signed_in(cfg, tmp_path, docs):
    client, app = make(cfg, tmp_path)
    login(client)
    return client, app


def d(name):
    return "Docs/" + name.replace(" ", "%20")


# ---- kinds ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name,kind", [
    ("a.epub", "epub"), ("A.CBZ", "comic"), ("x.pdf", "pdf"), ("p.JPG", "image"), ("p.svg", "image"), ("s.mp3", "audio"),
    ("v.mkv", "video"), ("n.txt", "text"), ("n.py", "text"), ("page.html", "text"), ("r.docx", "other"),
    ("README", "other"), ("archive.tar.gz", "other"), (".hidden", "other")])
def test_kind_of(name, kind):
    assert kinds.kind_of(name) == kind


def test_nothing_scriptable_is_ever_served_inline():
    for ext in (".html", ".htm", ".xhtml", ".xml", ".js", ".mjs", ".css", ".php", ".exe", ".txt", ".json"):
        assert ext not in kinds.INLINE_MIME, ext
    assert ".svg" in kinds.SANDBOXED and kinds.INLINE_MIME[".svg"] == "image/svg+xml"
    assert all(m.split("/")[0] in ("image", "audio", "video") or m == "application/pdf" for m in kinds.INLINE_MIME.values())


# ---- text decoding -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,text,enc", [
    ("héllo".encode("utf-8"), "héllo", "utf-8"),
    (b"\xef\xbb\xbfhello", "hello", "utf-8-sig"),
    ("héllo".encode("utf-16"), "héllo", "utf-16"),
    ("héllo".encode("utf-16-be"), None, None),                                    # no BOM: not guessed as UTF-16
    ("café".encode("cp1252"), "café", "windows-1252"),
    (b"caf\xe9 \x81", None, "latin-1"),                                            # 0x81 is undefined in cp1252
    (b"plain ascii", "plain ascii", "utf-8")])
def test_decode(raw, text, enc):
    got, encoding = textview.decode(raw)
    if text is not None:
        assert got == text
    if enc is not None:
        assert encoding == enc


def test_decode_survives_a_cut_inside_a_multibyte_character():
    raw = ("é" * 10).encode("utf-8")[:-1]                                           # cut in the middle of the last é
    text, enc = textview.decode(raw, truncated=True)
    assert text == "é" * 9 and enc == "utf-8"


def test_forced_decode_never_fails():
    text, enc = textview.decode(b"ok \xff\xfe\x00 bytes", force=True)
    assert "ok" in text and "�" in text and "damaged" in enc


@pytest.mark.parametrize("raw,binary", [
    (b"", False), (b"just text\nwith lines\n", False), (b"a\x00b", True), (bytes(range(1, 9)) * 50, True),
    ("héllo wörld".encode("utf-16"), False), (b"tabs\tand\r\nnewlines\x0c are fine", False)])
def test_looks_binary(raw, binary):
    assert textview.looks_binary(raw) is binary


# ---- the listing and downloads -----------------------------------------------------------------------------------------
def test_listing_shows_every_file_with_a_view_or_read_link(cfg, tmp_path, docs):
    client, _ = make(cfg, tmp_path, page_size=100)
    login(client)
    html = client.get("/browse/books/Docs", headers=HTML).text
    for name in ("report.docx", "blob.bin", "README", "evil.html", "song1.mp3"):
        assert name in html, name
    assert "href='/view/books/Docs/report.docx'" in html and "href='/read/books/Docs/x.cbz'" in html


def test_any_file_downloads_as_an_attachment_never_in_our_origin(signed_in):
    client, _ = signed_in
    for name in ("evil.html", "pic.svg", "report.docx", "blob.bin", "README"):
        r = client.get("/dl/books/" + d(name))
        assert r.status_code == 200, name
        assert r.headers["content-disposition"].startswith("attachment"), name
        assert r.headers["x-content-type-options"] == "nosniff"


def test_opds_still_lists_only_books(signed_in):
    client, _ = signed_in
    feed = client.get("/opds/books/Docs").text
    assert "x.cbz" in feed or "x</title>" in feed
    for name in ("report.docx", "note.txt", "blob.bin", "song1.mp3"):
        assert name.split(".")[0] + "</title>" not in feed and name not in feed, name


# ---- /raw: the only inline serving -------------------------------------------------------------------------------------------
def test_raw_serves_safe_types_with_a_type_we_state(signed_in):
    client, _ = signed_in
    for name, mime in (("a.png", "image/png"), ("song1.mp3", "audio/mpeg"), ("clip.mp4", "video/mp4"),
                       ("doc.pdf", "application/pdf"), ("pic.svg", "image/svg+xml")):
        r = client.get("/raw/books/" + d(name))
        assert r.status_code == 200 and r.headers["content-type"].startswith(mime), name
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "content-disposition" not in r.headers                               # displayed, not saved
    assert client.get("/raw/books/" + d("a.png")).content == RED


def test_raw_svg_is_sandboxed_so_its_script_cannot_touch_the_site(signed_in):
    client, _ = signed_in
    csp = client.get("/raw/books/" + d("pic.svg")).headers["content-security-policy"]
    assert csp.startswith("sandbox") and "default-src 'none'" in csp


def test_a_file_cannot_choose_its_own_type(signed_in):
    client, _ = signed_in
    r = client.get("/raw/books/" + d("disguised.png"))                              # HTML pretending to be a PNG
    assert r.headers["content-type"] == "image/png" and r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("name", ["evil.html", "note.txt", "report.docx", "blob.bin", "README", "x.cbz"])
def test_raw_refuses_everything_that_is_not_a_safe_inline_type(signed_in, name):
    client, _ = signed_in
    assert client.get("/raw/books/" + d(name)).status_code == 404


def test_raw_supports_range_requests_for_media(signed_in):
    client, _ = signed_in
    r = client.get("/raw/books/" + d("song1.mp3"), headers={"range": "bytes=0-2"})
    assert r.status_code == 206 and r.content == b"ID3" and r.headers["content-range"] == "bytes 0-2/203"


def test_raw_needs_a_login_and_respects_hidden_folders(signed_in, library):
    client, app = signed_in
    anon = TestClient(app, follow_redirects=False)
    r = anon.get("/raw/books/" + d("a.png"))
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    (library / "comics" / "Adult Pub" / "x.png").write_bytes(RED)
    assert client.get("/raw/comics/Adult%20Pub/x.png").status_code == 404           # alice may not see it
    assert client.get("/raw/books/%2e%2e/%2e%2e/secret.txt").status_code in (400, 404)


# ---- the image viewer ----------------------------------------------------------------------------------------------------
def test_image_viewer_steps_through_the_images_of_the_folder_only(signed_in):
    client, _ = signed_in
    html = client.get("/view/books/" + d("b.png"), headers=HTML).text
    assert "<img class='pl-page' id='page' src='/raw/books/Docs/b.png'" in html
    assert "href='/view/books/Docs/a.png'" in html                                  # previous: natural order of images
    assert "href='/view/books/Docs/c2.png'" in html                                 # next: c2 before c10, and not note.txt
    assert "2 / 5" in html or "2 / 6" in html
    assert "data-next-raw='/raw/books/Docs/c2.png'" in html
    assert "viewer-image.js" in html and "script-src 'self'" in client.get("/view/books/" + d("b.png"), headers=HTML).headers["content-security-policy"]


def test_first_and_last_image_have_a_disabled_end(signed_in):
    client, _ = signed_in
    first = client.get("/view/books/" + d("a.png"), headers=HTML).text
    assert "id='prev' type='button' disabled" in first and "id='tap-prev'" not in first
    last = client.get("/view/books/" + d("pic.svg"), headers=HTML).text                    # last image in natural order
    assert "id='next' type='button' disabled" in last


def test_svg_is_viewed_through_an_img_tag_never_inlined(signed_in):
    client, _ = signed_in
    html = client.get("/view/books/" + d("pic.svg"), headers=HTML).text
    assert "src='/raw/books/Docs/pic.svg'" in html and "<svg" not in html.replace(OUTLINE_SPRITE, "") and "alert(1)" not in html


# ---- the text viewer ---------------------------------------------------------------------------------------------------------
def test_text_is_always_shown_escaped(signed_in):
    client, _ = signed_in
    for name in ("evil.txt", "evil.html"):
        html = client.get("/view/books/" + d(name), headers=HTML).text
        assert "&lt;script&gt;alert(" in html and "<script>alert" not in html, name
        assert "&lt;b&gt;bold&lt;/b&gt;" in html and "&amp;" in html


def test_encodings_are_detected_and_named(signed_in):
    client, _ = signed_in
    utf8 = client.get("/view/books/" + d("note.txt"), headers=HTML).text
    assert "utf-8" in utf8 and "2 lines" in utf8 and ">hello\nworld\n</pre>" in utf8
    latin = client.get("/view/books/" + d("latin.txt"), headers=HTML).text
    assert "café crème" in latin and "windows-1252" in latin
    wide = client.get("/view/books/" + d("wide.txt"), headers=HTML).text
    assert "héllo wörld" in wide and "utf-16" in wide
    assert "0 lines" in client.get("/view/books/" + d("empty.txt"), headers=HTML).text


def test_a_large_file_is_cut_with_a_clear_notice(cfg, tmp_path, docs):
    client, _ = make(cfg, tmp_path, text_view_kb=16)
    login(client)
    html = client.get("/view/books/" + d("big.txt"), headers=HTML).text
    assert "only the first 16.0 KB of 50.8 KB is shown" in html and "download the whole file" in html
    assert html.count("line of text") == 16 * 1024 // 13                            # 1260 whole lines (the cut line is partial)
    small = client.get("/view/books/" + d("note.txt"), headers=HTML).text
    assert "is shown" not in small                                                    # a small file shows no notice


# ---- unknown files and "open as text" -------------------------------------------------------------------------------------------
def test_unknown_files_that_are_plainly_text_open_as_text(signed_in):
    client, _ = signed_in
    for name, expect in (("README", "plain text without any extension"), ("data.xyz", "plainly text")):
        html = client.get("/view/books/" + d(name), headers=HTML).text
        assert expect in html and "pl-textview" in html, name


def test_binary_files_get_an_info_page_with_download_and_view_as_text(signed_in):
    client, _ = signed_in
    for name in ("blob.bin", "binary.log", "report.docx"):
        html = client.get("/view/books/" + d(name), headers=HTML).text
        assert "pl-infoview" in html and "href='/dl/books/Docs/%s'" % name in html, name
        assert "href='/view/books/Docs/%s?as=text'" % name in html and "View as text" in html
        assert "<pre" not in html
    assert "This looks like a binary file." in client.get("/view/books/" + d("blob.bin"), headers=HTML).text


def test_view_as_text_forces_even_binary_files_to_open(signed_in):
    client, _ = signed_in
    html = client.get("/view/books/" + d("blob.bin") + "?as=text", headers=HTML).text
    assert "pl-textview" in html and "shown as text although it looks like a binary file" in html and "�" in html
    docx = client.get("/view/books/" + d("report.docx") + "?as=text", headers=HTML).text
    assert "fake word document" in docx
    image_as_text = client.get("/view/books/" + d("a.png") + "?as=text", headers=HTML).text
    assert "pl-textview" in image_as_text and "<img" not in image_as_text


# ---- audio and video ---------------------------------------------------------------------------------------------------------------
def test_audio_and_video_pages(signed_in):
    client, _ = signed_in
    audio = client.get("/view/books/" + d("song1.mp3"), headers=HTML).text
    assert "<audio id='media' controls" in audio and "src='/raw/books/Docs/song1.mp3'" in audio
    assert "data-progress='/api/progress/books/Docs/song1.mp3'" in audio and "viewer-media.js" in audio
    assert "href='/view/books/Docs/song2.mp3'" in audio and "1 / 2" in audio       # next song, not the video
    video = client.get("/view/books/" + d("clip.mp4"), headers=HTML).text
    assert "<video id='media' controls" in video and "playsinline" in video and "1 / 1" in video


# ---- dispatch, access and progress ----------------------------------------------------------------------------------------------------
def test_books_are_sent_to_their_readers(signed_in):
    client, _ = signed_in
    r = client.get("/view/books/" + d("x.cbz"), headers=HTML)
    assert r.status_code == 303 and r.headers["location"] == "/read/books/Docs/x.cbz"


def test_view_needs_a_login_and_only_opens_files_you_may_see(signed_in, library):
    client, app = signed_in
    anon = TestClient(app, follow_redirects=False)
    r = anon.get("/view/books/" + d("note.txt"), headers=HTML)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    assert client.get("/view/books/Docs", headers=HTML).status_code == 404                  # a folder
    assert client.get("/view/books/Docs/nope.txt", headers=HTML).status_code == 404
    (library / "comics" / "Adult Pub" / "secret.txt").write_text("hidden", encoding="utf-8")
    assert client.get("/view/comics/Adult%20Pub/secret.txt", headers=HTML).status_code == 404
    carol = TestClient(app, follow_redirects=False)
    login(carol, "carol", "pw-carol")
    assert carol.get("/view/books/" + d("note.txt"), headers=HTML).status_code == 404       # carol has no 'books' root
    assert client.get("/view/books/%2e%2e/x.txt", headers=HTML).status_code in (400, 404)


def test_progress_and_continue_reading_cover_pdf_and_media_but_not_plain_files(signed_in):
    client, app = signed_in
    put = lambda path, loc, pct: client.put("/api/progress/books/" + path, content=json.dumps({"locator": loc, "percent": pct}),
                                            headers={"content-type": "application/json", "origin": "http://testserver"})
    assert put(d("song1.mp3"), "t:61.5", 0.3).status_code == 200
    assert put(d("doc.pdf"), "page:4", 0.4).status_code == 200
    assert put(d("note.txt"), "x", 0.5).status_code == 200                                  # allowed, just not listed
    assert put("Docs/nope.mp3", "t:1", 0.1).status_code == 404
    home = client.get("/", headers=HTML).text
    assert "href='/view/books/Docs/song1.mp3'" in home and "href='/view/books/Docs/doc.pdf'" in home
    assert "note" not in home.split("Browse")[0]


# ---- the guard that keeps pluteus's own files out of the library ------------------------------------------------------------------------
def test_state_and_config_may_not_live_inside_a_library(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    base = {"roots": [{"name": "r", "path": str(lib)}],
            "users": [{"name": "u", "password_hash": "scrypt$1$2$3$4$5"}]}
    for key in ("data_dir", "cache_dir", "themes_dir"):
        with pytest.raises(ConfigError, match="inside root"):
            parse_config({**base, "server": {key: str(lib / "pluteus-data")}})
    parse_config({**base, "server": {"data_dir": str(tmp_path / "elsewhere")}})                # fine
    parse_config({**base, "server": {"data_dir": str(lib / "data"), "show_all_files": False}})   # fine in book-server mode
    inside = lib / "config.toml"
    inside.write_text("[[roots]]\nname = 'r'\npath = '%s'\n[[users]]\nname = 'u'\npassword_hash = 'scrypt$1$2$3$4$5'\n" % lib,
                      encoding="utf-8")
    with pytest.raises(ConfigError, match="config file"):
        load_config(str(inside))
    (tmp_path / "out.toml").write_text(inside.read_text(encoding="utf-8"), encoding="utf-8")
    assert load_config(str(tmp_path / "out.toml")).show_all_files is True


# ---- scripts ---------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["viewer-image.js", "viewer-text.js", "viewer-media.js", "viewer-table.js"])
def test_viewer_scripts_are_served_and_parse(signed_in, name):
    client, _ = signed_in
    assert client.get("/static/" + name).status_code == 200
    if shutil.which("node"):
        done = subprocess.run(["node", "--check", str(STATIC / name)], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr


# ---- PDF viewer ------------------------------------------------------------------------------------------------------------------------
def test_pdf_page_loads_the_module_viewer_and_stays_inside_our_origin(signed_in):
    client, _ = signed_in
    r = client.get("/view/books/" + d("doc.pdf"), headers=HTML)
    assert r.status_code == 200
    assert "viewer-pdf.mjs" in r.text and "type='module'" in r.text
    assert "http://" not in r.text.replace("http://testserver", "") and "cdn" not in r.text.lower()
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "blob:" in csp.split("style-src")[1].split(";")[0]


def test_pdf_assets_are_served_with_usable_types(signed_in):
    client, _ = signed_in
    mjs = client.get("/static/viewer-pdf.mjs")
    assert mjs.status_code == 200 and mjs.headers["content-type"].startswith("text/javascript")
    for name in ("pdf.min.mjs", "pdf.worker.min.mjs"):
        r = client.get("/static/vendor/pdfjs/" + name)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    fonts = sorted((STATIC / "vendor" / "pdfjs" / "standard_fonts").iterdir())
    assert fonts and client.get("/static/vendor/pdfjs/standard_fonts/" + fonts[0].name).status_code == 200


def test_pdf_viewer_script_parses():
    if shutil.which("node"):
        done = subprocess.run(["node", "--check", str(STATIC / "viewer-pdf.mjs")], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr


def test_pdf_wasm_decoders_are_served_as_webassembly_and_the_csp_allows_only_wasm(signed_in):
    client, _ = signed_in
    r = client.get("/static/vendor/pdfjs/wasm/openjpeg.wasm")
    assert r.status_code == 200 and r.headers["content-type"] == "application/wasm" and r.content[:4] == b"\0asm"
    csp = client.get("/", headers=HTML).headers["content-security-policy"]
    assert "'wasm-unsafe-eval'" in csp and "'unsafe-eval'" not in csp.replace("'wasm-unsafe-eval'", "")


# ---- audiobooks (.m4b) and the listening controls --------------------------------------------------------------------------------------
def test_m4b_is_audio():
    assert kinds.kind_of("The Hobbit.m4b") == "audio" and kinds.kind_of("X.M4B") == "audio"
    assert kinds.INLINE_MIME[".m4b"] == "audio/mp4"


def test_m4b_plays_seeks_and_has_the_listening_controls(signed_in, docs):
    client, _ = signed_in
    (docs / "Zebra book.m4b").write_bytes(b"\x00\x00\x00\x20ftypM4B " + b"\x00" * 5000)
    raw = client.get("/raw/books/" + d("Zebra book.m4b"))
    assert raw.status_code == 200 and raw.headers["content-type"] == "audio/mp4" and raw.headers["accept-ranges"] == "bytes"
    part = client.get("/raw/books/" + d("Zebra book.m4b"), headers={"Range": "bytes=100-199"})
    assert part.status_code == 206 and len(part.content) == 100                       # seeking in a 900 MB file is a range request
    page = client.get("/view/books/" + d("Zebra book.m4b"), headers=HTML).text
    assert "<audio id='media' controls" in page and "src='/raw/books/Docs/Zebra%20book.m4b'" in page
    for ident in ("back30", "fwd30", "speed", "sleep"):
        assert "id='%s'" % ident in page
    assert "data-title='Zebra book'" in page and "data-album='Docs'" in page            # for the lock screen
    assert "data-artwork='/static/icon/icon-512.png'" in page and "data-prev='" in page and "data-next='" in page
    assert "data-progress='/api/progress/books/Docs/Zebra%20book.m4b'" in page


def test_the_viewer_script_has_the_features_it_promises():
    js = (STATIC / "viewer-media.js").read_text(encoding="utf-8")
    for needle in ("mediaSession", "seekbackward", "nexttrack", "playbackRate", "pluteus-media-rate", "autoplay=1", "STEPS"):
        assert needle in js, needle

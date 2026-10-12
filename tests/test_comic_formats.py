"""CBR, CB7 and CBT next to CBZ: the format is decided by the file's content, RAR and 7z go through bsdtar with limits,
and the upload folder opens ZIP comics only."""
import io
import shutil
import subprocess
import tarfile

import pytest
from cbz_factory import make_cbz, png
from rar_factory import make_rar
from starlette.testclient import TestClient
from test_login_web import HTML, login, make

from pluteus import cbz
from pluteus.cbz import CbzError

needs_bsdtar = pytest.mark.skipif(cbz.bsdtar() is None, reason="bsdtar (libarchive) is not installed")
RED, GREEN, BLUE = png((255, 0, 0)), png((0, 255, 0)), png((0, 0, 255))
ENTRIES = [("page10.png", BLUE), ("page2.png", GREEN), ("page1.png", RED), ("notes.txt", b"hello"), ("__MACOSX/page1.png", b"x")]
PAGES = ["page1.png", "page2.png", "page10.png"]


def make_cbt(path, entries):
    with tarfile.open(path, "w") as tf:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def make_cb7(path, entries, tmp):
    exe = shutil.which("7z") or shutil.which("7za") or shutil.which("7zz")
    if not exe:
        pytest.skip("no 7z program to build a test archive with")
    src = tmp / "src7"
    for name, data in entries:
        target = src / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    subprocess.run([exe, "a", "-t7z", str(path), "."], cwd=str(src), check=True, capture_output=True)
    return path


def test_the_kind_comes_from_the_content_not_the_name(tmp_path):
    assert cbz.sniff(make_cbz(tmp_path / "a.cbr", ENTRIES)) == "zip"                 # a renamed ZIP
    assert cbz.sniff(make_rar(tmp_path / "b.cbz", ENTRIES)) == "rar"
    assert cbz.sniff(make_cbt(tmp_path / "c.cbz", ENTRIES)) == "tar"
    junk = tmp_path / "d.cbr"
    junk.write_bytes(b"this is not an archive" * 50)
    assert cbz.sniff(str(junk)) == ""
    with pytest.raises(CbzError):
        cbz.list_pages(str(junk))
    with pytest.raises(CbzError):
        cbz.list_pages(str(tmp_path / "missing.cbr"))


def test_a_cbr_that_is_really_a_zip_just_works(tmp_path):
    path = make_cbz(tmp_path / "renamed.cbr", ENTRIES)
    assert cbz.list_pages(path) == PAGES and cbz.read_page(path, "page2.png") == GREEN


def test_cbt_is_read_with_python_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(cbz, "bsdtar", lambda: None)                                    # no external tool needed for TAR
    path = make_cbt(tmp_path / "a.cbt", ENTRIES)
    assert cbz.list_pages(path) == PAGES and cbz.read_page(path, "page10.png") == BLUE


@needs_bsdtar
def test_cbr_lists_in_natural_order_and_reads_each_page(tmp_path):
    path = make_rar(tmp_path / "a.cbr", ENTRIES)
    assert cbz.list_pages(path) == PAGES                                                # clutter and non-images left out
    assert cbz.read_page(path, "page1.png") == RED and cbz.read_page(path, "page10.png") == BLUE


@needs_bsdtar
def test_names_with_pattern_characters_select_exactly_one_page(tmp_path):
    path = make_rar(tmp_path / "a.cbr", [("p[1].png", b"ONE"), ("p1.png", b"TWO"), ("p*.png", b"STAR"), ("p?.png", b"ASK")])
    assert cbz.read_page(path, "p[1].png") == b"ONE" and cbz.read_page(path, "p1.png") == b"TWO"
    assert cbz.read_page(path, "p*.png") == b"STAR" and cbz.read_page(path, "p?.png") == b"ASK"


@needs_bsdtar
def test_cb7_is_read_through_bsdtar(tmp_path):
    path = make_cb7(tmp_path / "a.cb7", ENTRIES, tmp_path)
    assert cbz.sniff(str(path)) == "sevenzip"
    assert cbz.list_pages(str(path)) == PAGES and cbz.read_page(str(path), "page2.png") == GREEN


@needs_bsdtar
def test_a_damaged_or_oversized_rar_is_a_polite_error(tmp_path):
    path = make_rar(tmp_path / "a.cbr", [("page1.png", b"x" * 5000)])
    with pytest.raises(CbzError, match="larger"):
        cbz.read_page(path, "page1.png", limit=1000)
    data = open(path, "rb").read()
    broken = tmp_path / "broken.cbr"
    broken.write_bytes(data[:60])                                                      # cut off inside the first header
    with pytest.raises(CbzError):
        cbz.list_pages(str(broken))
    with pytest.raises(CbzError):
        cbz.read_page(path, "no-such-page.png")


def test_without_bsdtar_a_rar_says_so_instead_of_failing(tmp_path, monkeypatch):
    monkeypatch.setattr(cbz, "bsdtar", lambda: None)
    path = make_rar(tmp_path / "a.cbr", ENTRIES)
    with pytest.raises(CbzError, match="bsdtar"):
        cbz.list_pages(path)


def test_the_tool_has_a_time_limit_and_is_never_asked_to_extract_to_disk(tmp_path, monkeypatch):
    seen = []

    class Fake:
        def __init__(self, args, **kw):
            seen.append(args)
            self.stdout = io.BytesIO(b"page1.png\n")

        def read(self):
            return None

        def poll(self):
            return 0

        def wait(self, *a):
            return 0

        def kill(self):
            pass
    monkeypatch.setattr(cbz, "bsdtar", lambda: "bsdtar")
    monkeypatch.setattr(cbz.subprocess, "Popen", Fake)
    path = make_rar(tmp_path / "a.cbr", ENTRIES)
    assert cbz.list_pages(path) == ["page1.png"]
    assert seen[0][1] == "-tf"                                                         # list only
    assert cbz.read_page(path, "page1.png") == b"page1.png\n" and "-xOf" in seen[1]    # to stdout (-O), nothing written


def test_the_upload_folder_opens_zip_comics_only(tmp_path):
    rar = make_rar(tmp_path / "a.cbr", ENTRIES)
    zipped = make_cbz(tmp_path / "b.cbz", ENTRIES)
    tarred = make_cbt(tmp_path / "c.cbt", ENTRIES)
    for path in (rar, tarred):
        with pytest.raises(CbzError, match="upload"):
            cbz.list_pages(path, external=False)
        with pytest.raises(CbzError, match="upload"):
            cbz.read_page(path, "page1.png", external=False)
    assert cbz.list_pages(zipped, external=False) == PAGES and cbz.read_page(zipped, "page1.png", external=False) == RED


# ---- through the site -----------------------------------------------------------------------------------------------------
@needs_bsdtar
def test_a_cbr_in_the_library_is_listed_read_streamed_and_downloaded(cfg, tmp_path, library):
    make_rar(library / "comics" / "Formats" / "Old One.cbr", ENTRIES)
    make_cbt(library / "comics" / "Formats" / "Tar One.cbt", ENTRIES)
    client, _ = make(cfg, tmp_path)
    login(client)
    page = client.get("/browse/comics/Formats", headers=HTML).text
    assert "Old One.cbr" in page and "Tar One.cbt" in page
    assert "href='/read/comics/Formats/Old%20One.cbr'" in page                           # the title opens the comic reader
    assert client.get("/read/comics/Formats/Old%20One.cbr", headers=HTML).status_code == 200
    r = client.get("/cbz/comics/Formats/Old%20One.cbr?page=1")
    assert r.status_code == 200 and r.content == GREEN and r.headers["content-type"] == "image/png"
    assert client.get("/cbz/comics/Formats/Tar%20One.cbt?page=0").content == RED
    dl = client.get("/dl/comics/Formats/Old%20One.cbr")
    assert dl.status_code == 200 and dl.headers["content-type"].startswith("application/vnd.comicbook-rar")
    feed = client.get("/opds/comics/Formats").text
    assert "Old One.cbr" in feed and "http://vaemendis.net/opds-pse/stream" in feed     # a page-streaming link for it


def test_a_cbr_in_the_upload_folder_is_not_opened_but_stays_downloadable(cfg, tmp_path):
    from test_uploads import ORIGIN
    folder = tmp_path / "uploads"
    client, app = make(cfg, tmp_path, uploads_dir=str(folder))
    me = TestClient(app, follow_redirects=False)
    assert me.post("/login", content="username=alice&password=pw-alice",
                   headers={"content-type": "application/x-www-form-urlencoded", **ORIGIN}).status_code == 303
    make_rar(folder / "alice" / "old.cbr", ENTRIES)
    make_cbz(folder / "alice" / "ok.cbz", ENTRIES)
    r = me.get("/read/uploads/old.cbr", headers=HTML)
    assert r.status_code == 422 and "upload" in r.text
    assert me.get("/cbz/uploads/old.cbr?page=0").status_code == 422
    assert me.get("/read/uploads/ok.cbz", headers=HTML).status_code == 200            # ZIP comics still open
    assert me.get("/dl/uploads/old.cbr").status_code == 200

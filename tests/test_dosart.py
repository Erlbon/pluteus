"""DOS-style ASCII art (.nfo, .diz, .asc): the old character set (code page 437) is used when the bytes are not UTF-8."""
import pytest
from starlette.testclient import TestClient

from conftest import basic
from pluteus import textview
from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import parse_config

ART = bytes([0xDB, 0xDB, 0xB2, 0xB1, 0xB0, 0x20, 0xC9, 0xCD, 0xBB, 0x0D, 0x0A, 0xC8, 0xCD, 0xBC])      # blocks and a box in code page 437
ART_TEXT = "██▓▒░ ╔═╗\r\n╚═╝"
HTML = {"Accept": "text/html"}


@pytest.mark.parametrize("name", ["scene.nfo", "SCENE.NFO", "file.diz", "pic.asc"])
def test_dos_files_with_non_utf8_bytes_are_read_as_code_page_437(name):
    text, encoding = textview.decode(ART, name=name)
    assert text == ART_TEXT and encoding.startswith("cp437")
    assert textview.decode(ART, force=True, name=name)[0] == ART_TEXT                    # also behind "show as text anyway"
    assert textview.decode(ART, truncated=True, name=name)[0] == ART_TEXT


def test_other_files_and_valid_utf8_are_not_touched():
    assert textview.decode(ART, name="notes.txt")[1] == "windows-1252"                    # a .txt keeps the old guess
    assert textview.decode(ART)[1] == "windows-1252"
    utf8 = "Bjørn █".encode("utf-8")
    assert textview.decode(utf8, name="band.nfo") == ("Bjørn █", "utf-8")       # a UTF-8 .nfo (Kodi) stays UTF-8
    assert textview.decode(b"\xef\xbb\xbfplain", name="x.nfo")[1] == "utf-8-sig"


@pytest.fixture
def client(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "scene.nfo").write_bytes(ART)
    (lib / "plain.txt").write_bytes(b"caf\xe9 \xdb")
    cfg = parse_config({"roots": [{"name": "lib", "path": str(lib)}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]}]})
    return TestClient(create_app(cfg)), basic("alice", "pw")


def test_the_viewer_shows_the_art_with_tight_lines(client):
    c, auth = client
    page = c.get("/view/lib/scene.nfo", headers={**auth, **HTML}).text
    assert "██▓▒░ ╔═╗" in page and "cp437 (DOS)" in page and "class='pl-text pl-art'" in page
    plain = c.get("/view/lib/plain.txt", headers={**auth, **HTML}).text
    assert "pl-art" not in plain and "windows-1252" in plain

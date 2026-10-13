"""Quick share: the in-memory configuration, the one-time access link, uploads, and the QR code in the terminal."""
import os

import pytest
from starlette.testclient import TestClient

from pluteus import share
from pluteus.app import create_app
from pluteus.config import ConfigError

HTML = {"accept": "text/html"}
ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "Photos"
    (d / "Trip").mkdir(parents=True)
    (d / "Trip" / "beach.txt").write_text("sand")
    (d / "note.txt").write_text("hello")
    (d / ".secret").write_text("hidden")
    return d


def test_open_share_is_read_only_and_needs_no_login(folder, tmp_path):
    cfg = share.build_config([str(folder)], port=8123, data_dir=str(tmp_path / "state"))
    assert cfg.anonymous_user == "guest" and cfg.join_token == "" and not cfg.uploads_dir and list(cfg.roots) == ["Photos"]
    client = TestClient(create_app(cfg), follow_redirects=False)
    assert "Photos" in client.get("/", headers=HTML).text                               # no login at all
    page = client.get("/browse/Photos", headers=HTML).text
    assert "note.txt" in page and "Trip" in page and ".secret" not in page              # dot files stay hidden
    assert client.get("/dl/Photos/note.txt").content == b"hello"
    assert client.get("/browse/uploads", headers=HTML).status_code == 404               # no upload area in open mode
    assert client.get("/join/anything").status_code == 404                              # and no access link


def test_private_share_needs_the_token_and_the_token_signs_in(folder, tmp_path):
    cfg = share.build_config([str(folder)], port=8123, private=True, token="s3cret-token-value", data_dir=str(tmp_path / "state"))
    assert cfg.anonymous_user == "" and cfg.join_token == "s3cret-token-value"
    client = TestClient(create_app(cfg), follow_redirects=False)
    assert client.get("/", headers=HTML).status_code in (303, 401)                       # strangers get the login page
    assert client.get("/dl/Photos/note.txt").status_code in (401, 303)
    assert client.get("/join/wrong-token").status_code == 404 and client.get("/join/").status_code == 404
    r = client.get("/join/s3cret-token-value")
    assert r.status_code == 303 and r.headers["location"] == "/" and "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/dl/Photos/note.txt").content == b"hello"                         # the cookie from /join is enough
    assert "Photos" in client.get("/", headers=HTML).text


def test_upload_share_lets_guests_upload_into_a_separate_folder(folder, tmp_path):
    up = tmp_path / "uploads"
    cfg = share.build_config([str(folder)], port=8123, upload_dir=str(up), private=True, token="tok-tok-tok-tok", data_dir=str(tmp_path / "state"))
    client = TestClient(create_app(cfg), follow_redirects=False)
    client.get("/join/tok-tok-tok-tok")
    r = client.post("/upload/uploads", files=[("files", ("hi.txt", b"hello from a guest", "text/plain"))],
                    headers={**ORIGIN, "accept": "application/json"})
    assert r.status_code == 200
    assert (up / "guest" / "hi.txt").read_bytes() == b"hello from a guest"
    assert not (folder / "hi.txt").exists()                                                # the shared folder is never written to


def test_upload_folder_must_be_separate_from_the_shared_folder(folder, tmp_path):
    with pytest.raises(ConfigError):
        share.build_config([str(folder)], port=8123, upload_dir=str(folder / "in"), private=True, token="x" * 16, data_dir=str(tmp_path / "s"))
    with pytest.raises(ConfigError):
        share.build_config([str(folder)], port=8123, upload_dir=str(tmp_path), private=True, token="x" * 16, data_dir=str(tmp_path / "s"))
    with pytest.raises(ConfigError):
        share.build_config([str(tmp_path / "missing")], port=8123, data_dir=str(tmp_path / "s"))


def test_root_names_are_url_safe_and_unique(tmp_path):
    a, b = tmp_path / "My Photos", tmp_path / "x" / "My Photos"
    a.mkdir()
    b.mkdir(parents=True)
    assert share.root_names([str(a), str(b)]) == ["My-Photos", "My-Photos-2"]
    assert share.root_names(["."]) [0].replace("-", "").replace("_", "").isalnum()


def test_port_and_address_helpers():
    import socket
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        busy = s.getsockname()[1]
        assert share.free_port(busy, "0.0.0.0") != busy                                   # skips a port that is taken
    ip = share.lan_ip()
    assert ip.count(".") == 3 and all(p.isdigit() for p in ip.split("."))


def test_qr_code_is_drawn_in_every_mode():
    pytest.importorskip("qrcodegen")
    url = "http://192.168.1.20:8080/join/abcdefghijklmnop"
    plain = share.qr_text(url, blocks=True, colours=False)
    rows = plain.split("\n")
    assert len(rows) >= 15 and len({len(r) for r in rows}) == 1                              # a square, drawn two modules per line
    assert set(plain) <= {" ", "█", "▀", "▄", "\n"}
    assert "\x1b[30;47m" in share.qr_text(url, blocks=True, colours=True)                  # black on white whatever the theme
    ascii_art = share.qr_text(url, blocks=False, colours=False)
    assert set(ascii_art) <= {" ", "#", "\n"} and "##" in ascii_art
    from qrcodegen import QrCode
    q = QrCode.encode_text(url, QrCode.Ecc.MEDIUM)
    assert len(ascii_art.split("\n")) == q.get_size() + 4                                    # one row per module plus the quiet zone


def test_banner_says_what_is_shared_and_how_open_it_is(folder, tmp_path):
    cfg = share.build_config([str(folder)], port=8123, data_dir=str(tmp_path / "s"))
    text = share.banner(cfg, "http://192.168.1.20:8123/", private=False, upload_dir="", show_qr=False)
    assert "Photos" in text and "http://192.168.1.20:8123/" in text and "OPEN" in text and "Ctrl+C" in text and "read-only" in text
    private = share.banner(cfg, "http://192.168.1.20:8123/join/x", private=True, upload_dir=str(tmp_path / "up"), show_qr=False)
    assert "PRIVATE" in private and "Uploads" in private and "OPEN" not in private
    hint = share.banner(cfg, "http://192.168.1.20:8123/", private=False, upload_dir="", show_qr=False, others=("10.0.0.7", "100.64.0.2"))
    assert "10.0.0.7" in hint and "--address" in hint
    assert "other addresses" not in text

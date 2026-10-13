"""Tags, cover art and lyrics of audio files: the reader, the /cover route, the player page and the folder listing."""
import os
import shutil

import pytest
from starlette.testclient import TestClient

from conftest import basic
from pluteus import mediainfo
from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import parse_config

HERE = os.path.dirname(__file__)
TAGGED = os.path.join(HERE, "data", "tagged.mp3")
HTML = {"Accept": "text/html"}
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 40


@pytest.fixture
def music(tmp_path):
    folder = tmp_path / "music" / "Album"
    folder.mkdir(parents=True)
    shutil.copy(TAGGED, folder / "spark.mp3")
    (folder / "plain.mp3").write_bytes(b"ID3" + b"\x00" * 200)
    return tmp_path / "music"


def make_client(music):
    cfg = parse_config({"roots": [{"name": "music", "path": str(music)}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]}]})
    return TestClient(create_app(cfg)), basic("alice", "pw")


def test_tags_are_read_and_cleaned():
    info = mediainfo.read_info(TAGGED, with_cover=True)
    assert (info["title"], info["artist"], info["album"], info["year"], info["genre"]) == (
        "Spark På Leggen", "Kaizers Orchestra", "Ompa til du dør", "2001", "Rock")
    assert (info["track"], info["track_total"], info["has_cover"]) == (5, 12, True)
    assert "Første vers" in info["lyrics"]
    assert mediainfo.facts(info)[:3] == ["2001", "Rock", "Track 5 of 12"]


def test_a_file_without_tags_or_not_audio_gives_nothing(tmp_path):
    junk = tmp_path / "x.mp3"
    junk.write_bytes(b"not audio at all")
    assert mediainfo.brief(str(junk)) == {}
    assert mediainfo.read_info(str(tmp_path / "missing.mp3")) == {}


def test_only_real_pictures_are_accepted():
    assert mediainfo.sniff_image(JPEG) == "image/jpeg"
    assert mediainfo.sniff_image(b"<svg onload=alert(1)>") == ""
    assert mediainfo.sniff_image(b"GIF89a....") == "image/gif"


def test_timed_lyrics_are_parsed_in_order():
    lines = mediainfo.parse_lrc("[ar:x]\n[00:12.50]Hello\n[01:02.5][02:00]Again\n[00:03]")
    assert lines == [(3.0, ""), (12.5, "Hello"), (62.5, "Again"), (120.0, "Again")]


def test_cover_route_gives_the_embedded_picture(music):
    client, auth = make_client(music)
    r = client.get("/cover/music/Album/spark.mp3", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:3] == b"\xff\xd8\xff"
    assert r.headers["x-content-type-options"] == "nosniff"
    again = client.get("/cover/music/Album/spark.mp3", headers={**auth, "If-None-Match": r.headers["etag"]})
    assert again.status_code == 304


def test_cover_route_needs_a_login_and_refuses_other_files(music):
    client, auth = make_client(music)
    assert client.get("/cover/music/Album/spark.mp3").status_code == 401
    assert client.get("/cover/music/Album/plain.mp3", headers=auth).status_code == 404        # no picture anywhere
    (music / "Album" / "notes.txt").write_text("x")
    assert client.get("/cover/music/Album/notes.txt", headers=auth).status_code == 404


def test_folder_cover_is_the_fallback(music):
    (music / "Album" / "cover.jpg").write_bytes(JPEG)
    client, auth = make_client(music)
    r = client.get("/cover/music/Album/plain.mp3", headers=auth)
    assert r.status_code == 200 and r.content == JPEG
    page = client.get("/view/music/Album/plain.mp3", headers={**auth, **HTML}).text
    assert "src='/cover/music/Album/plain.mp3'" in page


def test_player_page_shows_the_tags(music):
    (music / "Album" / "spark.lrc").write_text("[00:01.00]Første\n[00:05.00]Andre\n", encoding="utf-8")
    client, auth = make_client(music)
    page = client.get("/view/music/Album/spark.mp3", headers={**auth, **HTML}).text
    for text in ("Spark På Leggen", "Kaizers Orchestra", "Ompa til du dør", "Track 5 of 12", "2001", "Rock"):
        assert text in page
    assert "data-artist='Kaizers Orchestra'" in page and "id='scrub'" in page and "id='play'" in page
    assert "data-t='1.0'" in page and "pl-lyrics-synced" in page             # the .lrc file wins over the plain tag text


def test_plain_lyrics_come_from_the_tag_when_there_is_no_lrc(music):
    client, auth = make_client(music)
    page = client.get("/view/music/Album/spark.mp3", headers={**auth, **HTML}).text
    assert "pl-lyrics-plain" in page and "Andre linje" in page


def test_a_file_without_tags_still_gets_a_page_named_after_the_file(music):
    client, auth = make_client(music)
    page = client.get("/view/music/Album/plain.mp3", headers={**auth, **HTML}).text
    assert "<h1 class='pl-track-title'>plain</h1>" in page and "pl-cover-blank" in page and "/cover/" not in page


def test_tag_text_is_escaped(music, monkeypatch):
    monkeypatch.setattr(mediainfo, "read_info", lambda path, with_cover=False: {
        "title": "<script>alert(1)</script>", "artist": "<b>x</b>", "lyrics": "<img src=x onerror=alert(2)>"})
    client, auth = make_client(music)
    page = client.get("/view/music/Album/spark.mp3", headers={**auth, **HTML}).text
    assert "<script>alert(1)" not in page and "<b>x</b>" not in page and "<img src=x" not in page
    assert "&lt;script&gt;" in page


def test_the_listing_shows_title_artist_and_track(music):
    client, auth = make_client(music)
    page = client.get("/browse/music/Album", headers={**auth, **HTML}).text
    assert "Spark På Leggen" in page and "Kaizers Orchestra" in page and "plain.mp3" in page

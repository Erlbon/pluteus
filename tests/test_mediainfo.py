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
    assert mediainfo.read_info(str(junk)).get("title", "") == ""
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


def test_the_folder_listing_shows_file_names_not_tags(music):
    client, auth = make_client(music)
    page = client.get("/browse/music/Album", headers={**auth, **HTML}).text
    assert "spark.mp3" in page and "plain.mp3" in page                       # the file view stays plain: tags only in the player
    assert "Spark På Leggen" not in page and "Kaizers Orchestra" not in page


# ---- the player script keeps the listener's place --------------------------------------------------------------------------------------------
def test_the_autoplay_flag_is_taken_off_the_address_and_keys_are_left_alone_in_fields():
    js = open(os.path.join(HERE, "..", "pluteus", "static", "viewer-media.js"), encoding="utf-8").read()
    assert "history.replaceState" in js and js.count("autoplay=1") >= 3
    assert "/[?&]autoplay=1/.test(location.search)" in js and js.count("test(location.search)") == 1          # read once, used as a variable
    assert "e.altKey || e.ctrlKey || e.metaKey" in js and "INPUT|TEXTAREA|SELECT" in js


# ---- chapters and bookmarks ---------------------------------------------------------------------------------------------------------------
CHAPTERS = os.path.join(HERE, "data", "chapters.m4b")


def test_chapters_are_read_from_an_m4b():
    chapters = mediainfo.read_chapters(CHAPTERS)
    assert [t for t, _ in chapters] == [0.0, 2.0, 4.5] and [n for _, n in chapters] == ["Opening", "Kapittel to æøå", "Epilogue"]
    assert mediainfo.read_chapters(TAGGED) == [] and mediainfo.read_chapters("nonexistent.m4b") == []


def test_a_damaged_chapter_atom_gives_nothing(tmp_path):
    bad = tmp_path / "bad.m4b"
    data = open(CHAPTERS, "rb").read()
    bad.write_bytes(data[:len(data) // 2])
    assert isinstance(mediainfo.read_chapters(str(bad)), list)               # no exception, whatever is left
    assert mediainfo._parse_chpl(b"\x01\x00\x00\x00\x00\x00\x00\x00\xff" + b"\x00" * 5) == []


def test_the_player_lists_chapters_and_offers_bookmarks(music):
    shutil.copy(CHAPTERS, music / "Album" / "book.m4b")
    client, auth = make_client(music)
    page = client.get("/view/music/Album/book.m4b", headers={**auth, **HTML}).text
    assert "id='chapters-btn'" in page and "data-t='2.0'" in page and "Kapittel to æøå" in page and "Epilogue" in page
    assert "id='bookmarks-btn'" in page and "data-bookmarks='/api/bookmarks/music/Album/book.m4b'" in page
    plain = client.get("/view/music/Album/spark.mp3", headers={**auth, **HTML}).text
    assert "id='chapters-btn'" not in plain


def test_bookmarks_are_saved_listed_and_deleted_per_user(music):
    client, auth = make_client(music)
    url = "/api/bookmarks/music/Album/spark.mp3"
    origin = {**auth, "Origin": "http://testserver"}
    assert client.get(url, headers=auth).json() == {"bookmarks": []}
    made = client.post(url, json={"position": 61.5, "note": "the good part"}, headers=origin)
    assert made.status_code == 201
    client.post(url, json={"position": 5, "note": ""}, headers=origin)
    listed = client.get(url, headers=auth).json()["bookmarks"]
    assert [b["position"] for b in listed] == [5, 61.5] and listed[1]["note"] == "the good part"       # in time order
    assert client.delete(url + "?id=%d" % made.json()["id"], headers=origin).status_code == 200
    assert [b["position"] for b in client.get(url, headers=auth).json()["bookmarks"]] == [5]


def test_bookmarks_refuse_bad_input_other_files_and_other_users(music):
    client, auth = make_client(music)
    url = "/api/bookmarks/music/Album/spark.mp3"
    origin = {**auth, "Origin": "http://testserver"}
    for body in ({"position": -1}, {"position": "x"}, {}, {"position": 1e12}):
        assert client.post(url, json=body, headers=origin).status_code == 400
    (music / "Album" / "notes.txt").write_text("x")
    assert client.get("/api/bookmarks/music/Album/notes.txt", headers=auth).status_code == 404
    assert client.post(url, json={"position": 1}).status_code == 401
    assert client.post(url, json={"position": 1}, headers=auth).status_code == 403                    # no Origin: cross-site refused
    made = client.post(url, json={"position": 3}, headers=origin).json()["id"]
    state = client.app.state.state
    assert state.delete_bookmark("someone-else", made) == 0                                          # not theirs: nothing deleted
    assert len(state.list_bookmarks("alice", "music", "Album/spark.mp3")) == 1


def test_there_is_a_limit_per_file(music):
    client, auth = make_client(music)
    state = client.app.state.state
    for i in range(state.MAX_BOOKMARKS):
        assert state.add_bookmark("alice", "music", "Album/spark.mp3", float(i), "") is not None
    r = client.post("/api/bookmarks/music/Album/spark.mp3", json={"position": 1}, headers={**auth, "Origin": "http://testserver"})
    assert r.status_code == 400


def test_chapters_are_read_from_id3_chap_frames_of_an_mp3():
    chapters = mediainfo.read_chapters(os.path.join(HERE, "data", "chapters.mp3"))
    assert [t for t, _ in chapters] == [0.0, 2.0, 4.5] and [n for _, n in chapters] == ["Intro", "Del to æøå", "Slutt"]


def test_odd_id3_tags_never_raise(tmp_path):
    for name, data in (("a.mp3", b"ID3"), ("b.mp3", b"ID3\x04\x00\x00\x00\x00\x00\x20" + b"CHAP\x00\x00\x00\x10" + b"\x00" * 30),
                       ("c.mp3", b"ID3\x02\x00\x00\x00\x00\x00\x05abcde"), ("d.mp3", b"ID3\x03\x00\x40\x00\x00\x00\x08" + b"\xff" * 8)):
        f = tmp_path / name
        f.write_bytes(data)
        assert isinstance(mediainfo.read_chapters(str(f)), list)


def test_an_mp3_audiobook_page_lists_its_chapters(music):
    shutil.copy(os.path.join(HERE, "data", "chapters.mp3"), music / "Album" / "book.mp3")
    client, auth = make_client(music)
    page = client.get("/view/music/Album/book.mp3", headers={**auth, **HTML}).text
    assert "id='chapters-btn'" in page and "Del to æøå" in page and "data-t='4.5'" in page


# ---- the player's speed and sleep controls ------------------------------------------------------------------------------------------------
def test_speed_has_minus_and_plus_and_skips_have_arrows(music):
    client, auth = make_client(music)
    page = client.get("/view/music/Album/spark.mp3", headers={**auth, **HTML}).text
    assert "id='speed-down'" in page and "id='speed-up'" in page and "id='sleep'" in page
    assert "pl-i-prev" in page and "pl-i-next" in page and "pl-i-back30" not in page


def test_the_speed_and_sleep_row_can_be_switched_off_in_settings(music):
    client, auth = make_client(music)
    r = client.post("/settings", data={"theme": "default", "icons": "gold", "view": "list", "extras": "hide"},
                    headers={**auth, "Origin": "http://testserver"}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/view/music/Album/spark.mp3", headers={**auth, **HTML}).text
    assert "id='speed'" not in page and "id='sleep'" not in page and "id='play'" in page and "id='lyrics-btn'" in page
    assert "<option value='hide' selected>" in client.get("/settings", headers={**auth, **HTML}).text

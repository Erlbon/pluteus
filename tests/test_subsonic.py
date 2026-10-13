"""The Subsonic / OpenSubsonic subset: login by key, token, legacy password; browsing; stream; covers; throttle."""
import hashlib
import json
import os
import shutil
import xml.etree.ElementTree as ET

import pytest
from starlette.testclient import TestClient

from pluteus import subsonic
from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import parse_config

HERE = os.path.dirname(__file__)
TAGGED = os.path.join(HERE, "data", "tagged.mp3")
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 40
KEY = "app-key-for-alice-123"


@pytest.fixture
def lib(tmp_path):
    album = tmp_path / "music" / "Kaizers" / "Ompa"
    album.mkdir(parents=True)
    shutil.copy(TAGGED, album / "01 spark.mp3")
    (album / "02 other.mp3").write_bytes(b"ID3" + b"\x00" * 300)
    (album / "folder.jpg").write_bytes(JPEG)
    (tmp_path / "music" / "Kaizers" / "loose.txt").write_text("x")
    (tmp_path / "music" / "Solo").mkdir()
    (tmp_path / "music" / "Solo" / "single.mp3").write_bytes(b"ID3" + b"\x00" * 300)
    return tmp_path / "music"


def make(lib, subsonic_on=True):
    cfg = parse_config({"server": {"subsonic": subsonic_on}, "roots": [{"name": "music", "path": str(lib)}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw-alice", n=2 ** 4), "roots": ["*"]},
                                  {"name": "bob", "password_hash": hash_password("pw-bob", n=2 ** 4), "roots": ["*"]}]})
    app = create_app(cfg)
    app.state.state.set_appkey("alice", KEY)
    return TestClient(app)


def call(client, name, **params):
    params = {"u": "alice", "p": KEY, "v": "1.16.1", "c": "test", "f": "json", **params}
    r = client.get("/rest/%s.view" % name, params=params)
    return r


def body(r):
    return r.json()["subsonic-response"]


def test_it_is_off_by_default(lib):
    assert make(lib, False).get("/rest/ping.view", params={"u": "alice", "p": KEY}).status_code == 404


def test_ping_in_both_formats(lib):
    c = make(lib)
    assert body(call(c, "ping"))["status"] == "ok"
    xml = c.get("/rest/ping", params={"u": "alice", "p": KEY}).text
    root = ET.fromstring(xml)
    assert root.tag.endswith("subsonic-response") and root.get("status") == "ok" and root.get("openSubsonic") == "true"


def test_the_login_methods(lib):
    c = make(lib)
    salt = "abc123"
    token = hashlib.md5((KEY + salt).encode()).hexdigest()
    assert body(c.get("/rest/ping", params={"u": "alice", "t": token, "s": salt, "f": "json"}))["status"] == "ok"
    assert body(c.get("/rest/ping", params={"apiKey": KEY, "f": "json"}))["status"] == "ok"
    assert body(c.get("/rest/ping", params={"u": "alice", "p": "enc:" + KEY.encode().hex(), "f": "json"}))["status"] == "ok"
    assert body(c.get("/rest/ping", params={"u": "alice", "p": "pw-alice", "f": "json"}))["status"] == "ok"      # the real password


def test_wrong_logins_fail_with_subsonic_error_codes(lib):
    c = make(lib)
    assert body(call(c, "ping", p="nope"))["error"]["code"] == 40
    assert body(c.get("/rest/ping", params={"apiKey": "wrong", "f": "json"}))["error"]["code"] == 44
    assert body(c.get("/rest/ping", params={"apiKey": KEY, "u": "alice", "f": "json"}))["error"]["code"] == 43
    assert body(c.get("/rest/ping", params={"f": "json"}))["error"]["code"] == 10
    bob = c.get("/rest/ping", params={"u": "bob", "t": "x", "s": "y", "f": "json"})        # bob has no key: token login impossible
    assert body(bob)["error"]["code"] == 40


def test_repeated_failures_lock_the_account(lib):
    c = make(lib)
    for _ in range(8):
        call(c, "ping", p="nope")
    assert "Too many" in body(call(c, "ping", p="nope"))["error"]["message"]


def test_folders_and_directory_browsing(lib):
    c = make(lib)
    folders = body(call(c, "getMusicFolders"))["musicFolders"]["musicFolder"]
    assert folders == [{"id": subsonic.make_id("music"), "name": "music"}]
    top = body(call(c, "getMusicDirectory", id=folders[0]["id"]))["directory"]["child"]
    assert [x["title"] for x in top] == ["Kaizers", "Solo"] and all(x["isDir"] for x in top)
    album = body(call(c, "getMusicDirectory", id=subsonic.make_id("music", ("Kaizers", "Ompa"))))["directory"]
    songs = album["child"]
    assert [s["title"] for s in songs] == ["Spark På Leggen", "02 other"]            # tagged first (track 5), then untagged
    assert songs[0]["artist"] == "Kaizers Orchestra" and songs[0]["track"] == 5 and songs[0]["year"] == 2001
    assert songs[0]["contentType"] == "audio/mpeg" and songs[0]["coverArt"] == album["id"]
    idx = body(call(c, "getIndexes"))["indexes"]
    assert [g["name"] for g in idx["index"]] == ["K", "S"]


def test_tag_style_browsing(lib):
    c = make(lib)
    artists = body(call(c, "getArtists"))["artists"]["index"]
    names = [a["name"] for g in artists for a in g["artist"]]
    assert names == ["Kaizers", "Solo"]
    kid = subsonic.make_id("music", ("Kaizers",))
    artist = body(call(c, "getArtist", id=kid))["artist"]
    assert [a["name"] for a in artist["album"]] == ["Ompa"] and artist["album"][0]["songCount"] == 2
    album = body(call(c, "getAlbum", id=artist["album"][0]["id"]))["album"]
    assert album["songCount"] == 2 and album["artist"] == "Kaizers" and len(album["song"]) == 2
    solo = body(call(c, "getArtist", id=subsonic.make_id("music", ("Solo",))))["artist"]
    assert len(solo["album"]) == 1                                                    # songs directly under the artist: one album
    listing = body(call(c, "getAlbumList2", type="alphabeticalByName", size="10"))["albumList2"]["album"]
    assert [a["name"] for a in listing] == ["Ompa", "Solo"]
    assert len(body(call(c, "getRandomSongs", size="5"))["randomSongs"]["song"]) >= 1


def test_search(lib):
    c = make(lib)
    found = body(call(c, "search3", query="omp"))["searchResult3"]
    assert [a["name"] for a in found["album"]] == ["Ompa"] and len(found["song"]) == 2
    empty = body(call(c, "search3", query=""))["searchResult3"]
    assert not (empty.get("artist") or empty.get("album") or empty.get("song"))


def test_stream_download_and_range(lib):
    c = make(lib)
    sid = subsonic.make_id("music", ("Kaizers", "Ompa", "01 spark.mp3"))
    r = c.get("/rest/stream", params={"u": "alice", "p": KEY, "id": sid})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content[:3] == b"ID3"
    part = c.get("/rest/stream", params={"u": "alice", "p": KEY, "id": sid}, headers={"range": "bytes=0-9"})
    assert part.status_code == 206 and len(part.content) == 10
    dl = c.get("/rest/download", params={"u": "alice", "p": KEY, "id": sid})
    assert "attachment" in dl.headers["content-disposition"]
    text = subsonic.make_id("music", ("Kaizers", "loose.txt"))
    assert body(call(c, "stream", id=text))["error"]["code"] == 70


def test_cover_art_from_the_folder_and_from_the_file(lib):
    c = make(lib)
    folder = c.get("/rest/getCoverArt", params={"u": "alice", "p": KEY, "id": subsonic.make_id("music", ("Kaizers", "Ompa"))})
    assert folder.status_code == 200 and folder.content == JPEG
    embedded = c.get("/rest/getCoverArt", params={"u": "alice", "p": KEY, "id": subsonic.make_id("music", ("Solo", "single.mp3"))})
    assert embedded.status_code == 404                                                # no picture anywhere
    artist = c.get("/rest/getCoverArt", params={"u": "alice", "p": KEY, "id": subsonic.make_id("music", ("Kaizers",))})
    assert artist.status_code == 200 and artist.content == JPEG                        # an artist folder uses its first album's


def test_bad_ids_and_paths_are_not_found_not_errors(lib):
    c = make(lib)
    assert body(call(c, "getMusicDirectory", id="%%%"))["error"]["code"] == 70
    assert body(call(c, "getMusicDirectory", id=subsonic.make_id("nope")))["error"]["code"] == 70
    assert body(call(c, "getMusicDirectory", id=subsonic.make_id("music", ("..",))))["error"]["code"] == 70
    assert body(call(c, "getSong", id=subsonic.make_id("music", ("Kaizers", "..", "..", "x.mp3"))))["error"]["code"] == 70


def test_empty_answers_and_unknown_calls(lib):
    c = make(lib)
    assert body(call(c, "getPlaylists"))["playlists"] == {}
    assert body(call(c, "scrobble", id="x"))["status"] == "ok"
    assert body(call(c, "getOpenSubsonicExtensions"))["openSubsonicExtensions"][0]["name"] == "apiKeyAuthentication"
    assert body(call(c, "jukeboxControl"))["error"]["code"] == 0


def test_xml_escapes_tag_text(lib):
    c = make(lib)
    root = ET.fromstring(c.get("/rest/getAlbum", params={"u": "alice", "p": KEY,
                                                           "id": subsonic.make_id("music", ("Kaizers", "Ompa"))}).text)
    song = root.find("{http://subsonic.org/restapi}album/{http://subsonic.org/restapi}song")
    assert song.get("title") == "Spark På Leggen"


def test_the_listing_shows_the_folder_picture(lib):
    c = make(lib)
    login = {"Authorization": "Basic " + __import__("base64").b64encode(b"alice:pw-alice").decode()}
    page = c.get("/browse/music/Kaizers/Ompa", headers={**login, "Accept": "text/html"}).text
    assert "pl-folder-cover" in page and "src='/cover/music/Kaizers/Ompa'" in page
    r = c.get("/cover/music/Kaizers/Ompa", headers=login)
    assert r.status_code == 200 and r.content == JPEG
    assert "pl-folder-cover" not in c.get("/browse/music/Solo", headers={**login, "Accept": "text/html"}).text

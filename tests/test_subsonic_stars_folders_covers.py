"""Subsonic: star times from the Favourites shelf, starred artists and the starred album list, `musicFolderId` on every list
that takes it, and covers scaled to the `size` an app asks for."""
import io
import shutil

import pytest
from PIL import Image
from starlette.testclient import TestClient

from pluteus import subsonic
from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import parse_config
from test_subsonic import KEY, TAGGED, body, call, lib, make  # noqa: F401

ARTIST = subsonic.make_id("music", ("Kaizers",))
ALBUM = subsonic.make_id("music", ("Kaizers", "Ompa"))
SONG = subsonic.make_id("music", ("Kaizers", "Ompa", "01 spark.mp3"))
WHEN = 1_700_000_000                                   # 2023-11-14T22:13:20Z


def _photo(width=1200, height=800) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 120, 40)).save(out, "JPEG")
    return out.getvalue()


def two_libraries(tmp_path, lib, data_dir=""):
    """`music` (the fixture) as folder 1 and `more` (one artist with one album) as folder 2."""
    album = tmp_path / "more" / "Wardruna" / "Runaljod"
    album.mkdir(parents=True)
    shutil.copy(TAGGED, album / "01 tyr.mp3")
    server = {"subsonic": True, **({"data_dir": data_dir} if data_dir else {})}
    cfg = parse_config({"server": server, "roots": [{"name": "music", "path": str(lib)}, {"name": "more", "path": str(tmp_path / "more")}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw-alice", n=2 ** 4), "roots": ["*"]}]})
    app = create_app(cfg)
    app.state.state.set_appkey("alice", KEY)
    return TestClient(app)


# ---- A1: the time a star was given ----------------------------------------------------------------------------------------
def test_starred_is_the_time_the_star_was_given_not_now(lib):
    c = make(lib)
    c.app.state.state.shelf_add("alice", "favourite", "music", "Kaizers/Ompa/01 spark.mp3", False, now=WHEN)
    c.app.state.state.shelf_add("alice", "favourite", "music", "Kaizers/Ompa", True, now=WHEN + 60)
    assert body(call(c, "getSong", id=SONG))["song"]["starred"] == "2023-11-14T22:13:20Z"
    assert body(call(c, "getAlbum", id=ALBUM))["album"]["starred"] == "2023-11-14T22:14:20Z"
    starred = body(call(c, "getStarred2"))["starred2"]
    assert starred["song"][0]["starred"] == "2023-11-14T22:13:20Z" and starred["album"][0]["starred"] == "2023-11-14T22:14:20Z"


# ---- A5: starred artists and the starred album list -------------------------------------------------------------------------
def test_a_starred_top_folder_is_an_artist_and_shows_its_heart(lib):
    c = make(lib)
    assert body(call(c, "star", artistId=ARTIST))["status"] == "ok"
    starred = body(call(c, "getStarred2"))["starred2"]
    assert [a["id"] for a in starred["artist"]] == [ARTIST] and not starred.get("album")
    artists = {a["name"]: a for g in body(call(c, "getArtists"))["artists"]["index"] for a in g["artist"]}
    assert artists["Kaizers"].get("starred") and not artists["Solo"].get("starred")
    assert body(call(c, "getArtist", id=ARTIST))["artist"]["starred"]
    assert body(call(c, "getIndexes"))["indexes"]["index"][0]["artist"][0].get("starred")
    call(c, "unstar", artistId=ARTIST)
    assert not body(call(c, "getStarred2"))["starred2"].get("artist")
    assert not body(call(c, "getArtist", id=ARTIST))["artist"].get("starred")


def test_the_starred_album_list_holds_the_starred_albums_only(lib):
    c = make(lib)
    assert not body(call(c, "getAlbumList2", type="starred"))["albumList2"].get("album")
    call(c, "star", artistId=ARTIST)
    call(c, "star", id=SONG)
    assert not body(call(c, "getAlbumList2", type="starred"))["albumList2"].get("album")     # an artist or a song is not an album
    call(c, "star", albumId=ALBUM)
    listed = body(call(c, "getAlbumList2", type="starred"))["albumList2"]["album"]
    assert [a["id"] for a in listed] == [ALBUM] and listed[0]["starred"]
    assert [a["id"] for a in body(call(c, "getAlbumList", type="starred"))["albumList"]["album"]] == [ALBUM]


def test_album_lists_search_and_folders_carry_the_heart(lib):
    c = make(lib)
    call(c, "star", albumId=ALBUM)
    newest = body(call(c, "getAlbumList2", type="alphabeticalByName"))["albumList2"]["album"]
    assert {a["name"]: bool(a.get("starred")) for a in newest} == {"Ompa": True, "Solo": False}
    found = body(call(c, "search3", query="kaiz"))["searchResult3"]
    assert found["album"][0]["starred"]
    children = body(call(c, "getMusicDirectory", id=ARTIST))["directory"]["child"]
    assert [bool(x.get("starred")) for x in children if x["isDir"]] == [True]
    assert body(call(c, "getArtist", id=ARTIST))["artist"]["album"][0]["starred"]


# ---- A6: musicFolderId --------------------------------------------------------------------------------------------------
def test_music_folder_id_limits_every_list_that_takes_it(tmp_path, lib):
    c = two_libraries(tmp_path, lib)
    names = lambda r: [a["name"] for g in r["index"] for a in g["artist"]]                     # noqa: E731
    assert names(body(call(c, "getArtists"))["artists"]) == ["Kaizers", "Solo", "Wardruna"]
    assert names(body(call(c, "getArtists", musicFolderId="2"))["artists"]) == ["Wardruna"]
    assert names(body(call(c, "getArtists", musicFolderId="1"))["artists"]) == ["Kaizers", "Solo"]
    for kind in ("alphabeticalByName", "random", "alphabeticalByArtist"):
        listed = body(call(c, "getAlbumList2", type=kind, musicFolderId="2"))["albumList2"]["album"]
        assert [a["name"] for a in listed] == ["Runaljod"], kind
    everything = body(call(c, "search3", query="", musicFolderId="1"))["searchResult3"]
    assert [a["name"] for a in everything["album"]] == ["Ompa", "Solo"]
    assert [a["name"] for a in everything["artist"]] == ["Kaizers", "Solo"]
    songs = body(call(c, "getRandomSongs", size="50", musicFolderId="2"))["randomSongs"]["song"]
    assert songs and all(s["path"].startswith("Wardruna/") for s in songs)
    call(c, "star", albumId=ALBUM)
    call(c, "star", albumId=subsonic.make_id("more", ("Wardruna", "Runaljod")))
    assert [a["name"] for a in body(call(c, "getStarred2", musicFolderId="2"))["starred2"]["album"]] == ["Runaljod"]
    assert [a["name"] for a in body(call(c, "getAlbumList2", type="starred", musicFolderId="1"))["albumList2"]["album"]] == ["Ompa"]
    assert body(call(c, "getArtists", musicFolderId="9"))["error"]["code"] == 70


# ---- A2: covers at the size the app asks for ------------------------------------------------------------------------------
def _cover(c, size=None):
    params = {"u": "alice", "p": KEY, "id": ALBUM, **({"size": size} if size is not None else {})}
    return c.get("/rest/getCoverArt", params=params)


def test_a_cover_is_scaled_to_the_size_asked_for(tmp_path, lib):
    photo = _photo()
    (lib / "Kaizers" / "Ompa" / "folder.jpg").write_bytes(photo)
    c = two_libraries(tmp_path, lib, data_dir=str(tmp_path / "data"))
    assert _cover(c).content == photo                                                       # no size: the original, as before
    assert _cover(c, "2000").content == photo                                               # more than the largest width: the original
    small = _cover(c, "300")
    assert small.status_code == 200 and small.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(small.content)) as im:
        assert im.size == (320, 213)                                                        # rounded up to a thumbnail width
    assert len(small.content) < len(photo)
    assert len(list((tmp_path / "data" / "thumbs").glob("*.jpg"))) == 1                      # kept on disk
    assert _cover(c, "300").content == small.content                                        # the second time from the cache
    assert _cover(c, "abc").content == photo                                                # junk: the original


def test_a_picture_that_cannot_be_scaled_is_sent_as_it_is(lib):
    c = make(lib)                                                                           # folder.jpg is not a real JPEG
    r = _cover(c, "160")
    assert r.status_code == 200 and r.content.startswith(b"\xff\xd8")


@pytest.mark.parametrize("size", ["96", "640"])
def test_an_embedded_song_picture_is_scaled_too(lib, size):
    c = make(lib)
    r = c.get("/rest/getCoverArt", params={"u": "alice", "p": KEY, "id": SONG, "size": size})
    assert r.status_code == 200
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.width <= int(size)

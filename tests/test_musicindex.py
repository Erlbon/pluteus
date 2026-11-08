"""The music index: which folders are albums and artists (notes first, then the folder shape), its copy on disk, the background
rescan, and the marker files checked per user."""
import shutil
import time

import pytest
from starlette.testclient import TestClient

from pluteus import musicindex, subsonic
from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import parse_config
from pluteus.fs import Library
from test_subsonic import KEY, TAGGED

SONGS = {
    # the owner's own layout: genre / letter / artist / album
    "0 - Bands/H/Handsome Family, The/[2016] - Unseen/01 Gold.mp3": TAGGED,
    "0 - Bands/H/Handsome Family, The/[2016] - Unseen/02 Silver.mp3": None,
    "0 - Bands/H/Handsome Family, The/[2009] - Honey Moon/01 Linger.mp3": None,
    "1.5 - Hip-hop/NO/Jaa9 & OnklP/[2015] - Ferie/06 Jens.mp3": None,
    # a double album in disc folders
    "0 - Bands/Q/Queen/[1975] - Night/CD1/01 Death.mp3": None,
    "0 - Bands/Q/Queen/[1975] - Night/CD2/01 Bohemian.mp3": None,
    # an album.md album whose parts are folders without disc names
    "3 - Classical/Bach/Passions/album.md": "note",
    "3 - Classical/Bach/Passions/St John/01.mp3": None,
    "3 - Classical/Bach/Passions/St Matthew/01.mp3": None,
    # an artist.md two levels above the album
    "3 - Classical/Mozart/artist.md": "note",
    "3 - Classical/Mozart/Operas/Figaro/01.mp3": None,
    # loose songs in an artist folder, next to artist folders, or next to the artist's own albums
    "0 - Bands/W/Wig Wam/01 Hit.mp3": None,
    "0 - Bands/W/We/[2008] - Tension/01.mp3": None,
    "0 - Bands/Y/Yello/single.mp3": None,
    "0 - Bands/Y/Yello/[1980] - Solid/01.mp3": None,
    # a box set in named disc folders, with and without an artist.md
    "0 - Bands/T/Tom Lehrer/artist.md": "note",
    "0 - Bands/T/Tom Lehrer/Remains/Disc 2 - Live/[1959] - Evening/01.mp3": None,
    "0 - Bands/T/Toto/Box/Disc 1 - Studio/[1978] - Toto/01.mp3": None,
    # songs right in a top folder: an album of its own
    "Solo/single.mp3": None,
    # not music
    "Radio/nrk.m3u": "#EXTM3U",
    "0 - Bands/H/Handsome Family, The/Scans/front.jpg": "jpg",
}


@pytest.fixture
def lib(tmp_path):
    top = tmp_path / "music"
    for rel, content in SONGS.items():
        path = top / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if content == TAGGED:
            shutil.copy(TAGGED, path)
        else:
            path.write_bytes(b"ID3" + b"\x00" * 300 if content is None else content.encode())
    return top


def config(top, **server):
    return parse_config({"server": {"subsonic": True, **server}, "roots": [{"name": "music", "path": str(top)}],
                         "users": [{"name": "alice", "password_hash": hash_password("pw-alice", n=2 ** 4), "roots": ["*"]},
                                   {"name": "grown", "password_hash": hash_password("pw-grown", n=2 ** 4), "roots": ["*"], "adult": True}]})


def albums_of(top):
    found = musicindex.scan_tree(Library(config(top)), "music", str(top))
    return {"/".join(a.parts): ("/".join(a.artist), a.songs) for a in found}


def make(top, **server):
    app = create_app(config(top, **server))
    app.state.state.set_appkey("alice", KEY)
    app.state.state.set_appkey("grown", "grown-key-123456")
    return TestClient(app)


def api(client, name, user="alice", **params):
    key = KEY if user == "alice" else "grown-key-123456"
    return client.get("/rest/%s.view" % name, params={"u": user, "p": key, "f": "json", **params}).json()["subsonic-response"]


def artist_names(client, user="alice"):
    return [a["name"] for g in api(client, "getArtists", user)["artists"]["index"] for a in g["artist"]]


# ---- the rules ------------------------------------------------------------------------------------------------------------
def test_albums_and_artists_follow_notes_first_then_the_folder_shape(lib):
    assert albums_of(lib) == {
        "0 - Bands/H/Handsome Family, The/[2016] - Unseen": ("0 - Bands/H/Handsome Family, The", 2),
        "0 - Bands/H/Handsome Family, The/[2009] - Honey Moon": ("0 - Bands/H/Handsome Family, The", 1),
        "1.5 - Hip-hop/NO/Jaa9 & OnklP/[2015] - Ferie": ("1.5 - Hip-hop/NO/Jaa9 & OnklP", 1),
        "0 - Bands/Q/Queen/[1975] - Night": ("0 - Bands/Q/Queen", 2),                          # CD1 + CD2: one album
        "3 - Classical/Bach/Passions": ("3 - Classical/Bach", 2),                              # album.md: its folders are its parts
        "3 - Classical/Mozart/Operas/Figaro": ("3 - Classical/Mozart", 1),                     # artist.md two levels up
        "0 - Bands/W/Wig Wam": ("0 - Bands/W/Wig Wam", 1),                                     # not by the letter W
        "0 - Bands/W/We/[2008] - Tension": ("0 - Bands/W/We", 1),
        "0 - Bands/Y/Yello": ("0 - Bands/Y/Yello", 1),                                           # the loose single is Yello's
        "0 - Bands/Y/Yello/[1980] - Solid": ("0 - Bands/Y/Yello", 1),
        "0 - Bands/T/Tom Lehrer/Remains/Disc 2 - Live/[1959] - Evening": ("0 - Bands/T/Tom Lehrer", 1),     # artist.md 3 up
        "0 - Bands/T/Toto/Box/Disc 1 - Studio/[1978] - Toto": ("0 - Bands/T/Toto/Box", 1),         # never the disc folder
        "Solo": ("Solo", 1),
    }


def test_marked_folders_are_kept_with_the_album(lib):
    (lib / "0 - Bands" / "H" / ".adult").write_text("")
    found = {a.parts[-1]: a.guard for a in musicindex.scan_tree(Library(config(lib)), "music", str(lib))}
    assert found["[2016] - Unseen"] == (("0 - Bands", "H"),) and found["Solo"] == ()


# ---- the API on that layout -------------------------------------------------------------------------------------------------
def test_apps_list_the_real_artists_and_albums_at_any_depth(lib):
    c = make(lib)
    assert artist_names(c) == ["Bach", "Box", "Handsome Family, The", "Jaa9 & OnklP", "Mozart", "Queen", "Solo", "Tom Lehrer", "We",
                               "Wig Wam", "Yello"]
    family = subsonic.make_id("music", ("0 - Bands", "H", "Handsome Family, The"))
    artist = api(c, "getArtist", id=family)["artist"]
    assert artist["albumCount"] == 2 and [a["songCount"] for a in artist["album"]] == [1, 2]       # natural order: 2009 before 2016
    assert all(a["artistId"] == family and a["artist"] == "Handsome Family, The" for a in artist["album"])
    night = api(c, "getAlbum", id=subsonic.make_id("music", ("0 - Bands", "Q", "Queen", "[1975] - Night")))["album"]
    assert night["songCount"] == 2 and night["artist"] == "Queen" and all(s["artistId"] == night["artistId"] for s in night["song"])
    assert all(s["albumId"] == night["id"] for s in night["song"])                         # a song in CD1 belongs to the album above
    passions = api(c, "getAlbum", id=subsonic.make_id("music", ("3 - Classical", "Bach", "Passions")))["album"]
    assert passions["songCount"] == 2
    unseen = api(c, "getAlbum", id=subsonic.make_id("music", ("0 - Bands", "H", "Handsome Family, The", "[2016] - Unseen")))["album"]
    assert unseen["song"][1]["artistId"] == family                                         # a song without tags gets the album's artist
    listed = api(c, "getAlbumList2", type="alphabeticalByArtist", size="50")["albumList2"]["album"]
    assert [a["artist"] for a in listed][:3] == ["Bach", "Box", "Handsome Family, The"]
    found = api(c, "search3", query="jaa9")["searchResult3"]
    assert [a["name"] for a in found["artist"]] == ["Jaa9 & OnklP"]


def test_folder_browsing_is_still_the_folders_as_they_are(lib):
    c = make(lib)
    top = [a["name"] for g in api(c, "getIndexes")["indexes"]["index"] for a in g["artist"]]
    assert "Radio" not in top and len(top) == 4                                             # the four top folders with music
    bands = api(c, "getMusicDirectory", id=subsonic.make_id("music", ("0 - Bands",)))["directory"]["child"]
    assert [x["title"] for x in bands] == ["H", "Q", "T", "W", "Y"]


def test_an_adult_folder_hides_its_albums_from_users_who_may_not_see_it(lib):
    (lib / "0 - Bands" / "H" / ".adult").write_text("")
    c = make(lib)
    assert "Handsome Family, The" not in artist_names(c)
    assert "Handsome Family, The" in artist_names(c, "grown")
    assert all("Handsome" not in a["artist"] for a in api(c, "getAlbumList2", type="random", size="50")["albumList2"]["album"])


# ---- the copy on disk and the rescan ------------------------------------------------------------------------------------------
def test_the_copy_on_disk_is_used_after_a_restart_and_a_moved_library_is_walked_again(lib, tmp_path, monkeypatch):
    path = str(tmp_path / "cache" / "musicindex.sqlite3")
    first = musicindex.MusicIndex(Library(config(lib)), path)
    assert len(first.albums("music", str(lib))) == 13

    def no_walk(*a, **k):
        raise AssertionError("walked again")
    monkeypatch.setattr(musicindex, "scan_tree", no_walk)
    second = musicindex.MusicIndex(Library(config(lib)), path)
    again = second.albums("music", str(lib))
    assert len(again) == 13 and second.album("music", str(lib), ("Solo",)).songs == 1
    assert second.albums("music", str(tmp_path / "elsewhere")) == []                       # another folder: never the old lists (its walk failed here)


def test_start_scan_picks_up_a_new_album_without_waiting(lib):
    c = make(lib)
    assert "Newcomer" not in artist_names(c)
    (lib / "0 - Bands" / "N" / "Newcomer" / "First").mkdir(parents=True)
    (lib / "0 - Bands" / "N" / "Newcomer" / "First" / "01.mp3").write_bytes(b"ID3" + b"\x00" * 300)
    assert "Newcomer" not in artist_names(c)                                                # the lists are kept between walks
    assert api(c, "startScan")["scanStatus"]["scanning"] is True
    deadline = time.monotonic() + 10
    while "Newcomer" not in artist_names(c) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "Newcomer" in artist_names(c)
    status = api(c, "getScanStatus")["scanStatus"]
    assert status["count"] == 17 and status["scanning"] in (True, False)                   # the songs in the albums, the new one included


def test_starred_folders_are_artists_or_albums_by_what_the_index_says(lib):
    c = make(lib)
    family = subsonic.make_id("music", ("0 - Bands", "H", "Handsome Family, The"))
    night = subsonic.make_id("music", ("0 - Bands", "Q", "Queen", "[1975] - Night"))
    solo = subsonic.make_id("music", ("Solo",))
    for ident in (family, night, solo):
        api(c, "star", id=ident)
    starred = api(c, "getStarred2")["starred2"]
    assert [a["id"] for a in starred["artist"]] == [family]
    assert sorted(a["id"] for a in starred["album"]) == sorted([night, solo])                 # a folder that is both is an album
    assert sorted(a["id"] for a in api(c, "getAlbumList2", type="starred")["albumList2"]["album"]) == sorted([night, solo])

"""Music apps on a library sorted Letter/Artist/Album (artist_level = 2): artists, albums and lists are read from the folders when an
app asks, and every call opens only the few folders it needs. Nothing is walked, built or kept."""
import pytest
from starlette.testclient import TestClient

from pluteus import subsonic
from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import ConfigError, parse_config
from test_subsonic import KEY

LETTERS = "ABC"
ARTISTS = 20                       # per letter
ALBUMS = 3                         # per artist


@pytest.fixture
def bands(tmp_path):
    top = tmp_path / "bands"
    for letter in LETTERS:
        for n in range(ARTISTS):
            for a in range(ALBUMS):
                album = top / letter / ("%s Band %02d" % (letter, n)) / ("[%d] - Album %d" % (2000 + a, a))
                album.mkdir(parents=True)
                (album / "01 song.mp3").write_bytes(b"ID3" + b"\x00" * 300)
    loose = top / "A" / "A Band 00" / "single.mp3"                       # loose songs in an artist folder
    loose.write_bytes(b"ID3" + b"\x00" * 300)
    box = top / "B" / "B Band 00" / "[1999] - Box"                       # an album in disc folders
    for disc in ("CD1", "CD2"):
        (box / disc).mkdir(parents=True)
        (box / disc / "01.mp3").write_bytes(b"ID3" + b"\x00" * 300)
    (top / "C" / "C Band 00" / "Scans").mkdir()                          # no songs: not an album
    (top / "C" / "C Band 00" / "Scans" / "front.jpg").write_bytes(b"\xff\xd8\xff")
    return top


def make(top, level=2):
    cfg = parse_config({"server": {"subsonic": True}, "roots": [{"name": "bands", "path": str(top), "artist_level": level}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw-alice", n=2 ** 4), "roots": ["*"]}]})
    app = create_app(cfg)
    app.state.state.set_appkey("alice", KEY)
    c = TestClient(app)
    api = [r for r in app.routes if getattr(r, "path", "") == "/rest/{name}"][0].endpoint.__self__
    return c, api


def call(c, name, **params):
    return c.get("/rest/%s.view" % name, params={"u": "alice", "p": KEY, "f": "json", **params}).json()["subsonic-response"]


@pytest.fixture
def opened(monkeypatch):
    """Counts the folder listings of the calls made inside it."""
    calls = []

    def watch(api):
        real = api.lib.list_dir

        def counting(*a, **k):
            calls.append(a[2] if len(a) > 2 else k.get("parts"))
            return real(*a, **k)
        monkeypatch.setattr(api.lib, "list_dir", counting)
        return calls
    return watch


def test_the_setting_is_a_small_whole_number():
    base = {"roots": [{"name": "r", "path": ".", "artist_level": 0}], "users": [{"name": "u", "password_hash": "scrypt$1$2$3$4$5"}]}
    for bad in (0, 6, "2", True, 1.5):
        base["roots"][0]["artist_level"] = bad
        with pytest.raises(ConfigError, match="artist_level"):
            parse_config(base, check_roots=False)
    base["roots"][0]["artist_level"] = 3
    assert parse_config(base, check_roots=False).roots["r"].artist_level == 3
    del base["roots"][0]["artist_level"]
    assert parse_config(base, check_roots=False).roots["r"].artist_level == 1


def test_artists_are_the_folders_at_the_artist_level_read_from_the_letters_only(bands, opened):
    c, api = make(bands)
    calls = opened(api)
    names = [a["name"] for g in call(c, "getArtists")["artists"]["index"] for a in g["artist"]]
    assert len(names) == 60 and names[0] == "A Band 00" and "A" not in names
    assert len(calls) == 1 + len(LETTERS)                                                # the top folder and the three letters, nothing more


def test_an_artist_and_its_albums_come_from_its_own_folder(bands, opened):
    c, api = make(bands)
    calls = opened(api)
    artist = call(c, "getArtist", id=subsonic.make_id("bands", ("A", "A Band 00")))["artist"]
    assert [a["name"] for a in artist["album"]] == ["A Band 00", "[2000] - Album 0", "[2001] - Album 1", "[2002] - Album 2"]
    assert artist["album"][0]["songCount"] == 1                                         # the loose single, under the artist's name
    assert all(a["artist"] == "A Band 00" for a in artist["album"])
    assert len(calls) <= 1 + 1 + ALBUMS                                                 # the folder check, the artist folder, one per album
    box = call(c, "getAlbum", id=subsonic.make_id("bands", ("B", "B Band 00", "[1999] - Box")))["album"]
    assert box["songCount"] == 2 and box["artist"] == "B Band 00"                        # CD1 + CD2
    scans = call(c, "getArtist", id=subsonic.make_id("bands", ("C", "C Band 00")))["artist"]
    assert "Scans" not in [a["name"] for a in scans["album"]]
    song = box["song"][0]
    assert song["artistId"] == subsonic.make_id("bands", ("B", "B Band 00"))


def test_alphabetical_pages_open_only_what_they_show_and_continue_where_the_last_one_stopped(bands, opened):
    c, api = make(bands)
    calls = opened(api)
    first = call(c, "getAlbumList2", type="alphabeticalByArtist", size="10")["albumList2"]["album"]
    assert len(first) == 10 and first[0]["artist"] == "A Band 00"
    used = len(calls)
    assert used < 30                                                                    # the letters and the first few artists, not 60
    second = call(c, "getAlbumList2", type="alphabeticalByArtist", size="10", offset="10")["albumList2"]["album"]
    assert len(second) == 10 and second[0]["id"] not in {a["id"] for a in first}
    assert len(calls) - used < 30                                                       # went on from there, did not start over
    every = []
    offset = 0
    while True:
        page = call(c, "search3", query="", albumCount="50", albumOffset=str(offset), artistCount="0")["searchResult3"].get("album", [])
        every += page
        if len(page) < 50:
            break
        offset += 50
    assert len(every) == 60 * ALBUMS + 2 and len({a["id"] for a in every}) == len(every)   # the whole library once: + the single, + the box


def test_random_lists_open_a_few_folders_each(bands, opened):
    c, api = make(bands)
    calls = opened(api)
    picked = call(c, "getAlbumList2", type="random", size="10")["albumList2"]["album"]
    assert len(picked) == 10 and len({a["id"] for a in picked}) == 10
    assert len(calls) < 100
    songs = call(c, "getRandomSongs", size="5")["randomSongs"]["song"]
    assert len(songs) == 5


def test_a_search_opens_only_the_artists_whose_names_match(bands, opened):
    c, api = make(bands)
    calls = opened(api)
    found = call(c, "search3", query="b band 0")["searchResult3"]
    assert len(found["artist"]) == 10 and found["album"] and found["song"]
    assert len(calls) < 1 + len(LETTERS) + 10 * (1 + ALBUMS + 2) + 10


def test_newest_needs_every_folder_and_is_honestly_empty(bands):
    c, _ = make(bands)
    assert not call(c, "getAlbumList2", type="newest")["albumList2"].get("album")
    assert call(c, "getScanStatus")["scanStatus"] == {"scanning": False, "count": 0}


def test_level_one_reads_the_top_folders_as_artists(bands):
    c, _ = make(bands, level=1)
    names = [a["name"] for g in call(c, "getArtists")["artists"]["index"] for a in g["artist"]]
    assert names == ["A", "B", "C"]

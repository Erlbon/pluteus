"""The folders say how they are arranged: `layout:` in the front matter of a folder's about.md tells music apps how many folders down the
artists are, for everything inside it (a nearer note wins), and album.md makes a folder an album even when its songs sit two folders
down. One library can mix depths, as the owner's music folder does."""
import pytest
from starlette.testclient import TestClient

from pluteus import subsonic
from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import parse_config
from test_subsonic import KEY

NOTE = "---\nlayout: %s\n---\n%s\n"
SONG = b"ID3" + b"\x00" * 300
FILES = {
    "0 - Bands/about.md": NOTE % ("letter/artist/album", "Bands, by first letter."),
    "0 - Bands/H/Handsome Family, The/[2016] - Unseen/01 Gold.mp3": SONG,
    "0 - Bands/H/Handsome Family, The/[2009] - Honey Moon/01 Linger.mp3": SONG,
    "0 - Bands/T/Tom Lehrer/Remains/album.md": "A box set.",
    "0 - Bands/T/Tom Lehrer/Remains/Disc 1 - Studio/[1953] - Songs/01.mp3": SONG,
    "0 - Bands/T/Tom Lehrer/Remains/Disc 2 - Live/[1959] - Evening/01.mp3": SONG,
    "0 - Bands/T/Toto/Box/Disc 1/[1978] - Toto/01.mp3": SONG,                   # the same shape without album.md: not an album
    "1.5 - Hip-hop/about.md": NOTE % ("country/artist/album", "Hip-hop by country."),
    "1.5 - Hip-hop/NO/Jaa9 & OnklP/[2015] - Ferie/06 Jens.mp3": SONG,
    "6.2 - Compilations/about.md": NOTE % ("album", "Various artists."),
    "6.2 - Compilations/Pop Goes/01.mp3": SONG,
    "Solo/single.mp3": SONG,                                                   # no note: the library's default, the top folder
    "Plain/about.md": "No front matter, just words.",
    "Plain/Some Band/[2001] - First/01.mp3": SONG,
}


@pytest.fixture
def music(tmp_path):
    top = tmp_path / "music"
    for rel, content in FILES.items():
        path = top / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return top


def make(top, **root):
    cfg = parse_config({"server": {"subsonic": True}, "roots": [{"name": "music", "path": str(top), **root}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw-alice", n=2 ** 4), "roots": ["*"]}]})
    app = create_app(cfg)
    app.state.state.set_appkey("alice", KEY)
    return TestClient(app), [r for r in app.routes if getattr(r, "path", "") == "/rest/{name}"][0].endpoint.__self__


def call(c, name, **params):
    return c.get("/rest/%s.view" % name, params={"u": "alice", "p": KEY, "f": "json", **params}).json()["subsonic-response"]


def artist_names(c):
    return [a["name"] for g in call(c, "getArtists")["artists"]["index"] for a in g["artist"]]


def ident(*parts):
    return subsonic.make_id("music", parts)


@pytest.mark.parametrize("value, depth", [("letter/artist/album", 2), ("Country / Artist / Album", 2), ("artist/album", 1),
                                          ("album", 1), ("genre/letter/artists/albums", 3), ("", None), ("folders", None),
                                          (["letter", "artist"], None), (None, None)])
def test_the_layout_line_gives_the_artist_depth(value, depth):
    assert subsonic.layout_depth(value) == depth


def test_each_folder_says_where_its_artists_are_in_one_library(music, monkeypatch):
    c, api = make(music)
    listed = []
    real = api.lib.list_dir
    monkeypatch.setattr(api.lib, "list_dir", lambda *a, **k: listed.append(a[2]) or real(*a, **k))
    assert artist_names(c) == ["Handsome Family, The", "Jaa9 & OnklP", "Plain", "Pop Goes", "Solo", "Tom Lehrer", "Toto"]
    assert len(listed) == 1 + 3 + 3                      # the top, the three top folders with a layout, H, T and NO: no artist folder


def test_an_artist_found_by_a_note_has_its_albums_and_its_songs_point_back_to_it(music):
    c, _ = make(music)
    family = ident("0 - Bands", "H", "Handsome Family, The")
    artist = call(c, "getArtist", id=family)["artist"]
    assert [a["name"] for a in artist["album"]] == ["[2009] - Honey Moon", "[2016] - Unseen"]
    unseen = call(c, "getAlbum", id=ident("0 - Bands", "H", "Handsome Family, The", "[2016] - Unseen"))["album"]
    assert unseen["artist"] == "Handsome Family, The" and unseen["song"][0]["artistId"] == family
    ferie = call(c, "getAlbum", id=ident("1.5 - Hip-hop", "NO", "Jaa9 & OnklP", "[2015] - Ferie"))["album"]
    assert ferie["artist"] == "Jaa9 & OnklP"


def test_album_md_makes_a_box_set_an_album_and_without_it_the_shape_is_not_guessed(music):
    c, _ = make(music)
    lehrer = call(c, "getArtist", id=ident("0 - Bands", "T", "Tom Lehrer"))["artist"]
    assert [(a["name"], a["songCount"]) for a in lehrer["album"]] == [("Remains", 2)]
    box = call(c, "getAlbum", id=ident("0 - Bands", "T", "Tom Lehrer", "Remains"))["album"]
    assert box["songCount"] == 2 and box["artist"] == "Tom Lehrer"
    assert not call(c, "getArtist", id=ident("0 - Bands", "T", "Toto"))["artist"].get("album")


def test_compilations_and_folders_without_a_layout_line(music):
    c, _ = make(music)
    pop = call(c, "getArtist", id=ident("6.2 - Compilations", "Pop Goes"))["artist"]
    assert [a["name"] for a in pop["album"]] == ["Pop Goes"]                   # each album folder is its own artist
    plain = call(c, "getArtist", id=ident("Plain"))["artist"]
    assert [a["name"] for a in plain["album"]] == ["Some Band"]                # a note without a layout line changes nothing


def test_a_nearer_note_wins_over_the_library_default_and_over_a_note_above(music):
    (music / "about.md").write_text(NOTE % ("genre/letter/artist/album", "The whole music folder."), encoding="utf-8")
    c, _ = make(music)
    names = artist_names(c)
    assert "Pop Goes" in names and "Handsome Family, The" in names            # their own notes win over the top one
    assert "Some Band" not in names and "Solo" not in names                   # no note of their own: the top note's depth (3)
    c, _ = make(music, artist_level=2)
    (music / "about.md").unlink()
    c, _ = make(music, artist_level=2)
    assert "Some Band" in artist_names(c)                                     # without any note the library's artist_level counts


def test_stars_and_random_follow_the_notes(music):
    c, _ = make(music)
    family = ident("0 - Bands", "H", "Handsome Family, The")
    unseen = ident("0 - Bands", "H", "Handsome Family, The", "[2016] - Unseen")
    call(c, "star", id=family)
    call(c, "star", id=unseen)
    starred = call(c, "getStarred2")["starred2"]
    assert [a["id"] for a in starred["artist"]] == [family] and [a["id"] for a in starred["album"]] == [unseen]
    picked = call(c, "getAlbumList2", type="random", size="20")["albumList2"]["album"]
    assert picked and all(a["artist"] not in ("H", "T", "NO", "0 - Bands") for a in picked)

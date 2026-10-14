"""A top-level Radio folder (station files) is not an artist: Subsonic apps do not see it among the music."""
from pluteus import subsonic
from test_subsonic import body, call, lib, make  # noqa: F401


def test_a_top_level_radio_folder_is_left_out_of_the_music_lists(lib):
    radio = lib / "Radio" / "Norway"
    radio.mkdir(parents=True)
    (radio / "NRK P3.m3u").write_text("https://example.org/p3\n", encoding="utf-8")
    c = make(lib)
    names = [a["name"] for g in body(call(c, "getArtists"))["artists"]["index"] for a in g["artist"]]
    assert "Radio" not in names and "Kaizers" in names
    folders = [a["name"] for g in body(call(c, "getIndexes"))["indexes"]["index"] for a in g["artist"]]
    assert "Radio" not in folders
    children = [x["title"] for x in body(call(c, "getMusicDirectory", id=subsonic.make_id("music")))["directory"]["child"]]
    assert "Radio" not in children and "Kaizers" in children
    albums = [a["name"] for a in body(call(c, "getAlbumList2", type="alphabeticalByName"))["albumList2"]["album"]]
    assert "Norway" not in albums and "Ompa" in albums


def test_a_radio_folder_deeper_down_is_an_ordinary_folder(lib):
    (lib / "Kaizers" / "Radio").mkdir()
    (lib / "Kaizers" / "Radio" / "x.mp3").write_bytes(b"ID3" + b"\x00" * 300)
    c = make(lib)
    albums = [a["name"] for a in body(call(c, "getAlbumList2", type="alphabeticalByName"))["albumList2"]["album"]]
    assert "Radio" in albums

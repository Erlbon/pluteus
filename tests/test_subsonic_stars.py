"""Subsonic stars (the user's Favourites shelf, the same list as the heart in the web pages) and OpenSubsonic lyrics."""
from starlette.testclient import TestClient  # noqa: F401  (the fixtures come from test_subsonic)

from pluteus import subsonic
from test_subsonic import body, call, lib, make  # noqa: F401

SONG = subsonic.make_id("music", ("Kaizers", "Ompa", "01 spark.mp3"))
ALBUM = subsonic.make_id("music", ("Kaizers", "Ompa"))
OTHER = subsonic.make_id("music", ("Kaizers", "Ompa", "02 other.mp3"))


def starred(c):
    return body(call(c, "getStarred2"))["starred2"]


def test_a_star_lands_on_the_favourites_shelf_and_shows_in_the_lists(lib):
    c = make(lib)
    assert starred(c).get("song") in (None, [])
    assert body(call(c, "star", id=SONG))["status"] == "ok"
    assert [r["path"] for r in c.app.state.state.shelf_list("alice", "favourite")] == ["Kaizers/Ompa/01 spark.mp3"]
    assert [s["id"] for s in starred(c)["song"]] == [SONG]
    album = body(call(c, "getAlbum", id=ALBUM))["album"]
    assert [bool(s.get("starred")) for s in album["song"]] == [True, False]                  # the heart shows in the album view
    assert body(call(c, "getSong", id=SONG))["song"]["starred"]
    assert body(call(c, "getSong", id=OTHER))["song"].get("starred") is None


def test_albums_can_be_starred_and_unstarred_and_other_users_do_not_see_it(lib):
    c = make(lib)
    call(c, "star", albumId=ALBUM)
    assert [a["id"] for a in starred(c)["album"]] == [ALBUM]
    assert body(call(c, "getAlbum", id=ALBUM))["album"]["starred"]
    other = body(c.get("/rest/getStarred2.view", params={"u": "bob", "p": "pw-bob", "f": "json"}))
    assert not other["starred2"].get("album")
    call(c, "unstar", albumId=ALBUM)
    assert not starred(c).get("album")
    assert "starred" not in body(call(c, "getAlbum", id=ALBUM))["album"]


def test_several_ids_in_one_call_and_junk_ids_are_harmless(lib):
    c = make(lib)
    r = c.get("/rest/star.view", params=[("u", "alice"), ("p", "app-key-for-alice-123"), ("f", "json"), ("id", SONG), ("id", OTHER),
                                         ("id", "7"), ("id", "pnot-base64!!"), ("id", subsonic.make_id("music", ("gone.mp3",)))])
    assert r.json()["subsonic-response"]["status"] == "ok"
    assert len(starred(c)["song"]) == 2
    assert body(call(c, "star"))["status"] == "ok"                                          # no id: nothing to do


def test_lyrics_come_timed_from_a_lrc_file_or_plain_from_the_tag(lib):
    c = make(lib)
    (lib / "Kaizers" / "Ompa" / "02 other.lrc").write_text("[00:01.50]Hello\n[00:03.00]World\n", encoding="utf-8")
    timed = body(call(c, "getLyricsBySongId", id=OTHER))["lyricsList"]["structuredLyrics"][0]
    assert timed["synced"] is True and timed["line"] == [{"start": 1500, "value": "Hello"}, {"start": 3000, "value": "World"}]
    plain = body(call(c, "getLyricsBySongId", id=SONG))["lyricsList"]["structuredLyrics"][0]       # tagged.mp3 has plain lyrics in its tag
    assert plain["synced"] is False and any("vers" in line["value"] for line in plain["line"])
    assert body(call(c, "getOpenSubsonicExtensions"))["openSubsonicExtensions"][1]["name"] == "songLyrics"


def test_no_lyrics_is_an_empty_list_and_the_old_call_answers_empty(lib):
    c = make(lib)
    single = subsonic.make_id("music", ("Solo", "single.mp3"))
    assert body(call(c, "getLyricsBySongId", id=single))["lyricsList"] == {}
    assert body(call(c, "getLyricsBySongId", id=subsonic.make_id("music", ("Solo",))))["error"]["code"] == 70
    assert body(call(c, "getLyrics", artist="x", title="y"))["lyrics"] == {}

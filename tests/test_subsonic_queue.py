"""Subsonic: the recently played albums (from the saved positions) and the play queue a music app saves to resume on another device."""
from test_subsonic import KEY, body, call, lib, make  # noqa: F401

from pluteus import subsonic

SPARK = subsonic.make_id("music", ("Kaizers", "Ompa", "01 spark.mp3"))
OTHER = subsonic.make_id("music", ("Kaizers", "Ompa", "02 other.mp3"))
SINGLE = subsonic.make_id("music", ("Solo", "single.mp3"))
FOLDER = subsonic.make_id("music", ("Solo",))


def get(c, name, **params):
    return call(c, name, **params)


def test_nothing_saved_means_an_empty_queue_and_an_empty_recent_list(lib):
    c = make(lib)
    assert body(get(c, "getPlayQueue"))["playQueue"] == {}
    assert not body(get(c, "getAlbumList2", type="recent"))["albumList2"].get("album")


def test_a_saved_queue_comes_back_with_its_songs_current_song_and_position(lib):
    c = make(lib)
    r = c.get("/rest/savePlayQueue.view", params={"u": "alice", "p": KEY, "f": "json", "id": [SPARK, OTHER, SINGLE], "current": OTHER,
                                                  "position": "61000", "c": "Tempo"})
    assert body(r)["status"] == "ok"
    queue = body(get(c, "getPlayQueue"))["playQueue"]
    assert [e["id"] for e in queue["entry"]] == [SPARK, OTHER, SINGLE]
    assert queue["current"] == OTHER and queue["position"] == 61000 and queue["username"] == "alice" and queue["changedBy"] == "Tempo"
    assert queue["changed"].endswith("Z")
    c.get("/rest/savePlayQueue.view", params={"u": "alice", "p": KEY, "f": "json", "id": [SINGLE], "current": "junk", "position": "x"})
    queue = body(get(c, "getPlayQueue"))["playQueue"]
    assert [e["id"] for e in queue["entry"]] == [SINGLE] and queue["current"] == SINGLE and queue["position"] == 0      # a bad value is not trusted


def test_only_songs_the_user_can_reach_are_kept_and_an_empty_save_clears_it(lib):
    c = make(lib)
    c.get("/rest/savePlayQueue.view", params={"u": "alice", "p": KEY, "f": "json",
                                              "id": [SINGLE, "pnot-base64!", subsonic.make_id("other", ("x.mp3",)), FOLDER, subsonic.make_id("music", ("Kaizers", "loose.txt"))]})
    assert [e["id"] for e in body(get(c, "getPlayQueue"))["playQueue"]["entry"]] == [SINGLE]
    (lib / "Solo" / "single.mp3").unlink()                                                          # a file that is gone drops out
    assert body(get(c, "getPlayQueue"))["playQueue"] == {}
    c.get("/rest/savePlayQueue.view", params={"u": "alice", "p": KEY, "f": "json", "id": [SPARK]})
    c.get("/rest/savePlayQueue.view", params={"u": "alice", "p": KEY, "f": "json"})
    assert body(get(c, "getPlayQueue"))["playQueue"] == {}


def test_each_user_has_their_own_queue(lib):
    c = make(lib)
    c.app.state.state.set_appkey("bob", "bob-key-123456")
    c.get("/rest/savePlayQueue.view", params={"u": "alice", "p": KEY, "f": "json", "id": [SPARK]})
    assert body(c.get("/rest/getPlayQueue.view", params={"u": "bob", "p": "bob-key-123456", "f": "json"}))["playQueue"] == {}


def test_recent_albums_come_from_the_saved_positions_newest_first(lib):
    c = make(lib)
    state = c.app.state.state
    state.put_progress("alice", "music", "Kaizers/Ompa/01 spark.mp3", "12", 0.3, now=1000)
    state.put_progress("alice", "music", "Solo/single.mp3", "5", 0.5, now=2000)
    state.put_progress("alice", "music", "Kaizers/Ompa/02 other.mp3", "9", 0.1, now=3000)             # same album as the first: listed once, newest
    state.put_progress("alice", "music", "Kaizers/loose.txt", "1", 1.0, now=4000)                     # not audio: left out
    state.put_progress("bob", "music", "Solo/single.mp3", "5", 0.5, now=5000)                         # another user's
    albums = body(get(c, "getAlbumList2", type="recent"))["albumList2"]["album"]
    assert [a["id"] for a in albums] == [subsonic.make_id("music", ("Kaizers", "Ompa")), subsonic.make_id("music", ("Solo",))]

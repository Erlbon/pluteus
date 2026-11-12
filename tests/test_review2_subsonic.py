"""The Subsonic fixes of the code review of 2026-11-12 (cards 2, 3, 8, 10, 28, 30, 32, 47, 48 and 49), one test or more per card."""
import dataclasses
import hashlib
import os
import xml.etree.ElementTree as ET

import pytest
from starlette.testclient import TestClient

from pluteus import subsonic
from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import MAX_ARTIST_LEVEL, parse_config
from pluteus.fs import PathError

KEY = "app-key-for-alice-123"
SONG = b"ID3" + b"\x00" * 300
NS = "{http://subsonic.org/restapi}"


def tree(top, *files):
    for rel in files:
        path = top.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(SONG)
    return top


@pytest.fixture
def music(tmp_path):
    """Six artists with one album each, at the top (artist_level 1)."""
    return tree(tmp_path / "music", *["Band %d/Album %d/01 song.mp3" % (n, n) for n in range(6)])


def make(top, level=1):
    cfg = parse_config({"server": {"subsonic": True}, "roots": [{"name": "music", "path": str(top), "artist_level": level}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw-alice", n=2 ** 4), "roots": ["*"]},
                                  {"name": "bob", "password_hash": hash_password("pw-bob", n=2 ** 4), "roots": ["*"]}]})
    app = create_app(cfg)
    app.state.state.set_appkey("alice", KEY)
    api = [r for r in app.routes if getattr(r, "path", "") == "/rest/{name}"][0].endpoint.__self__
    return TestClient(app), api, app


def get(c, name, /, **params):
    return c.get("/rest/%s.view" % name, params={"u": "alice", "p": KEY, "c": "test", "f": "json", **params})


def call(c, name, /, **params):
    r = get(c, name, **params)
    assert r.status_code == 200
    return r.json()["subsonic-response"]


def code(answer) -> int:
    return answer["error"]["code"]


@pytest.fixture
def opens(monkeypatch):
    """The paths subsonic.py itself opens (the layout notes), in order."""
    seen = []

    def spy(path, *a, **k):
        seen.append(str(path))
        return open(path, *a, **k)

    monkeypatch.setattr(subsonic, "open", spy, raising=False)
    return seen


def listings(api):
    """Records every folder listing and every later read of sizes and dates: ([(parts, stat)], [entries])."""
    listed, statted = [], []
    real_list, real_stats = api.lib.list_dir, api.lib.with_stats

    def list_dir(user, root, parts, all_files=False, stat=True):
        listed.append((tuple(parts), stat))
        return real_list(user, root, parts, all_files, stat)

    def with_stats(entries):
        statted.extend(entries)
        return real_stats(entries)

    api.lib.list_dir, api.lib.with_stats = list_dir, with_stats
    return listed, statted


# ---- card 2: only a verified real password clears a name's failures ---------------------------------------------------------
def test_key_and_token_logins_do_not_clear_the_name_lock(music):
    c, api, _ = make(music)
    cleared = []
    api.throttle.ok = lambda request, name: cleared.append(name)
    token = hashlib.md5((KEY + "salt1").encode()).hexdigest()
    assert c.get("/rest/ping", params={"u": "alice", "p": KEY, "f": "json"}).json()["subsonic-response"]["status"] == "ok"
    assert c.get("/rest/ping", params={"apiKey": KEY, "f": "json"}).json()["subsonic-response"]["status"] == "ok"
    assert c.get("/rest/ping", params={"u": "alice", "t": token, "s": "salt1", "f": "json"}).json()["subsonic-response"]["status"] == "ok"
    assert cleared == []
    assert c.get("/rest/ping", params={"u": "alice", "p": "pw-alice", "f": "json"}).json()["subsonic-response"]["status"] == "ok"
    assert cleared == ["alice"]


def test_a_key_login_leaves_a_locked_name_locked(music):
    c, _, app = make(music)
    for _ in range(12):
        c.get("/rest/ping", params={"u": "alice", "p": "wrong", "f": "json"})
    assert app.state.limiter.wait_seconds("alice", "") > 0
    assert call(c, "ping")["status"] == "ok"                                  # the app key still works ...
    assert app.state.limiter.wait_seconds("alice", "") > 0                    # ... and does not unlock the name for password guesses


# ---- card 3: no id reaches the disk before the library has checked it ---------------------------------------------------------
def test_get_album_checks_the_path_before_anything_is_opened(music, tmp_path, opens):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "about.md").write_text("---\nlayout: artist/album\n---\n")
    c, _, _ = make(music)
    for parts in ((str(outside),), ("..", "outside"), ("Band 0", "..", "..", "outside")):
        assert code(call(c, "getAlbum", id=subsonic.make_id("music", parts))) == 70
    assert opens == []


def test_the_layout_reader_refuses_a_bad_path_part(music, opens):
    _, api, _ = make(music)
    for parts in (("..",), ("a/b",), ("",), ("ok", "..")):
        with pytest.raises(PathError):
            api._layout("music", parts)
    assert opens == []


def test_a_saved_position_outside_the_library_is_not_a_recent_album(music, tmp_path, opens):
    (tmp_path / "elsewhere").mkdir()
    c, api, app = make(music, level=3)                                       # level 3: the layout notes of two folders are read on the way
    app.state.state.put_progress("alice", "music", "../elsewhere/x/y/song.mp3", "t:1", 0.5)
    app.state.state.put_progress("alice", "music", "Band 1/Album 1/01 song.mp3", "t:1", 0.5)
    albums = call(c, "getAlbumList2", type="recent")["albumList2"]["album"]
    assert [a["name"] for a in albums] == ["Album 1"]
    assert not [p for p in opens if "elsewhere" in p]


def test_artist_info_checks_the_folder_first(music, tmp_path):
    c, _, _ = make(music)
    assert code(call(c, "getArtistInfo2", id=subsonic.make_id("music", ("..", "outside")))) == 70


# ---- card 8: a changed password stops working at once ----------------------------------------------------------------------
def test_a_changed_password_is_refused_at_once(music):
    c, _, app = make(music)
    assert call(c, "ping", p="pw-alice")["status"] == "ok"                     # remembered for a few minutes
    auth = app.state.auth
    changed = dataclasses.replace(auth.users["alice"], password_hash=hash_password("new-password", n=2 ** 4))
    auth.set_users({**auth.users, "alice": changed})
    assert code(call(c, "ping", p="pw-alice")) == 40
    assert call(c, "ping", p="new-password")["status"] == "ok"


# ---- card 10: the token login costs the same for every name ----------------------------------------------------------------
def test_the_token_login_never_hashes_a_password_and_the_dummy_secret_opens_nothing(music, monkeypatch):
    c, _, _ = make(music)
    hashed = []
    monkeypatch.setattr(subsonic, "verify_password", lambda *a: hashed.append(a) and False)
    for name in ("nobody", "bob", "alice"):                                   # unknown, no key, wrong token
        token = hashlib.md5(("guess" + "s1").encode()).hexdigest()
        assert code(c.get("/rest/ping", params={"u": name, "t": token, "s": "s1", "f": "json"}).json()["subsonic-response"]) == 40
    assert hashed == []
    dummy = hashlib.md5((subsonic._DUMMY_SECRET + "s1").encode()).hexdigest()
    for name in ("nobody", "bob"):
        assert code(c.get("/rest/ping", params={"u": name, "t": dummy, "s": "s1", "f": "json"}).json()["subsonic-response"]) == 40


# ---- card 28: an offset that continues nothing lists nothing ----------------------------------------------------------------
def test_a_deep_offset_opens_no_folder(music):
    c, api, _ = make(music)
    listed, _ = listings(api)
    assert not call(c, "getAlbumList2", type="alphabeticalByName", offset="1000000", size="1")["albumList2"].get("album")
    assert not call(c, "search3", query="", albumOffset="1000000", albumCount="1", artistCount="0")["searchResult3"].get("album")
    assert not call(c, "getAlbumList2", type="alphabeticalByName", offset="3", size="2")["albumList2"].get("album")
    assert listed == []


def test_a_run_belongs_to_one_client_and_goes_on_page_by_page(music):
    c, _, _ = make(music)

    def page(client, offset):
        found = call(c, "getAlbumList2", type="alphabeticalByName", offset=str(offset), size="2", c=client)["albumList2"].get("album", [])
        return [a["name"] for a in found]

    assert page("phone", 0) == ["Album 0", "Album 1"]
    assert page("car", 0) == ["Album 0", "Album 1"]                            # another device starts its own run ...
    assert page("phone", 2) == ["Album 2", "Album 3"]                          # ... and does not disturb the first
    assert page("car", 4) == []                                                # not where the car's run stopped
    assert page("car", 2) == ["Album 2", "Album 3"]                            # the refused request did not cost the run
    assert page("phone", 2) == ["Album 2", "Album 3"]                          # the same page asked again (a retry) is answered again
    assert page("phone", 4) == ["Album 4", "Album 5"]
    assert page("phone", 6) == []                                              # the end


def test_the_album_list_and_the_search_do_not_share_a_run(music):
    c, _, _ = make(music)
    assert len(call(c, "getAlbumList2", type="alphabeticalByName", size="2")["albumList2"]["album"]) == 2
    assert len(call(c, "search3", query="", albumCount="2", artistCount="0")["searchResult3"]["album"]) == 2
    second = call(c, "getAlbumList2", type="alphabeticalByName", size="2", offset="2")["albumList2"]["album"]
    assert [a["name"] for a in second] == ["Album 2", "Album 3"]


def test_the_number_of_kept_runs_is_limited(music):
    c, api, _ = make(music)
    for n in range(subsonic.MAX_RUNS + 10):
        call(c, "getAlbumList2", type="alphabeticalByName", size="1", c="client%d" % n)
    assert len(api._runs) <= subsonic.MAX_RUNS


# ---- card 30: sizes and dates are read only where they are shown -------------------------------------------------------------
def test_artist_lists_read_no_sizes_or_dates(music):
    c, api, _ = make(music)
    (music / "loose.mp3").write_bytes(SONG)
    listed, statted = listings(api)
    assert len(call(c, "getArtists")["artists"]["index"]) == 1
    assert listed and not [parts for parts, stat in listed if stat] and statted == []
    del listed[:]
    answer = call(c, "getIndexes")["indexes"]
    assert answer["child"][0]["size"] == len(SONG)                             # the songs at the top keep their size
    assert not [parts for parts, stat in listed if stat] and [e.name for e in statted] == ["loose.mp3"]


def test_an_album_page_reads_the_date_of_each_album_folder_and_nothing_of_its_songs(music):
    c, api, _ = make(music)
    listed, statted = listings(api)
    albums = call(c, "getAlbumList2", type="alphabeticalByName", size="3")["albumList2"]["album"]
    assert len(albums) == 3 and all(not a["created"].startswith("1970") for a in albums)
    assert not [parts for parts, stat in listed if stat]
    assert statted and all(e.is_dir for e in statted)
    del statted[:]
    artist = call(c, "getArtist", id=subsonic.make_id("music", ("Band 2",)))["artist"]
    assert not artist["album"][0]["created"].startswith("1970") and all(e.is_dir for e in statted)


def test_songs_keep_their_size_and_an_album_its_date(music):
    c, _, _ = make(music)
    (music / "Band 1" / "single.mp3").write_bytes(SONG)
    album = call(c, "getAlbum", id=subsonic.make_id("music", ("Band 1", "Album 1")))["album"]
    assert album["song"][0]["size"] == len(SONG) and not album["created"].startswith("1970")
    loose = call(c, "getAlbum", id=subsonic.make_id("music", ("Band 1",)))["album"]
    assert loose["song"][0]["size"] == len(SONG) and not loose["created"].startswith("1970")
    folder = call(c, "getMusicDirectory", id=subsonic.make_id("music", ("Band 1", "Album 1")))["directory"]
    assert folder["child"][0]["size"] == len(SONG)
    listed = call(c, "getArtist", id=subsonic.make_id("music", ("Band 1",)))["artist"]["album"]
    assert [a["name"] for a in listed] == ["Band 1", "Album 1"] and not listed[0]["created"].startswith("1970")


# ---- card 32: the layout note is capped like artist_level; one spelling of about.md on Windows --------------------------------
def test_a_layout_note_is_capped_at_the_deepest_artist_level():
    assert subsonic.layout_depth("a/b/c/d/e/f/g/artist") == MAX_ARTIST_LEVEL
    assert subsonic.layout_depth("letter/artist/album") == 2 and subsonic.layout_depth("album") == 1


def test_one_spelling_of_the_layout_note_on_windows_three_elsewhere(music, opens):
    assert subsonic._layout_names(True) == ("about.md",)
    assert subsonic._layout_names(False) == ("about.md", "About.md", "ABOUT.MD")
    assert subsonic._LAYOUT_NAMES == subsonic._layout_names(os.name == "nt")
    _, api, _ = make(music)
    assert api._layout("music", ("Band 0",)) is None
    assert len(opens) == len(subsonic._LAYOUT_NAMES)


# ---- card 47: a box set without disc tags plays disc by disc -----------------------------------------------------------------
def test_discs_without_disc_tags_play_one_after_the_other(tmp_path):
    names = ["Art/Box/%s/%02d.mp3" % (disc, n) for disc in ("CD1", "CD2", "CD10") for n in (1, 2, 3)]
    c, api, _ = make(tree(tmp_path / "music", *names))
    api.meta.song_tags_many = lambda entries: [{"track": int(e.name[:2])} for e in entries]
    songs = call(c, "getAlbum", id=subsonic.make_id("music", ("Art", "Box")))["album"]["song"]
    assert [s["path"] for s in songs] == names                               # CD1 1-3, CD2 1-3, CD10 1-3


def test_disc_tags_still_come_first(tmp_path):
    c, api, _ = make(tree(tmp_path / "music", "Art/Box/A/01.mp3", "Art/Box/B/01.mp3"))
    api.meta.song_tags_many = lambda entries: [{"track": 1, "disc": 2 if "A" in e.parts else 1} for e in entries]
    songs = call(c, "getAlbum", id=subsonic.make_id("music", ("Art", "Box")))["album"]["song"]
    assert [s["path"] for s in songs] == ["Art/Box/B/01.mp3", "Art/Box/A/01.mp3"]


# ---- card 48: small answer bugs ------------------------------------------------------------------------------------------
def test_station_logos_do_not_count_towards_the_radio_list_cut(music):
    for n in range(200):
        (music / "Radio").mkdir(exist_ok=True)
        (music / "Radio" / ("s%03d.m3u" % n)).write_text("http://radio.invalid/s%03d\n" % n)
        (music / "Radio" / ("s%03d.png" % n)).write_bytes(b"\x89PNG")
    c, _, _ = make(music)
    assert len(call(c, "getInternetRadioStations")["internetRadioStations"]["internetRadioStation"]) == 200


def test_create_bookmark_is_accepted_and_does_nothing(music):
    c, _, _ = make(music)
    assert call(c, "createBookmark", id=subsonic.make_id("music", ("Band 0", "Album 0", "01 song.mp3")), position="1000")["status"] == "ok"


@pytest.mark.parametrize("name, params", [
    ("getMusicDirectory", {"id": "²"}),
    ("getMusicDirectory", {"id": "٣"}),
    ("getMusicDirectory", {"id": "9" * 5000}),
    ("getIndexes", {"musicFolderId": "²"}),
    ("getIndexes", {"musicFolderId": "9" * 5000}),
    ("getAlbumList2", {"type": "alphabeticalByName", "musicFolderId": "²"}),
    ("stream", {"id": subsonic.make_id("music", ("Radio", "a.m3u")) + ".²"}),
    ("stream", {"id": subsonic.make_id("music", ("Radio", "a.m3u")) + "." + "9" * 5000}),
    ("getAlbum", {"id": "p" + "A" * 20000}),
    ("getSong", {"id": "p" + "A" * 20000}),
])
def test_odd_numbers_and_absurd_ids_are_not_found_never_a_server_error(music, name, params):
    c, _, _ = make(music)
    assert code(call(c, name, **params)) == 70


def test_an_odd_cover_size_gives_the_whole_picture(music):
    (music / "Band 0" / "Album 0" / "folder.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 40)
    c, _, _ = make(music)
    for size in ("²", "9" * 5000):
        r = get(c, "getCoverArt", id=subsonic.make_id("music", ("Band 0", "Album 0")), size=size)
        assert r.status_code == 200 and r.content.startswith(b"\xff\xd8")


def test_a_value_error_inside_a_call_is_an_api_error(music):
    c, api, _ = make(music)

    def broken(user, p):
        raise ValueError("bad value")

    api.handlers["getsong"] = broken
    assert code(call(c, "getSong", id="x")) == 70


def test_xml_puts_long_text_in_elements():
    assert subsonic._node("line", {"start": 0, "value": "a < b"}) == '<line start="0">a &lt; b</line>'
    assert subsonic._node("artistInfo2", {"biography": "Born.", "notes": "n"}) == "<artistInfo2><biography>Born.</biography><notes>n</notes></artistInfo2>"
    assert subsonic._node("song", {"value": "x"}) == '<song value="x"></song>'            # only a lyric line's value is its text


def test_the_biography_and_the_lyrics_in_xml(music):
    (music / "Band 0" / "artist.md").write_text("# Band 0\n\nFormed in 1999.")
    (music / "Band 0" / "Album 0" / "01 song.lrc").write_text("[00:01.00]first line\n[00:02.00]second line\n[00:03.00]third line\n")
    c, _, _ = make(music)
    root = ET.fromstring(c.get("/rest/getArtistInfo2", params={"u": "alice", "p": KEY, "id": subsonic.make_id("music", ("Band 0",))}).text)
    info = root.find(NS + "artistInfo2")
    assert info.get("biography") is None and "Formed in 1999." in info.find(NS + "biography").text
    song = subsonic.make_id("music", ("Band 0", "Album 0", "01 song.mp3"))
    root = ET.fromstring(c.get("/rest/getLyricsBySongId", params={"u": "alice", "p": KEY, "id": song}).text)
    lines = root.find(NS + "lyricsList").find(NS + "structuredLyrics").findall(NS + "line")
    assert [(line.get("start"), line.text, line.get("value")) for line in lines][0] == ("1000", "first line", None)
    as_json = call(c, "getLyricsBySongId", id=song)["lyricsList"]["structuredLyrics"][0]["line"][0]
    assert as_json == {"start": 1000, "value": "first line"}                   # JSON is as it was


def test_the_extension_list_needs_no_login(music):
    c, _, _ = make(music)
    answer = c.get("/rest/getOpenSubsonicExtensions.view", params={"f": "json"}).json()["subsonic-response"]
    assert answer["status"] == "ok" and answer["openSubsonicExtensions"][0]["name"] == "apiKeyAuthentication"
    root = ET.fromstring(c.get("/rest/getOpenSubsonicExtensions").text)
    assert root.get("status") == "ok" and len(root.findall(NS + "openSubsonicExtensions")) == 2
    assert code(c.get("/rest/ping.view", params={"f": "json"}).json()["subsonic-response"]) == 10      # everything else still does


def test_token_info_names_the_user_of_an_api_key(music):
    c, _, _ = make(music)
    answer = c.get("/rest/tokenInfo.view", params={"apiKey": KEY, "f": "json"}).json()["subsonic-response"]
    assert answer["status"] == "ok" and answer["tokenInfo"] == {"username": "alice"}
    assert code(c.get("/rest/tokenInfo.view", params={"apiKey": "wrong", "f": "json"}).json()["subsonic-response"]) == 44


# ---- card 49: a name that is not valid Unicode, or holds control characters, breaks no list ----------------------------------
BAD = "bad\udce9name"                    # what Python hands over for a Latin-1 byte in a name on a UTF-8 file system


def test_an_id_survives_a_name_that_is_not_valid_unicode():
    ident = subsonic.make_id("music", (BAD, "s\udcff.mp3"))
    assert ident.isascii() and subsonic.read_id(ident) == ("music", (BAD, "s\udcff.mp3"))


def test_answers_never_carry_a_character_that_cannot_be_sent():
    body = subsonic.envelope({"a": {"name": BAD, "list": [{"title": "x\x01y"}, "z\udc80"]}})
    assert body["a"] == {"name": "bad�name", "list": [{"title": "x�y"}, "z�"]}
    for fmt in ("json", "xml"):
        subsonic.render(body, fmt).body.decode("utf-8")
    ET.fromstring(subsonic.render(body, "xml").body)


def odd_tree(top, name):
    try:
        tree(top, "%s/Album/01 %s.mp3" % (name, name))
    except (OSError, ValueError):
        pytest.skip("this file system does not take such a name")
    return top


@pytest.mark.parametrize("name", [BAD, "ctl\x01name"])
def test_an_odd_name_on_disk_breaks_no_list_and_still_plays(music, name):
    c, _, _ = make(odd_tree(music, name))
    shown = subsonic.clean(name)
    for fmt in ("json", "xml"):
        for what, params in (("getIndexes", {}), ("getArtists", {}), ("getAlbumList2", {"type": "alphabeticalByName", "size": "50"}),
                             ("search3", {"query": ""}), ("getMusicDirectory", {"id": "1"})):
            r = get(c, what, f=fmt, **params)
            assert r.status_code == 200 and shown in r.text, what
            if fmt == "xml":
                assert ET.fromstring(r.text).get("status") == "ok", what
    artist = [a for i in call(c, "getArtists")["artists"]["index"] for a in i["artist"] if a["name"] == shown][0]
    album = call(c, "getArtist", id=artist["id"])["artist"]["album"][0]
    song = call(c, "getAlbum", id=album["id"])["album"]["song"][0]
    assert song["title"] == "01 " + shown and song["size"] == len(SONG)
    assert call(c, "getSong", id=song["id"])["song"]["id"] == song["id"]
    assert get(c, "stream", id=song["id"]).content == SONG
    assert get(c, "download", id=song["id"]).content == SONG
    assert call(c, "star", id=song["id"])["status"] == "ok"                    # a star on such a name is dropped, the call still answers
    assert call(c, "getStarred2")["status"] == "ok"

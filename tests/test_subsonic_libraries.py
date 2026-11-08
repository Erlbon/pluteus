"""Which libraries music apps see: a Subsonic tick per library (and one for the upload folders) on the admin page's Libraries tab,
kept as `subsonic = false` on the library and `subsonic_uploads = false` in [server], in use at once."""
import shutil

import pytest
from starlette.testclient import TestClient
from test_adminweb import FAST, FORM, basic
from test_libraries import SAMPLE
from test_subsonic import KEY, TAGGED

from pluteus import auth as authmod
from pluteus import libedit, subsonic
from pluteus.adminapp import create_admin_app
from pluteus.app import create_app
from pluteus.config import ConfigError, load_config, parse_config
from pluteus.useredit import EditError, read_config_text

BOSS = basic("boss", "boss-password-1")


# ---- the text of config.toml ----------------------------------------------------------------------------------------------
def test_hiding_libraries_writes_false_lines_and_showing_them_again_gives_back_the_file():
    hidden = libedit.set_subsonic_text(SAMPLE, {"books"}, False)
    comics = hidden[hidden.index('name = "comics"'):hidden.index("# users below")]
    assert "subsonic = false" in comics and "subsonic" not in hidden[:hidden.index('name = "comics"')].replace("subsonic_uploads", "")
    server = hidden[hidden.index("[server]"):hidden.index("# the books")]
    assert "subsonic_uploads = false" in server
    assert "# my comment, keep it" in hidden and "# trailing" in hidden and "# the comics (comment above the block)" in hidden
    assert libedit.set_subsonic_text(hidden, {"books", "comics"}, True) == SAMPLE


def test_an_unknown_library_is_refused_and_a_file_without_server_gets_one_at_the_end():
    with pytest.raises(EditError, match="no library"):
        libedit.set_subsonic_text(SAMPLE, {"nope"}, True)
    bare = SAMPLE.replace("[server]\nport = 8080   # trailing\n", "")
    new = libedit.set_subsonic_text(bare, {"books", "comics"}, False)
    assert new.rstrip().endswith("[server]\nsubsonic_uploads = false")
    assert parse_config_text(new).subsonic_uploads is False


def parse_config_text(text):
    import tomllib
    return parse_config(tomllib.loads(text), check_roots=False)


def test_the_setting_must_be_true_or_false():
    user = [{"name": "u", "password_hash": "scrypt$1$2$3$4$5"}]
    base = {"roots": [{"name": "r", "path": ".", "subsonic": "no"}], "users": user}
    with pytest.raises(ConfigError, match="subsonic must be true or false"):
        parse_config(base, check_roots=False)
    base["roots"][0]["subsonic"] = False
    assert parse_config(base, check_roots=False).roots["r"].subsonic is False
    assert parse_config({"roots": [{"name": "r", "path": "."}], "users": user}, check_roots=False).roots["r"].subsonic is True


# ---- the admin page and the API, together ---------------------------------------------------------------------------------
@pytest.fixture
def server(tmp_path):
    for root, artist in (("music", "Kaizers"), ("books", "Narrator")):
        album = tmp_path / root / artist / "Album"
        album.mkdir(parents=True)
        shutil.copy(TAGGED, album / "01 song.mp3")
    (tmp_path / "up" / "alice").mkdir(parents=True)
    users = ""
    for name, password, extra in (("boss", "boss-password-1", "admin = true\n"), ("alice", "alice-password-1", 'roots = ["*"]\n')):
        users += '[[users]]\nname = "%s"\npassword_hash = "%s"\n%s\n' % (name, authmod.hash_password(password, **FAST), extra)
    slash = lambda p: str(p).replace("\\", "/")                                                 # noqa: E731
    text = ('[server]\nport = 8080\nadmin_port = 8101\nsubsonic = true\nuploads_dir = "%s"\n\n'
            '[[roots]]\nname = "music"\npath = "%s"\n\n[[roots]]\nname = "books"\npath = "%s"\n\n%s'
            % (slash(tmp_path / "up"), slash(tmp_path / "music"), slash(tmp_path / "books"), users))
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8", newline="")
    app = create_app(load_config(str(path)), config_path=str(path), reload_interval=0.0)
    app.state.state.set_appkey("alice", KEY)
    admin = TestClient(create_admin_app(app), client=("127.0.0.1", 50000), follow_redirects=False)
    return admin, TestClient(app), path


def api(client, name, **params):
    return client.get("/rest/%s.view" % name, params={"u": "alice", "p": KEY, "f": "json", **params}).json()["subsonic-response"]


def app_state(client):
    return client.app.state.state


def folders(client):
    return [f["name"] for f in api(client, "getMusicFolders")["musicFolders"]["musicFolder"]]


def test_every_library_and_the_upload_folder_are_seen_until_unticked(server):
    admin, main, _ = server
    assert folders(main) == ["music", "books", "uploads"]
    html = admin.get("/libraries", headers=BOSS).text
    assert "Music apps (Subsonic)" in html and "Folders available to Subsonic" in html
    assert html.count("name='root' value=") == 2 and "name='uploads' checked" in html


def test_unticking_a_library_hides_it_from_music_apps_at_once(server):
    admin, main, path = server
    book_album = subsonic.make_id("books", ("Narrator", "Album"))
    assert api(main, "getAlbum", id=book_album)["status"] == "ok"
    r = admin.post("/libraries/subsonic", content="root=music", headers={**BOSS, **FORM})
    assert r.status_code == 303 and r.headers["location"] == "/libraries?ok=libsubsonic"
    cfg = load_config(str(path))
    assert cfg.roots["books"].subsonic is False and cfg.roots["music"].subsonic is True and cfg.subsonic_uploads is False
    assert folders(main) == ["music"]                                                           # no restart needed
    artists = [a["name"] for g in api(main, "getArtists")["artists"]["index"] for a in g["artist"]]
    assert artists == ["Kaizers"]
    assert [a["name"] for a in api(main, "getAlbumList2", type="alphabeticalByName")["albumList2"]["album"]] == ["Album"]
    api(main, "star", albumId=book_album)                                                       # dropped, like any id it may not use
    assert not app_state(main).shelf_list("alice", "favourite")
    for call, params in (("getAlbum", {"id": book_album}), ("getMusicDirectory", {"id": book_album}),
                         ("getArtist", {"id": subsonic.make_id("books", ("Narrator",))}), ("getCoverArt", {"id": book_album})):
        assert api(main, call, **params)["error"]["code"] == 70, call
    song = subsonic.make_id("books", ("Narrator", "Album", "01 song.mp3"))
    assert main.get("/rest/stream", params={"u": "alice", "p": KEY, "id": song}).headers["content-type"].startswith("text/xml")
    assert main.get("/", headers=basic("alice", "alice-password-1")).status_code == 200            # the web site is not affected
    html = admin.get("/libraries?ok=libsubsonic", headers=BOSS).text
    assert "value='books'>" in html and "value='music' checked" in html and "name='uploads'>" in html


def test_ticking_everything_again_removes_the_lines(server):
    admin, main, path = server
    before = read_config_text(str(path))
    admin.post("/libraries/subsonic", content="root=music", headers={**BOSS, **FORM})
    admin.post("/libraries/subsonic", content="root=music&root=books&uploads=on", headers={**BOSS, **FORM})
    assert read_config_text(str(path)) == before
    assert folders(main) == ["music", "books", "uploads"]


def test_a_bad_form_is_refused_and_changes_nothing(server):
    admin, main, path = server
    before = read_config_text(str(path))
    r = admin.post("/libraries/subsonic", content="root=nope&uploads=on", headers={**BOSS, **FORM})
    assert r.status_code == 400 and "o library named" in r.text
    assert read_config_text(str(path)) == before and folders(main) == ["music", "books", "uploads"]

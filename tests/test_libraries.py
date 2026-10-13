"""The Libraries tab: text-level editing of [[roots]], the folder picker, live apply, and who may use the picker."""
import os

import pytest
from starlette.testclient import TestClient
from test_adminweb import BOSS, FORM, make_config

from pluteus import folderpick, libedit
from pluteus.adminapp import create_admin_app
from pluteus.app import create_app
from pluteus.config import load_config
from pluteus.useredit import EditError, read_config_text

SAMPLE = """# my comment, keep it
[server]
port = 8080   # trailing

# the books
[[roots]]
name = "books"
path = 'D:\\Books'

# the comics (comment above the block)
[[roots]]
name = "comics"
path = "/library/comics"

# users below
[[users]]
name = "alice"
password_hash = "scrypt$16$8$1$aa$bb"
"""


# ---- editing the file as text -------------------------------------------------------------------------------------------
def test_adding_a_library_keeps_everything_else_and_goes_after_the_last_one():
    new = libedit.add_root_text(SAMPLE, "music", "D:\\Music")
    assert "# my comment, keep it" in new and "# trailing" in new and "# the comics (comment above the block)" in new
    assert new.index('name = "music"') > new.index('name = "comics"')
    assert new.index('name = "music"') < new.index("# users below")                       # before what follows the roots
    assert "path = 'D:\\Music'" in new                                                   # a literal string: backslashes untouched
    assert new.count("[[roots]]") == 3


def test_a_path_with_a_quote_is_written_as_a_safe_string(tmp_path):
    new = libedit.add_root_text(SAMPLE, "odd", "/data/it's here")
    assert 'path = "/data/it\'s here"' in new


@pytest.mark.parametrize("name", ["", "has space", "a/b", "ünï", "x" * 0, "a.b"])
def test_bad_library_names_are_refused(name):
    with pytest.raises(EditError):
        libedit.add_root_text(SAMPLE, name, "/tmp")


def test_a_duplicate_name_and_an_empty_folder_are_refused():
    with pytest.raises(EditError, match="already a library"):
        libedit.add_root_text(SAMPLE, "books", "/x")
    with pytest.raises(EditError):
        libedit.add_root_text(SAMPLE, "ok", "  ")


def test_removing_a_library_keeps_the_comment_that_belongs_to_the_next_block():
    new = libedit.remove_root_text(SAMPLE, "books")
    assert 'name = "books"' not in new and 'name = "comics"' in new
    assert "# the books" in new or "# the comics (comment above the block)" in new       # the neighbour's comment survives
    assert "# my comment, keep it" in new and "# users below" in new
    with pytest.raises(EditError):
        libedit.remove_root_text(SAMPLE, "nothing")


def test_add_and_remove_roundtrip_through_the_real_validation(tmp_path):
    path = make_config(tmp_path)
    folder = tmp_path / "music"
    folder.mkdir()
    cfg = libedit.add_root(str(path), "music", str(folder))
    assert cfg.roots["music"].path == str(folder) and (tmp_path / "config.toml.bak").exists()
    assert "music" in load_config(str(path)).roots
    libedit.remove_root(str(path), "music")
    assert "music" not in load_config(str(path)).roots
    with pytest.raises(EditError, match="not a folder"):
        libedit.add_root(str(path), "ghost", str(tmp_path / "missing"))
    assert "ghost" not in read_config_text(str(path))                                  # nothing written for a refused edit


def test_a_library_that_a_user_is_limited_to_cannot_be_removed_blindly(tmp_path):
    path = make_config(tmp_path)
    text = read_config_text(str(path)).replace('name = "alice"\n', 'name = "alice"\nroots = ["comics"]\n', 1)
    path.write_text(text, encoding="utf-8", newline="")
    with pytest.raises(EditError, match="Users tab"):
        libedit.remove_root(str(path), "comics")
    assert 'name = "comics"' in read_config_text(str(path))


# ---- the folder picker ---------------------------------------------------------------------------------------------------------
@pytest.fixture
def tree(tmp_path):
    for d in ("Music/Rock", "Music/Jazz", "Music/.hidden", "Docs"):
        (tmp_path / "t" / d).mkdir(parents=True)
    (tmp_path / "t" / "Music" / "a-file.mp3").write_text("x")
    return tmp_path / "t"


def test_picker_lists_only_folders_without_hidden_ones_in_natural_order(tree):
    got = folderpick.browse(str(tree / "Music"))
    assert [d["name"] for d in got["dirs"]] == ["Jazz", "Rock"]                           # no file, no .hidden
    assert got["path"] == os.path.realpath(str(tree / "Music")) and got["parent"] == os.path.realpath(str(tree))
    with pytest.raises(FileNotFoundError):
        folderpick.browse(str(tree / "nope"))


def test_picker_with_bases_stays_inside_them(tree):
    bases = (str(tree / "Music"),)
    assert [d["path"] for d in folderpick.browse("", bases)["dirs"]] == [str(tree / "Music")]
    assert folderpick.browse(str(tree / "Music" / "Rock"), bases)["parent"] == os.path.realpath(str(tree / "Music"))
    assert folderpick.browse(str(tree / "Music"), bases)["parent"] == ""                    # cannot climb out of the base
    with pytest.raises(PermissionError):
        folderpick.browse(str(tree / "Docs"), bases)
    with pytest.raises(PermissionError):
        folderpick.browse(str(tree / "Music" / ".." / "Docs"), bases)                       # ../ is resolved before the check


def test_picker_start_list_has_a_starting_point():
    assert folderpick.browse("")["dirs"] and folderpick.browse("")["parent"] is None


def test_who_may_use_the_picker():
    assert folderpick.allowed("127.0.0.1", ()) and folderpick.allowed("::1", ())
    assert not folderpick.allowed("192.168.1.5", ()) and not folderpick.allowed("172.17.0.1", ())
    assert folderpick.allowed("172.17.0.1", ("/library",))                                    # with bases anyone who reached the page


# ---- the tab itself -------------------------------------------------------------------------------------------------------------
def local_admin(tmp_path, **server):
    path = make_config(tmp_path)
    if server:
        extra = "".join('%s = %s\n' % (k, v) for k, v in server.items())
        path.write_text(read_config_text(str(path)).replace("admin_port = 8101", "admin_port = 8101\n" + extra), encoding="utf-8", newline="")
    app = create_app(load_config(str(path)), config_path=str(path), reload_interval=0.0)
    return TestClient(create_admin_app(app), client=("127.0.0.1", 50000), follow_redirects=False), app, path


def test_tab_lists_the_libraries_and_has_the_picker_for_a_local_browser(tmp_path):
    client, _, _ = local_admin(tmp_path)
    html = client.get("/libraries", headers=BOSS).text
    assert "Libraries" in html and "books" in html and "comics" in html and "lib-browse" in html and "admin-libraries.js" in html
    assert 'href=\'/libraries\'' in html


def test_adding_a_library_is_live_at_once_and_removing_works(tmp_path):
    client, app, path = local_admin(tmp_path)
    music = tmp_path / "music"
    music.mkdir()
    r = client.post("/libraries/add", content="name=music&path=%s" % str(music).replace("\\", "/"), headers={**BOSS, **FORM})
    assert r.status_code == 303 and r.headers["location"] == "/libraries?ok=libadded"
    assert "music" in load_config(str(path)).roots
    assert "music" in app.state.library.cfg.roots                                       # in use without a restart
    assert "Library added" in client.get("/libraries?ok=libadded", headers=BOSS).text
    from pluteus.config import User
    assert app.state.library.has_root(User("x", "h", ("*",)), "music")                  # a user with every folder can open it now
    assert not app.state.library.has_root(User("y", "h", ("books",)), "music")          # one limited to other libraries cannot
    r = client.post("/libraries/remove", content="name=music", headers={**BOSS, **FORM})
    assert r.status_code == 303 and "music" not in app.state.library.cfg.roots and "music" not in load_config(str(path)).roots


def test_refused_additions_are_shown_and_change_nothing(tmp_path):
    client, app, path = local_admin(tmp_path)
    before = read_config_text(str(path))
    for body, message in (("name=bad name&path=%s" % str(tmp_path), "letters, digits"), ("name=ghost&path=%s/missing" % str(tmp_path).replace("\\", "/"), "not a folder"),
                          ("name=books&path=%s" % str(tmp_path).replace("\\", "/"), "already a library")):
        r = client.post("/libraries/add", content=body, headers={**BOSS, **FORM})
        assert r.status_code == 400 and message in r.text and "role='alert'" in r.text
    assert read_config_text(str(path)) == before
    r = client.post("/libraries/remove", content="name=books", headers={**BOSS, **FORM})
    assert r.status_code == 303
    r = client.post("/libraries/remove", content="name=comics", headers={**BOSS, **FORM})
    assert r.status_code == 400 and "last library" in r.text                               # never zero libraries


def test_the_picker_endpoint_is_for_local_browsers_or_confined_to_the_bases(tmp_path, tree):
    client, _, _ = local_admin(tmp_path)
    got = client.get("/libraries/browse", params={"path": str(tree)}, headers=BOSS).json()
    assert [d["name"] for d in got["dirs"]] == ["Docs", "Music"]
    assert client.get("/libraries/browse", params={"path": str(tree / "none")}, headers=BOSS).status_code == 404
    remote = TestClient(client.app, client=("192.168.1.9", 50000), follow_redirects=False)
    r = remote.get("/libraries/browse", params={"path": str(tree)}, headers=BOSS)
    assert r.status_code == 403 and "this computer" in r.json()["error"]                     # no picker for a remote admin ...
    assert "lib-browse" not in remote.get("/libraries", headers=BOSS).text and "library_bases" in remote.get("/libraries", headers=BOSS).text
    bases = str(tree / "Music").replace("\\", "/")
    confined, _, _ = local_admin(tmp_path / "second", library_bases='["%s"]' % bases) if (tmp_path / "second").mkdir() is None else (None, None, None)
    remote2 = TestClient(confined.app, client=("192.168.1.9", 50000), follow_redirects=False)
    assert remote2.get("/libraries/browse", params={"path": str(tree / "Music")}, headers=BOSS).status_code == 200      # ... unless confined
    assert remote2.get("/libraries/browse", params={"path": str(tree / "Docs")}, headers=BOSS).status_code == 403


def test_a_hand_edit_of_the_file_is_picked_up_by_the_running_server(tmp_path):
    client, app, path = local_admin(tmp_path)
    folder = tmp_path / "later"
    folder.mkdir()
    text = read_config_text(str(path)) + '\n[[roots]]\nname = "later"\npath = "%s"\n' % str(folder).replace("\\", "/")
    path.write_text(text, encoding="utf-8", newline="")
    os.utime(str(path), (os.path.getmtime(str(path)) + 5,) * 2)
    main = TestClient(app, follow_redirects=False)
    main.get("/healthz")
    main.get("/", headers={**BOSS, "accept": "text/html"})                                     # a request triggers the reload check
    assert "later" in app.state.library.cfg.roots

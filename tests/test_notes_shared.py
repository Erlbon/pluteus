"""Shared notes: written into the library itself, only on a root set to notes = "edit" and only by a user with the notes right."""
import dataclasses
import os
from urllib.parse import urlencode

import pytest
from starlette.testclient import TestClient
from test_login_web import HTML, make
from test_notes_edit import FORM, post, version_of
from test_uploads import ORIGIN

from pluteus import notes
from pluteus.config import parse_config, ConfigError
from pluteus.config import Root

FOLDER = "/note/books/Good%20Author"


def sign_in(app, name):
    me = TestClient(app, follow_redirects=False)
    r = me.post("/login", content="username=%s&password=pw-%s" % (name, name), headers={"content-type": "application/x-www-form-urlencoded", **ORIGIN})
    assert r.status_code == 303
    return me


@pytest.fixture
def shared(cfg, tmp_path):
    books = cfg.roots["books"]
    roots = {**cfg.roots, "books": Root("books", books.path, "edit")}
    users = {**cfg.users, "alice": dataclasses.replace(cfg.users["alice"], notes=True)}
    client, app = make(dataclasses.replace(cfg, roots=roots, users=users), tmp_path, uploads_dir=str(tmp_path / "uploads"))
    return sign_in(app, "alice"), sign_in(app, "bob"), app, tmp_path / "books" / "Good Author"


def test_the_editor_offers_both_kinds_to_a_user_with_the_right(shared):
    me, bob, app, folder = shared
    page = me.get(FOLDER, headers=HTML).text
    assert "<select name='where'>" in page and "value='library'" in page and "value='own'" in page
    plain = bob.get(FOLDER, headers=HTML).text
    assert "<select name='where'>" not in plain and "name='where' value='own'" in plain                        # bob has no right: no choice


def test_a_shared_note_is_written_next_to_the_files_and_seen_by_everybody(shared):
    me, bob, app, folder = shared
    r = post(me, name="author.md", text="# Shared\n\nFor all.\n", where="library", version="", action="save")
    assert r.status_code == 303
    assert (folder / "author.md").read_text(encoding="utf-8") == "# Shared\n\nFor all.\n"
    assert "For all." in bob.get("/browse/books/Good%20Author", headers=HTML).text
    editor = me.get(FOLDER + "?name=author.md&where=library", headers=HTML).text
    assert "For all." in editor and version_of(editor, "library") != ""
    stale = version_of(editor, "library")
    (folder / "author.md").write_text("changed outside, longer\n", encoding="utf-8")
    r = me.post(FOLDER, content=urlencode({"name": "author.md", "text": "x", "where": "library", "v_library": stale, "v_own": "", "action": "save"}), headers=FORM)
    assert r.status_code == 409 and (folder / "author.md").read_text(encoding="utf-8") == "changed outside, longer\n"


def test_an_existing_note_in_another_letter_case_is_the_one_written(shared):
    me, bob, app, folder = shared
    (folder / "Author.MD").write_text("old\n", encoding="utf-8")
    editor = me.get(FOLDER + "?name=author.md&where=library", headers=HTML).text
    r = me.post(FOLDER, content=urlencode({"name": "author.md", "text": "new\n", "where": "library", "v_library": version_of(editor, "library"),
                                           "v_own": "", "action": "save"}), headers=FORM)
    assert r.status_code == 303
    names = [n for n in os.listdir(folder) if n.lower().startswith("author")]
    assert names == ["Author.MD"] and (folder / "Author.MD").read_text(encoding="utf-8") == "new\n"


def test_without_the_right_or_on_a_root_that_does_not_allow_it_nothing_is_written(shared, tmp_path):
    me, bob, app, folder = shared
    r = post(bob, name="author.md", text="x", where="library", version="", action="save")
    assert r.status_code == 403 and not (folder / "author.md").exists()
    r = post(me, "/note/comics/Plain", name="about.md", text="x", where="library", version="", action="save")
    assert r.status_code == 403 and not (tmp_path / "comics" / "Plain" / "about.md").exists()          # the comics root has no notes = "edit"


def test_only_note_names_and_real_folders_inside_the_library(shared, tmp_path):
    me, bob, app, folder = shared
    for bad in ("evil.exe", "../x.md", "notes.md", ".hidden.md"):
        assert post(me, name=bad, text="x", where="library", version="", action="save").status_code == 400, bad
    assert me.get("/note/books/Nope?where=library", headers=HTML).status_code == 404
    assert me.get("/note/books/..%2F..", headers=HTML).status_code in (400, 404)
    assert not list(tmp_path.rglob("evil.exe"))


def test_a_shared_note_can_be_removed_and_a_symlink_is_never_followed(shared, tmp_path):
    me, bob, app, folder = shared
    post(me, name="about.md", text="x\n", where="library", version="", action="save")
    editor = me.get(FOLDER + "?name=about.md&where=library", headers=HTML).text
    r = me.post(FOLDER, content=urlencode({"name": "about.md", "text": "", "where": "library", "v_library": version_of(editor, "library"), "v_own": "",
                                           "action": "remove"}), headers=FORM)
    assert r.status_code == 303 and not (folder / "about.md").exists()
    outside = tmp_path / "secret.txt"
    try:
        os.symlink(outside, folder / "about.md")
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("symbolic links are not available here")
    assert post(me, name="about.md", text="overwrite", where="library", version="", action="save").status_code == 400
    assert outside.read_text(encoding="utf-8") == "outside the library"


def test_the_config_and_the_save_function_agree(cfg, tmp_path):
    data = {"roots": [{"name": "a", "path": str(tmp_path), "notes": "edit"}, {"name": "b", "path": str(tmp_path)}],
            "users": [{"name": "u", "password_hash": cfg.users["alice"].password_hash, "roots": ["*"], "notes": True}]}
    parsed = parse_config(data)
    assert parsed.roots["a"].notes == "edit" and parsed.roots["b"].notes == "" and parsed.users["u"].notes is True
    assert notes.may_share(parsed, parsed.users["u"], "a") and not notes.may_share(parsed, parsed.users["u"], "b")
    with pytest.raises(ConfigError):
        parse_config({**data, "roots": [{"name": "a", "path": str(tmp_path), "notes": "yes"}]})
    assert not notes.may_share(parsed, dataclasses.replace(parsed.users["u"], read_only=True), "a")        # an app key never writes

"""Writing notes from the browser: the overlay in the user's own upload folder, in-place edits there, and every refusal."""
import re
from urllib.parse import urlencode

from starlette.testclient import TestClient
from test_uploads import HTML, ORIGIN, up  # noqa: F401

from notehelp import with_note

from pluteus import notes

FORM = {"content-type": "application/x-www-form-urlencoded", **ORIGIN}
FOLDER = "/note/books/Good%20Author"


def post(me, url=FOLDER, **fields):
    if "version" in fields:                                   # the page's hidden fields: v_own (the user's own file), v_library (the shared one)
        fields["v_" + fields.pop("where_of", "own")] = fields.pop("version")
    return me.post(url, content=urlencode(fields), headers=FORM)


def version_of(html, which="own"):
    return re.search(r"name='v_%s' value='([^']*)'" % which, html).group(1)


def test_a_note_added_to_a_library_folder_is_kept_in_the_users_upload_folder(up):
    me, app, folder = up
    page = with_note(me, "/browse/books/Good%20Author", HTML)
    assert "Add a note" in page and "pl-note-top" not in page
    editor = me.get(FOLDER, headers=HTML)
    assert editor.status_code == 200 and "<select name='name'>" in editor.text
    r = post(me, name="author.md", text="# Good Author\n\nWrote **sagas**.\n", version="", action="save")
    assert r.status_code == 303 and "ok=note" in r.headers["location"]
    saved = folder / "alice" / "Notes" / "books" / "Good Author" / "author.md"
    assert saved.read_text(encoding="utf-8") == "# Good Author\n\nWrote **sagas**.\n"
    page = with_note(me, "/browse/books/Good%20Author", HTML)
    assert "<strong>sagas</strong>" in page and "Edit note" in page


def test_an_existing_library_note_is_the_start_and_the_users_own_wins(up, tmp_path):
    me, app, folder = up
    (tmp_path / "books" / "Good Author" / "author.md").write_text("From the library\n", encoding="utf-8")
    assert "From the library" in with_note(me, "/browse/books/Good%20Author", HTML)
    editor = me.get(FOLDER + "?name=author.md", headers=HTML).text
    assert "From the library" in editor and "starts from the note in the library" in editor
    post(me, name="author.md", text="My own words\n", version="", action="save")
    html = with_note(me, "/browse/books/Good%20Author", HTML)
    assert "My own words" in html and "From the library" not in html
    assert (tmp_path / "books" / "Good Author" / "author.md").read_text(encoding="utf-8") == "From the library\n"      # the library is untouched


def test_a_save_after_a_change_on_disk_is_refused_and_keeps_the_text(up):
    me, app, folder = up
    post(me, name="about.md", text="one\n", version="", action="save")
    opened = me.get(FOLDER + "?name=about.md", headers=HTML).text
    stale = version_of(opened)
    path = folder / "alice" / "Notes" / "books" / "Good Author" / "about.md"
    path.write_text("changed elsewhere, longer\n", encoding="utf-8")
    r = post(me, name="about.md", text="my edit\n", version=stale, action="save")
    assert r.status_code == 409 and "changed on disk" in r.text and "my edit" in r.text
    assert path.read_text(encoding="utf-8") == "changed elsewhere, longer\n"
    fresh = version_of(me.get(FOLDER + "?name=about.md", headers=HTML).text)
    assert post(me, name="about.md", text="my edit\n", version=fresh, action="save").status_code == 303
    # a note that appeared while the page was open (a new note saved with no version) is not overwritten either
    assert post(me, name="about.md", text="x", version="", action="save").status_code == 409


def test_preview_and_remove(up):
    me, app, folder = up
    r = post(me, name="artist.md", text="# Hi\n\n<script>alert(1)</script>\n", version="", action="preview")
    assert r.status_code == 200 and "<h1>Hi</h1>" in r.text and "<script>alert(1)</script>" not in r.text
    assert not (folder / "alice" / "Notes").exists()                                   # a preview writes nothing
    post(me, name="artist.md", text="keep\n", version="", action="save")
    version = version_of(me.get(FOLDER + "?name=artist.md", headers=HTML).text)
    r = post(me, name="artist.md", text="", version=version, action="remove")
    assert r.status_code == 303 and "ok=noteremoved" in r.headers["location"]
    assert not (folder / "alice" / "Notes" / "books" / "Good Author" / "artist.md").exists()


def test_only_note_names_text_and_sizes_are_accepted(up):
    me, app, folder = up
    for bad in ("evil.exe", "../x.md", "sub/artist.md", ".hidden.md", "notes.md", ""):
        assert post(me, name=bad, text="x", version="", action="save").status_code == 400, bad
    assert post(me, name="author.md", text="x" * 70_000, version="", action="save").status_code == 400
    assert post(me, name="author.md", text="a\x00b", version="", action="save").status_code == 400
    assert me.get("/note/books/No%20Such%20Folder", headers=HTML).status_code == 404
    assert me.get("/note/books/..%2F..%2Fx", headers=HTML).status_code in (400, 404)
    assert me.get(FOLDER + "?name=evil.exe", headers=HTML).status_code == 404
    assert not any((folder / "alice").rglob("*.exe"))


def test_a_folder_the_user_may_not_see_cannot_get_a_note(up):
    me, app, folder = up
    for who in ("carol",):                                                              # carol sees only the comics library
        other = TestClient(app, follow_redirects=False)
        other.post("/login", content="username=%s&password=pw-%s" % (who, who), headers=FORM)
        assert other.get(FOLDER, headers=HTML).status_code in (403, 404)
        assert post(other, name="about.md", text="x", version="", action="save").status_code in (403, 404)


def test_notes_follow_the_same_checks_as_every_other_write(up):
    me, app, folder = up
    r = me.post(FOLDER, content=urlencode({"name": "about.md", "text": "x", "version": "", "action": "save"}),
                headers={"content-type": "application/x-www-form-urlencoded", "origin": "http://evil.example"})
    assert r.status_code == 403
    anon = TestClient(app, follow_redirects=False)
    assert anon.get(FOLDER).status_code in (401, 303)


def test_a_note_in_the_upload_folder_is_edited_in_place_and_the_viewer_links_to_the_editor(up):
    me, app, folder = up
    mine = folder / "alice"
    mine.mkdir(parents=True, exist_ok=True)
    (mine / "diary.md").write_text("# Diary\n\nfirst\n", encoding="utf-8")
    page = me.get("/view/uploads/diary.md", headers=HTML).text
    assert "id='edit'" in page and "/note/uploads?name=diary.md" in page
    editor = me.get("/note/uploads?name=diary.md", headers=HTML).text
    assert "first" in editor
    r = post(me, "/note/uploads", name="diary.md", text="# Diary\n\nsecond\n", version=version_of(editor), action="save")
    assert r.status_code == 303 and (mine / "diary.md").read_text(encoding="utf-8") == "# Diary\n\nsecond\n"
    assert post(me, "/note/uploads", name="a.exe", text="x", version="", action="save").status_code == 400


def test_the_viewer_of_a_library_note_offers_the_editor_only_for_note_names(up, tmp_path):
    me, app, folder = up
    (tmp_path / "books" / "Good Author" / "author.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "books" / "Good Author" / "other.md").write_text("y\n", encoding="utf-8")
    assert "id='edit'" in me.get("/view/books/Good%20Author/author.md", headers=HTML).text
    assert "id='edit'" not in me.get("/view/books/Good%20Author/other.md", headers=HTML).text


def test_the_space_limit_applies(up):
    me, app, folder = up
    lib = app.state.library
    user = app.state.cfg.users["alice"]
    notes.save(lib, 1, user, "books", ("Good Author",), "about.md", "x" * 60_000, "")
    (folder / "alice" / "big.bin").write_bytes(b"\0" * (1024 * 1024))
    try:
        notes.save(lib, 1, user, "books", ("Good Author",), "artist.md", "y" * 1000, "")
    except notes.NoteError as e:
        assert e.code == "quota"
    else:
        raise AssertionError("the space limit was not applied")


STAMPED = ("---\ntitle: Depeche Mode\nstatus: draft\ngenerator: mp3redactor 2026-10-17#01\nnote-format: 2\nbody-sha: 0123456789abcdef\n---\n"
           "# Depeche Mode\n\nFrom Basildon.\n")


def test_editing_a_stamped_note_keeps_every_front_matter_key_and_hides_them_from_the_page(up):
    """The Redactor apps stamp their notes with generator / note-format / body-sha. The editor shows the whole file, so a save keeps those keys as
    they are (the stamp then no longer matches the changed text, which is how the apps see that a person edited it); the viewer shows only the body."""
    me, app, folder = up
    mine = folder / "alice"
    mine.mkdir(parents=True, exist_ok=True)
    (mine / "artist.md").write_text(STAMPED, encoding="utf-8")
    shown = me.get("/view/uploads/artist.md", headers=HTML).text
    assert "From Basildon" in shown and "body-sha" not in shown and "note-format" not in shown and "generator" not in shown
    editor = me.get("/note/uploads?name=artist.md", headers=HTML).text
    assert "body-sha: 0123456789abcdef" in editor and "note-format: 2" in editor and "generator: mp3redactor 2026-10-17#01" in editor
    text = STAMPED.replace("From Basildon.", "From Basildon, Essex.")
    r = post(me, "/note/uploads", name="artist.md", text=text, version=version_of(editor), action="save")
    saved = (mine / "artist.md").read_text(encoding="utf-8")
    assert r.status_code == 303 and saved == text
    assert all(line in saved for line in ("generator: mp3redactor 2026-10-17#01", "note-format: 2", "body-sha: 0123456789abcdef", "status: draft"))

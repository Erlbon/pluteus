"""Code review of 2026-11-12, the web side (cards 39, 45 and the static halves of 37, 41-44): what can be checked without a browser.
The behaviour itself is checked in a real browser in test_review2_web_browser.py and test_review2_web_epub_last_page_browser.py."""
import os
import re

from test_login_web import HTML
from test_notes_shared import shared  # noqa: F401  (fixture)
from test_uploads import up  # noqa: F401  (fixture)

from pluteus import notes, web
from pluteus.urls import Urls

STATIC = os.path.join(os.path.dirname(__file__), "..", "pluteus", "static")
FOLDER = "/note/books/Good%20Author"


def script(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


# ---- card 39: every data-confirm really asks ---------------------------------------------------------------------------------------
def test_the_confirm_question_lives_in_the_script_every_page_loads(up):
    me, app, folder = up
    scheme = script("scheme.js")
    assert "data-confirm" in scheme and "window.confirm" in scheme and "submitter" in scheme
    assert "data-confirm" not in script("uploads.js").replace("(The question before a delete, data-confirm,", "")   # one handler: no double question
    for url in ("/settings", FOLDER, "/browse/books/Good%20Author", "/"):                # pages of every kind carry scheme.js
        page = me.get(url, headers=HTML)
        assert page.status_code == 200 and "/static/scheme.js'></script>" in page.text, url


def test_the_remove_question_says_what_is_removed(up):
    me, app, folder = up
    lib, user = app.state.library, app.state.cfg.users["alice"]
    notes.save(lib, 0, user, "books", ("Good Author",), "about.md", "mine\n", "")
    own = me.get(FOLDER + "?name=about.md", headers=HTML).text
    assert "data-confirm='Remove your note? The library is not touched.'" in own            # true here: the overlay copy is the user's own


def test_the_remove_question_for_a_library_note_does_not_say_the_library_is_untouched(shared):
    me, bob, app, folder = shared
    (folder / "author.md").write_text("For all.\n", encoding="utf-8")
    page = me.get(FOLDER + "?name=author.md&where=library", headers=HTML).text
    asked = re.search(r"value='remove' formnovalidate data-confirm='([^']*)'", page).group(1)
    assert "not touched" not in asked and "everybody" in asked


def test_the_remove_question_by_kind_of_note(tmp_path):
    ctx = web.Ctx(urls=Urls(""), user="alice")
    common = dict(name="about.md", names=notes.NOTE_NAMES, text="x", versions={"own": "1-1", "library": "1-1"}, exists=True, from_library=False)

    def asked(**kw):
        return re.search(r"data-confirm='([^']*)'", web.note_editor_page(ctx, "books", ("A",), **common, **kw)).group(1)
    assert asked(in_place=False, where_kind="library", choices=(("library", "x"),)).startswith("Remove this note from the library?")
    both = asked(in_place=False, where_kind="own", choices=(("own", "x"), ("library", "y")))
    assert "not touched" not in both and "everybody" in both                                # the choice on the page decides which note goes
    assert asked(in_place=True, where_kind="own", choices=(("own", "x"),)) == "Remove this note from your upload folder?"


# ---- card 45: a note above the editor's size limit ------------------------------------------------------------------------------------
def test_too_large_tells_an_oversized_note_from_no_note(up):
    me, app, folder = up
    lib, user = app.state.library, app.state.cfg.users["alice"]
    args = (lib, user, "books", ("Good Author",), "about.md")
    assert notes.too_large(*args) is False                                                  # no note at all
    notes.save(lib, 0, user, "books", ("Good Author",), "about.md", "small\n", "")
    assert notes.too_large(*args) is False
    path = folder / "alice" / "Notes" / "books" / "Good Author" / "about.md"
    path.write_bytes(b"x" * (notes.MAX_NOTE_BYTES + 1))                                      # put there by hand or by a sync tool
    assert notes.read(*args) is None                                                        # the old answer: the same as "no note"
    assert notes.too_large(*args) is True
    path.write_bytes(b"x" * notes.MAX_NOTE_BYTES)                                            # exactly the limit still opens
    assert notes.too_large(*args) is False and notes.read(*args) is not None
    assert notes.too_large(lib, user, "books", ("Good Author",), "../x.md") is False        # a name that is no note
    assert notes.too_large(lib, user, "books", ("No Such Folder",), "about.md") is False


def test_too_large_for_the_librarys_shared_note(shared):
    me, bob, app, folder = shared
    lib, user = app.state.library, app.state.cfg.users["alice"]
    args = (lib, user, "books", ("Good Author",), "author.md", "library")
    assert notes.too_large(*args) is False
    (folder / "Author.md").write_bytes(b"y" * (notes.MAX_NOTE_BYTES + 100))                   # another letter case: still the note
    assert notes.too_large(*args) is True
    assert notes.too_large(lib, user, "books", ("Good Author",), "author.md", "own") is False   # the user's own copy does not exist


def test_the_editor_page_says_too_large_instead_of_opening_empty():
    ctx = web.Ctx(urls=Urls(""), user="alice")
    page = web.note_editor_page(ctx, "books", ("Good Author",), name="about.md", names=notes.NOTE_NAMES, text="", versions={"own": "", "library": ""},
                                in_place=False, exists=True, from_library=False, back_url="/browse/books/Good%20Author", too_large=True)
    assert "too large to edit here" in page and "64 KB" in page
    assert "<textarea" not in page and "<form class='pl-card'" not in page                  # nothing to type into, nothing to save or remove
    assert "href='/browse/books/Good%20Author'" in page


# ---- the vendored epub.js patch (card 37) is written down ---------------------------------------------------------------------------------
def test_the_epub_patch_is_in_the_file_and_in_the_readme():
    epub, readme = script(os.path.join("vendor", "epub.min.js")), script(os.path.join("vendor", "README.md"))
    after = ("this.container.scrollLeft+this.container.offsetWidth+this.layout.delta<=this.container.scrollWidth+this.layout.delta/2?",
             "this.container.scrollLeft>this.layout.delta/2?this.scrollBy(-this.layout.delta,0,!0)",
             "this.container.scrollLeft+-1*this.layout.delta>-1*this.container.scrollWidth+this.layout.delta/2?")
    for expression in after:
        assert epub.count(expression) == 1 and expression in readme
    assert "this.layout.delta<=this.container.scrollWidth?" not in epub                      # the published test is gone
    assert "no longer matches" in readme and "0.3.93" in readme


def test_the_page_arithmetic_with_fractional_offsets():
    """The numbers of the fault, without a browser: a 411-pixel page at pixel ratio 2.625, seven pages. The offset is kept on whole
    device pixels, so it reads back a fraction above the page boundary."""
    ratio, page, pages = 2.625, 411, 7
    width = page * pages

    def read_back(offset):
        return round(offset * ratio) / ratio
    left, shown_old, shown_new = 0.0, 1, 1
    while left + page + page <= width:                                                       # the published test
        left = read_back(left + page)
        shown_old += 1
    left = 0.0
    while left + page + page <= width + page / 2:                                            # with half a page of slack
        left = read_back(left + page)
        shown_new += 1
    assert shown_old == pages - 1 and shown_new == pages                                     # the last page was never shown; now it is
    assert left + page + page > width + page / 2                                             # and on the last page the test still says "no more"


# ---- small static checks for what the browser tests cover in full -------------------------------------------------------------------------
def test_reader_keys_leave_alt_ctrl_and_meta_to_the_browser():
    for name in ("reader-comic.js", "viewer-pdf.mjs", "viewer-image.js", "reader-epub.js", "viewer-media.js"):
        assert "if (e.altKey || e.ctrlKey || e.metaKey) return;" in script(name), name


def test_the_four_readers_share_one_swipe():
    assert "window.plSwipe = function" in script("tapzones.js") and "visualViewport" in script("tapzones.js")
    for name in ("reader-comic.js", "viewer-pdf.mjs", "viewer-image.js", "reader-epub.js"):
        text = script(name)
        assert "window.plSwipe(" in text and "changedTouches" not in text, name            # no copy of the swipe arithmetic left

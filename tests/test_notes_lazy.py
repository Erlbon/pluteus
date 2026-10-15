"""The note loads after the folder list (never in front of it), releases in a discography are marked have or missing from the folders, and no page waits on a crawl."""
import re

from conftest import basic
from notehelp import with_note
from test_subsonic import lib, make  # noqa: F401

from pluteus import markdown

AUTH = basic("alice", "pw-alice")
HTML = {"Accept": "text/html"}
DISCOGRAPHY = """# Depeche Mode

An English electronic band.

### Studio albums (all)

- Speak & Spell (1981)
- A Broken Frame (1982)
- Violator (1990)
- Playing the Angel (2005)

### Members

- Dave Gahan
- Violator
"""


def count_calls(client, names):
    lib = client.app.state.library
    counts = {n: 0 for n in names}
    for name in names:
        original = getattr(lib, name)

        def wrapper(*a, _o=original, _n=name, **k):
            counts[_n] += 1
            return _o(*a, **k)
        setattr(lib, name, wrapper)
    return counts


def test_the_folder_page_does_not_read_the_note_and_the_note_comes_after_the_list(lib):
    (lib / "Kaizers" / "artist.md").write_text("# Kaizers\n\nA band.\n", encoding="utf-8")
    c = make(lib)
    counts = count_calls(c, ["file_info"])
    html = c.get("/browse/music/Kaizers", headers={**AUTH, **HTML}).text
    assert counts["file_info"] == 0 and "A band." not in html                                  # the page is built without opening the note
    assert "id='folder-notes' data-src='/api/folder-note/music/Kaizers'" in html and "notes-lazy.js" in html
    assert html.index("<ul class='pl-list") < html.index("id='folder-notes'")                   # an empty place UNDER the list
    api = c.get("/api/folder-note/music/Kaizers", headers=AUTH)
    assert api.status_code == 200 and "pl-note-top" in api.json()["html"] and "A band." in api.json()["html"]
    assert with_note(c, "/browse/music/Kaizers", {**AUTH, **HTML}).count("A band.") == 1


def test_a_folder_without_any_note_has_no_lazy_place_and_the_api_needs_a_login(lib):
    c = make(lib)
    html = c.get("/browse/music/Solo", headers={**AUTH, **HTML}).text
    assert "folder-notes" not in html and "notes-lazy.js" not in html
    anon = make(lib)
    assert anon.get("/api/folder-note/music/Kaizers").status_code in (401, 303)
    assert c.get("/api/folder-note/music/NoSuchFolder", headers=AUTH).status_code in (404, 400)


def test_release_titles_match_folder_names_ignoring_year_ampersand_and_brackets():
    key = markdown.release_key
    assert key("Speak & Spell (1981)") == key("[1981] - Speak And Spell") == key("1981 - Speak and Spell") == "speak and spell"
    assert key("Violator") == key("[1990] - Violator (Remastered)") == "violator"
    assert key("Playing the Angel (2005)") != key("Sounds of the Universe")
    assert key("What's Up?") == key("Whats Up") == key("What’s Up? (1993)") == "whats up"                  # apostrophes do not count


def test_a_discography_shows_which_releases_the_folders_have_and_which_are_missing(lib):
    band = lib / "Depeche Mode"
    for name in ("[1981] - Speak And Spell", "[1982] - A Broken Frame", "[1990] - Violator"):
        (band / name).mkdir(parents=True)
    (band / "artist.md").write_text(DISCOGRAPHY, encoding="utf-8")
    c = make(lib)
    html = c.get("/api/folder-note/music/Depeche%20Mode", headers=AUTH).json()["html"]
    assert "Speak &amp; Spell (1981) <span class='pl-have'" in html and "A Broken Frame (1982) <span class='pl-have'" in html
    assert "Violator (1990) <span class='pl-have'" in html
    assert re.search(r"Playing the Angel \(2005\) <span class='pl-missing'>missing</span>", html)
    assert "Violator</li>" in html and "Dave Gahan</li>" in html                                      # other lists are left alone
    assert html.count("class='pl-have'") == 3 and html.count("class='pl-missing'") == 1
    assert "[1981] - Speak And Spell" in html                                                         # the folder it matched, on hover


def test_a_note_in_a_folder_with_no_album_folders_marks_nothing(lib):
    (lib / "Solo" / "artist.md").write_text(DISCOGRAPHY, encoding="utf-8")
    c = make(lib)
    html = c.get("/api/folder-note/music/Solo", headers=AUTH).json()["html"]
    assert "pl-have" not in html and "pl-missing" not in html


def test_many_unresolved_wiki_links_cost_one_bounded_search_not_one_each(lib):
    (lib / "Kaizers" / "artist.md").write_text("".join("See [[Nothing %d]] and [[Nowhere %d]].\n\n" % (i, i) for i in range(60)), encoding="utf-8")
    c = make(lib)
    counts = count_calls(c, ["list_dir"])
    html = c.get("/api/folder-note/music/Kaizers", headers=AUTH).json()["html"]
    assert "Nothing 59" in html
    assert counts["list_dir"] < 12                                                                    # one index of this folder, below it and above it


def test_the_play_all_queue_lists_each_folder_once(lib):
    (lib / "Kaizers" / "artist.md").write_text("# K\n", encoding="utf-8")
    c = make(lib)
    counts = count_calls(c, ["list_dir"])
    items = c.get("/api/queue/music/Kaizers", headers=AUTH).json()["items"]
    folders = {i["url"].rsplit("/", 1)[0] for i in items}
    assert items and counts["list_dir"] <= len(folders) + 2 + 4                                       # the walk, plus at most the folders above the start

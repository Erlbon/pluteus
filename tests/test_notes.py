"""A folder's own note (artist.md, author.md, about.md at the top; README.md, index.md at the bottom) and the artist biography for Subsonic apps."""
from conftest import basic
from pluteus import subsonic
from test_subsonic import body, call, lib, make  # noqa: F401

AUTH = basic("alice", "pw-alice")
HTML = {"Accept": "text/html"}
KAIZERS = subsonic.make_id("music", ("Kaizers",))
NOTE = "# Kaizers Orchestra\n\nA band from **Stavanger**, [Ompa](Ompa/) and more.\n\n- formed 2000\n- sings in *Norwegian*\n"


def page(c, path="/browse/music/Kaizers", **kw):
    return c.get(path, headers={**AUTH, **HTML}, **kw).text


def test_the_artist_note_shows_at_the_top_and_the_readme_at_the_bottom(lib):
    (lib / "Kaizers" / "artist.md").write_text(NOTE, encoding="utf-8")
    (lib / "Kaizers" / "README.md").write_text("## How this folder is kept\n\nRipped from CD.\n", encoding="utf-8")
    c = make(lib)
    html = page(c)
    top, bottom = html.index("class='pl-note pl-note-top"), html.index("class='pl-note pl-note-bottom")
    assert "<h1>Kaizers Orchestra</h1>" in html[top:bottom] and "<strong>Stavanger</strong>" in html[top:bottom]
    assert "href='/browse/music/Kaizers/Ompa'" in html[top:bottom] or "/browse/music/Kaizers/Ompa" in html[top:bottom]      # a link inside the library works
    assert "How this folder is kept" in html[bottom:] and html.index("<ul class='pl-list") > top


def test_the_first_note_file_of_each_group_wins_and_no_note_shows_no_box(lib):
    c = make(lib)
    assert "pl-note" not in page(c, "/browse/music/Solo")
    (lib / "Solo" / "about.md").write_text("About the single\n", encoding="utf-8")
    (lib / "Solo" / "author.md").write_text("Author wins over about\n", encoding="utf-8")
    (lib / "Solo" / "index.md").write_text("The index\n", encoding="utf-8")
    html = page(c, "/browse/music/Solo")
    assert "Author wins over about" in html and "About the single" not in html and "The index" in html
    for n in range(60):                                                                       # more than one page of entries
        (lib / "Solo" / ("file%02d.txt" % n)).write_text("x", encoding="utf-8")
    assert "Author wins over about" in page(c, "/browse/music/Solo")
    second = page(c, "/browse/music/Solo?page=2")
    assert "Page 2 of 2" in second and "pl-note" not in second.replace("pl-note-", "") .replace(".pl-note", "")   # notes only on the first page


def test_a_note_is_rendered_safely_and_a_bad_or_huge_one_is_skipped(lib):
    c = make(lib)
    (lib / "Solo" / "artist.md").write_text("<script>alert(1)</script>\n\n![x](https://evil.example/p.png)\n\n[bad](javascript:alert(1))\n", encoding="utf-8")
    html = page(c, "/browse/music/Solo")
    assert "<script>alert(1)</script>" not in html and "evil.example" not in html and "javascript:" not in html
    (lib / "Solo" / "artist.md").write_bytes(b"x" * 300_000)                                  # too big: no box, the page still works
    assert "pl-note" not in page(c, "/browse/music/Solo")
    (lib / "Solo" / "artist.md").write_bytes(b"\x00\x01\x02\x03" * 50)                         # binary: skipped
    assert "pl-note" not in page(c, "/browse/music/Solo")


def test_subsonic_gives_the_artist_note_as_the_biography(lib):
    (lib / "Kaizers" / "artist.md").write_text(NOTE, encoding="utf-8")
    c = make(lib)
    text = body(call(c, "getArtistInfo2", id=KAIZERS))["artistInfo2"]["biography"]
    assert text.startswith("Kaizers Orchestra") and "Stavanger" in text and "Ompa and more" in text
    assert "#" not in text and "*" not in text and "](" not in text and "formed 2000" in text
    assert body(call(c, "getArtistInfo", id=KAIZERS))["artistInfo"]["biography"] == text
    assert body(call(c, "getArtistInfo2", id=subsonic.make_id("music", ("Solo",))))["artistInfo2"] == {}        # no note: empty, as before
    assert body(call(c, "getArtistInfo2", id="nonsense"))["error"]["code"] == 70
    (lib / "Kaizers" / "artist.md").write_bytes(b"y" * 70_000)
    assert body(call(c, "getArtistInfo2", id=KAIZERS))["artistInfo2"] == {}


def test_plain_text_for_apps_drops_markdown_marks():
    assert subsonic.plain_text("# Title\n\n> quote with `code` and ~~gone~~\n\n1. one\n2) two\n![pic](a.png) text [link](http://x) end") == \
        "Title\n\nquote with code and gone\n\none\ntwo\ntext link end"
    assert len(subsonic.plain_text("word " * 5000)) <= 4000


# ---- notebook conveniences: front matter, anchors and contents, wiki links ---------------------------------------------------------
def test_front_matter_is_hidden_and_gives_the_page_title(lib):
    (lib / "Kaizers" / "history.md").write_text("---\ntitle: The early years\ntags: [band, rock]\n---\n# Start\n\nText\n", encoding="utf-8")
    c = make(lib)
    html = c.get("/view/music/Kaizers/history.md", headers={**AUTH, **HTML}).text
    assert "<title>The early years" in html and "tags:" not in html and "<h1 id='start'>Start" in html
    (lib / "Kaizers" / "artist.md").write_text("---\ntitle: X\n---\nBand text\n", encoding="utf-8")        # a folder note hides it too
    top = page(c)
    assert "Band text" in top and "title: X" not in top


def test_front_matter_that_is_not_clean_is_left_alone():
    from pluteus import markdown
    assert markdown.front_matter("---\nnot yaml at all\n---\nbody")[0] == {}
    assert markdown.front_matter("---\ntitle: open\nbody without a closing line")[1].startswith("---")
    meta, body = markdown.front_matter("---\ntitle: \"Quoted\"\ntags:\n  - one\n  - two\n---\nbody")
    assert meta == {"title": "Quoted", "tags": ["one", "two"]} and body == "body"


def test_long_documents_get_anchors_and_a_contents_list(lib):
    (lib / "Kaizers" / "long.md").write_text("# A\n\n## B\n\n## B\n\n### C\n\n## D\n", encoding="utf-8")
    (lib / "Kaizers" / "short.md").write_text("# A\n\n## B\n", encoding="utf-8")
    c = make(lib)
    long = c.get("/view/music/Kaizers/long.md", headers={**AUTH, **HTML}).text
    assert "class='pl-toc'" in long and "href='#b-1'" in long and "id='b-1'" in long and "class='pl-anchor'" in long
    assert "pl-toc'" not in c.get("/view/music/Kaizers/short.md", headers={**AUTH, **HTML}).text


def test_wiki_links_find_files_by_name_and_unknown_ones_are_plain_text(lib):
    (lib / "Kaizers" / "notes.md").write_text("See [[Ompa notes]] and [[Top page|the top]] and [[nothing here]] and [[Solo]].\n", encoding="utf-8")
    (lib / "Kaizers" / "Ompa").mkdir(exist_ok=True)
    (lib / "Kaizers" / "Ompa" / "Ompa notes.md").write_text("x", encoding="utf-8")                 # below this folder
    (lib / "Top page.md").write_text("x", encoding="utf-8")                                         # above it
    c = make(lib)
    html = c.get("/view/music/Kaizers/notes.md", headers={**AUTH, **HTML}).text
    assert "href='/view/music/Kaizers/Ompa/Ompa%20notes.md'" in html or "Ompa%20notes.md" in html
    assert ">the top</a>" in html and "Top%20page.md" in html
    assert "nothing here" in html and "href='/view/music/nothing" not in html and ">nothing here</a>" not in html
    assert "/browse/music/Solo" in html                                                             # a folder of that name counts


def test_the_player_queue_links_each_track_to_the_nearest_note(lib):
    (lib / "Kaizers" / "artist.md").write_text("The band\n", encoding="utf-8")
    c = make(lib)
    items = c.get("/api/queue/music/Kaizers", headers=AUTH).json()["items"]
    assert items and all(i["page"] == "/browse/music/Kaizers" and i["linkText"] == "About" for i in items)       # albums below inherit the artist's note
    solo = c.get("/api/queue/music/Solo", headers=AUTH).json()["items"]
    assert solo and "page" not in solo[0]


def test_wiki_links_work_in_a_folders_note_too(lib):
    (lib / "Kaizers" / "artist.md").write_text("See [[history]] and [[nothing]].\n", encoding="utf-8")
    (lib / "Kaizers" / "history.md").write_text("x", encoding="utf-8")
    html = page(make(lib))
    assert "href='/view/music/Kaizers/history.md'" in html and "[[" not in html and ">nothing<" not in html and "nothing" in html

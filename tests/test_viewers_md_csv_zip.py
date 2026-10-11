"""Markdown, CSV/TSV and zip viewers: what they show, and what they refuse."""
import io
import re
import zipfile

import pytest
from conftest import basic
from starlette.testclient import TestClient
from test_login_web import HTML, make

from pluteus import markdown, tabular, ziputil
from pluteus.urls import Urls

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0f01f0005"
                    "000201a5f645400000000049454e44ae426082")


@pytest.fixture
def lib(cfg, tmp_path, library):
    """(signed-in client, the folder where test files go)."""
    client, app = make(cfg, tmp_path)
    me = TestClient(app, follow_redirects=False)
    me.post("/login", content="username=alice&password=pw-alice",
            headers={"content-type": "application/x-www-form-urlencoded", "origin": "http://testserver"})
    return me, library / "comics" / "Plain"


def page(client, root, name, query=""):
    return client.get("/view/comics/Plain/%s%s" % (name.replace(" ", "%20"), query), headers=HTML)


# ---- markdown -------------------------------------------------------------------------------------------------------------
def render(text, folder=("Plain",)):
    return markdown.render(text, Urls(""), "comics", folder)


def test_markdown_basics():
    html = render("# Title\n\nSome **bold** and *italic* and `code`.\n\n- one\n- [x] done\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    assert "<h1>Title</h1>" in html and "<strong>bold</strong>" in html and "<em>italic</em>" in html
    assert "<code>code</code>" in html and "<li" in html and "<table>" in html and "checked" in html


@pytest.mark.parametrize("evil", [
    "<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "<iframe src='http://evil'></iframe>",
    "text <b onclick='x()'>b</b> text", "<style>body{display:none}</style>"])
def test_raw_html_is_escaped_never_run(evil):
    html = render("hello\n\n%s\n\nafter %s inline" % (evil, evil))
    assert "&lt;" in html                                                       # shown as text...
    assert re.search(r"<(script|iframe|style|img|b)\b", html) is None          # ...and no such tag was ever produced
    assert "<b onclick" not in html and "<img src=x" not in html


@pytest.mark.parametrize("url", ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<script>alert(1)</script>",
                                 "vbscript:x", "file:///C:/Windows/win.ini", "//evil.example/x", "/etc/passwd", "../../secret.txt",
                                 "..%2f..%2fsecret.txt", "C:\\Windows\\win.ini"])
def test_unsafe_links_become_plain_text(url):
    html = render("[click me](%s)" % url)
    assert "click me" in html and "href=" not in html


def test_safe_links_and_relative_files():
    html = render("[web](https://example.org/x) [mail](mailto:a@b.c) [other](other.md#part) [up](../Plain/n.txt) [frag](#top)")
    assert "href='https://example.org/x'" in html and "rel='noopener noreferrer'" in html and "target='_blank'" in html
    assert "href='mailto:a@b.c'" in html
    assert "href='/view/comics/Plain/other.md#part'" in html                     # a file next to this one
    assert "href='#top'" in html
    assert "../" not in html                                                      # a path that leaves the folder tree is refused


def test_images_only_from_the_library():
    html = render("![pic](pic.png) ![remote](https://tracker.example/p.gif) ![script](x.js) ![dots](../../../x.png)")
    assert "src='/raw/comics/Plain/pic.png'" in html and "loading='lazy'" in html
    assert "tracker.example" not in html and "x.js" not in html and "../" not in html
    assert html.count("[image:") == 3


def test_deeply_nested_or_huge_input_is_handled():
    with pytest.raises(markdown.TooLarge):
        render("x" * (markdown.MAX_RENDER_BYTES + 1))
    try:
        render(">" * 5000 + " deep")
        render("- a\n" + "".join("  " * i + "- n\n" for i in range(1, 300)))
    except markdown.TooLarge:
        pass                                                                       # refusing is fine, crashing is not


def test_markdown_page_and_source_toggle(lib):
    client, folder = lib
    (folder / "doc.md").write_text("# Hello\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1)) ![i](pic.png)", encoding="utf-8")
    (folder / "pic.png").write_bytes(PNG)
    r = page(client, "comics", "doc.md")
    assert r.status_code == 200 and "class='pl-markdown'" in r.text and "<h1>Hello</h1>" in r.text
    assert "<script>alert" not in r.text and "javascript:" not in r.text
    assert "/raw/comics/Plain/pic.png" in r.text and "id='source'" in r.text
    src = page(client, "comics", "doc.md", "?as=text")
    assert "&lt;script&gt;alert(1)" in src.text and "class='pl-text'" in src.text          # the source, escaped
    assert client.get("/raw/comics/Plain/pic.png").content == PNG                          # the image itself is served inline


def test_a_binary_file_named_md_is_not_rendered(lib):
    client, folder = lib
    (folder / "fake.md").write_bytes(bytes(range(256)) * 20)
    r = page(client, "comics", "fake.md")
    assert r.status_code == 200 and "pl-markdown" not in r.text


# ---- csv / tsv ------------------------------------------------------------------------------------------------------------
def test_comma_semicolon_tab_and_bar_are_detected():
    assert tabular.parse("a,b,c\n1,2,3\n").delimiter == ","
    assert tabular.parse("a;b;c\n1;2;3\n").delimiter == ";"
    assert tabular.parse("a\tb\tc\n1\t2\t3\n", ".tsv").delimiter == "\t"
    assert tabular.parse("a|b|c\n1|2|3\n").delimiter == "|"


def test_quotes_newlines_and_ragged_rows():
    t = tabular.parse('name,note\n"Smith, J.","two\nlines"\nshort\n')
    assert t.rows[1] == ["Smith, J.", "two\nlines"] and t.rows[2] == ["short", ""]
    assert all(len(r) == 2 for r in t.rows)


def test_numeric_columns_are_recognised():
    t = tabular.parse("name,qty,price\nbolt,10,1.5\nnut,2,0,75\nwasher,,3\n".replace("0,75", "0.75"))
    assert t.numeric == [False, True, True]


def test_limits_are_applied_and_reported():
    many = "a,b\n" + "".join("%d,x\n" % i for i in range(tabular.MAX_ROWS + 50))
    t = tabular.parse(many)
    assert t.rows_cut and len(t.rows) == tabular.MAX_ROWS
    wide = ",".join("c%d" % i for i in range(tabular.MAX_COLS + 20)) + "\n" + ",".join("v" * 3 for _ in range(tabular.MAX_COLS + 20))
    assert tabular.parse(wide).cols_cut
    assert tabular.parse("a,b\n" + "z" * (tabular.MAX_CELL + 10) + ",1\n").cells_cut == 1


@pytest.mark.parametrize("text", ["", "\n\n", "a,b\x00c\n"])
def test_nonsense_is_not_a_table(text):
    with pytest.raises(tabular.TableError):
        tabular.parse(text)


def test_csv_page_is_a_safe_sortable_table(lib):
    client, folder = lib
    (folder / "data.csv").write_text('name;qty\n"<script>alert(1)</script>";5\nb;12\n', encoding="utf-8")
    r = page(client, "comics", "data.csv")
    assert r.status_code == 200 and "<table class='pl-table'" in r.text and "viewer-table.js" in r.text
    assert "<script>alert" not in r.text and "&lt;script&gt;alert(1)" in r.text
    assert "data-num='1'" in r.text and "semicolon separated" in r.text and "2 rows" in r.text
    assert client.get("/static/viewer-table.js").status_code == 200
    assert "&lt;script&gt;" in page(client, "comics", "data.csv", "?as=text").text                      # source view still available


def test_empty_or_binary_csv_falls_back_to_text(lib):
    client, folder = lib
    (folder / "empty.csv").write_text("", encoding="utf-8")
    assert page(client, "comics", "empty.csv").status_code == 200
    assert "pl-table'" not in page(client, "comics", "empty.csv").text


# ---- zip ------------------------------------------------------------------------------------------------------------------
def make_zip(path, entries, extra=None):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
        if extra:
            extra(z)


@pytest.fixture
def zipped(lib):
    client, folder = lib
    make_zip(folder / "bundle.zip", {"readme.txt": "hello from the zip", "docs/": "", "docs/pic.png": PNG,
                                     "docs/data.csv": "a,b\n1,2\n", "bin/tool.exe": bytes(range(256)) * 10,
                                     "../../evil.txt": "path trick", "C:/x/y.txt": "windows path",
                                     "ålesund.txt": "unicode name"})
    return client, folder


def test_zip_listing_shows_entries_with_the_right_actions(zipped):
    client, _ = zipped
    r = page(client, "comics", "bundle.zip")
    assert r.status_code == 200 and "class='pl-zip'" in r.text
    for name in ("readme.txt", "docs/pic.png", "bin/tool.exe", "ålesund.txt"):
        assert name in r.text
    assert "&#128193; docs/" in r.text                                              # folder entry
    assert "as=raw" in r.text and "as=text" in r.text and "as=dl" in r.text
    assert "8 entr" in r.text


def entry_index(client, name):
    text = page(client, "comics", "bundle.zip").text
    row = re.search(r"<td class='pl-zip-name'>%s</td>.*?i=(\d+)&amp;as=" % re.escape(name), text)
    assert row, name
    return int(row.group(1))


def test_view_download_and_image_of_entries(zipped):
    client, _ = zipped
    i = entry_index(client, "readme.txt")
    t = client.get("/zip/comics/Plain/bundle.zip?i=%d&as=text" % i, headers=HTML)
    assert t.status_code == 200 and "hello from the zip" in t.text and "class='pl-text'" in t.text
    d = client.get("/zip/comics/Plain/bundle.zip?i=%d&as=dl" % i)
    assert d.status_code == 200 and d.content == b"hello from the zip"
    assert d.headers["content-disposition"].startswith("attachment;") and "readme.txt" in d.headers["content-disposition"]
    assert d.headers["content-type"] == "application/octet-stream" and d.headers["x-content-type-options"] == "nosniff"
    img = client.get("/zip/comics/Plain/bundle.zip?i=%d&as=raw" % entry_index(client, "docs/pic.png"))
    assert img.status_code == 200 and img.content == PNG and img.headers["content-type"] == "image/png"


def test_entries_are_picked_by_number_and_names_never_become_paths(zipped, tmp_path):
    client, _ = zipped
    for name in ("../../evil.txt", "C:/x/y.txt"):
        d = client.get("/zip/comics/Plain/bundle.zip?i=%d&as=dl" % entry_index(client, name))
        assert d.status_code == 200 and d.content in (b"path trick", b"windows path")
        disposition = d.headers["content-disposition"]
        assert ".." not in disposition and "/" not in disposition.split("filename=")[1] and "C:" not in disposition
    assert not (tmp_path / "evil.txt").exists()
    for bad in ("i=999&as=dl", "i=-1&as=dl", "i=abc&as=dl", "as=dl", "i=0&as=weird"):
        assert client.get("/zip/comics/Plain/bundle.zip?" + bad).status_code in (400, 422), bad


def test_only_images_inline_and_only_text_in_the_viewer(zipped):
    client, _ = zipped
    exe = entry_index(client, "bin/tool.exe")
    assert client.get("/zip/comics/Plain/bundle.zip?i=%d&as=raw" % exe).status_code == 404         # not an image
    assert client.get("/zip/comics/Plain/bundle.zip?i=%d&as=text" % exe).status_code == 415       # looks binary
    assert client.get("/zip/comics/Plain/bundle.zip?i=%d&as=dl" % exe).status_code == 200


def test_a_folder_entry_has_nothing_to_open(zipped):
    client, _ = zipped
    names = [e["name"] for e in ziputil.list_entries(str(zipped[1] / "bundle.zip"))[0]]
    folder_index = names.index("docs/")
    assert client.get("/zip/comics/Plain/bundle.zip?i=%d&as=dl" % folder_index).status_code == 422


def test_a_huge_text_entry_shows_only_its_first_part_and_a_bomb_costs_one_limit(lib):
    client, folder = lib
    make_zip(folder / "bomb.zip", {"zeros.txt": b"0" * (6 * 1024 * 1024), "zeros.png": b"0" * (40 * 1024 * 1024)})
    entries, _, _ = ziputil.list_entries(str(folder / "bomb.zip"))
    assert entries[0]["size"] == 6 * 1024 * 1024 and entries[0]["packed"] < 20000
    r = client.get("/zip/comics/Plain/bomb.zip?i=0&as=text", headers=HTML)
    assert r.status_code == 200 and "only the first" in r.text and len(r.text) < 2 * 1024 * 1024 + 20000
    big_image = client.get("/zip/comics/Plain/bomb.zip?i=1&as=raw")
    assert big_image.status_code == 422 and "too large" in big_image.text
    assert client.get("/zip/comics/Plain/bomb.zip?i=0&as=dl").status_code == 200            # a download is streamed, not held


def test_limits_apply_to_the_bytes_really_read(lib):
    client, folder = lib
    make_zip(folder / "big.zip", {"a.txt": b"x" * 100})
    with pytest.raises(ziputil.ZipError, match="too large"):
        ziputil.read_entry(str(folder / "big.zip"), 0, 50)
    assert ziputil.read_entry(str(folder / "big.zip"), 0, 100) == b"x" * 100
    assert b"".join(ziputil.stream_entry(str(folder / "big.zip"), 0)) == b"x" * 100
    with pytest.raises(ziputil.ZipError, match="too large"):
        list(ziputil.stream_entry(str(folder / "big.zip"), 0, limit=10))


def test_encrypted_entries_are_listed_but_never_opened(lib):
    client, folder = lib

    make_zip(folder / "locked.zip", {"secret.txt": "cannot be read"})
    data = bytearray((folder / "locked.zip").read_bytes())          # zipfile clears the flag when writing, so patch the bytes
    data[data.index(b"PK\x03\x04") + 6] |= 1                        # local header: encrypted
    data[data.rindex(b"PK\x01\x02") + 8] |= 1                       # central directory: encrypted
    (folder / "locked.zip").write_bytes(bytes(data))
    assert "password protected" in page(client, "comics", "locked.zip").text
    for mode in ("dl", "text", "raw"):
        assert client.get("/zip/comics/Plain/locked.zip?i=0&as=" + mode).status_code in (404, 422), mode


def test_a_broken_zip_gives_a_readable_page_not_an_error(lib):
    client, folder = lib
    (folder / "broken.zip").write_bytes(b"PK\x03\x04 this is not really a zip" * 10)
    r = page(client, "comics", "broken.zip")
    assert r.status_code == 200 and "cannot be opened" in r.text
    assert client.get("/zip/comics/Plain/broken.zip?i=0&as=dl").status_code == 422


def test_a_huge_listing_is_cut(lib):
    client, folder = lib
    make_zip(folder / "many.zip", {"f%05d.txt" % i: "" for i in range(ziputil.MAX_SHOWN + 25)})
    r = page(client, "comics", "many.zip")
    assert "Only the first %d of %d entries are listed" % (ziputil.MAX_SHOWN, ziputil.MAX_SHOWN + 25) in r.text


def test_zip_routes_obey_the_same_access_rules(cfg, tmp_path, library):
    client, app = make(cfg, tmp_path)
    folder = library / "comics" / "Adult Pub"
    make_zip(folder / "secret.zip", {"a.txt": "x"})
    anon = TestClient(app, follow_redirects=False)
    assert anon.get("/zip/comics/Adult%20Pub/secret.zip?i=0&as=dl").status_code in (303, 401)
    assert client.get("/zip/comics/Adult%20Pub/secret.zip?i=0&as=dl", headers=basic("alice", "pw-alice")).status_code == 404   # no .adult access
    assert client.get("/zip/comics/Adult%20Pub/secret.zip?i=0&as=dl", headers=basic("bob", "pw-bob")).status_code == 200
    assert client.get("/zip/comics/Plain/notes.txt?i=0&as=dl", headers=basic("alice", "pw-alice")).status_code == 404         # not a zip


def test_zip_inside_an_upload_folder_works_too(cfg, tmp_path):
    client, app = make(cfg, tmp_path, uploads_dir=str(tmp_path / "up"))
    me = TestClient(app, follow_redirects=False)
    me.post("/login", content="username=alice&password=pw-alice",
            headers={"content-type": "application/x-www-form-urlencoded", "origin": "http://testserver"})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", "from an upload")
    me.post("/upload/uploads", files=[("files", ("up.zip", buf.getvalue(), "application/zip"))],
            headers={"origin": "http://testserver", "accept": "application/json"})
    assert "a.txt" in me.get("/view/uploads/up.zip", headers=HTML).text
    assert me.get("/zip/uploads/up.zip?i=0&as=text", headers=HTML).text.count("from an upload") == 1

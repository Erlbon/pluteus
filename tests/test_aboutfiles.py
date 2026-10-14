"""The about text other tools write (artist.nfo, album.nfo, metadata.opf) as the fallback note of a folder."""
from conftest import basic
from test_subsonic import body, call, lib, make  # noqa: F401

from pluteus import aboutfiles, subsonic

AUTH = basic("alice", "pw-alice")
HTML = {"Accept": "text/html"}
ARTIST = ("<?xml version='1.0' encoding='utf-8'?><artist><name>Kaizers</name><genre>Rock</genre><genre>Norwegian</genre><formed>2000</formed>"
          "<biography>A band from Stavanger.&lt;br/&gt;Sings in Norwegian &lt;b&gt;only&lt;/b&gt;.</biography></artist>")
OPF = ("<package xmlns='http://www.idpf.org/2007/opf'><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>"
       "<dc:title>T</dc:title><dc:description>&lt;p&gt;A long &lt;i&gt;saga&lt;/i&gt;.&lt;/p&gt;&lt;p&gt;Second part.&lt;/p&gt;</dc:description></metadata></package>")


def test_the_summaries_are_plain_text():
    text = aboutfiles.summary("artist.nfo", ARTIST.encode())
    assert text.startswith("A band from Stavanger.") and "Sings in Norwegian only." in text and "<" not in text
    assert "Genre: Rock, Norwegian" in text and "Formed: 2000" in text
    assert aboutfiles.summary("metadata.opf", OPF.encode()) == "A long saga.\n\nSecond part."
    assert aboutfiles.as_html("a <b>\n\nsecond") == "<p>a &lt;b&gt;</p><p>second</p>"


def test_bad_or_hostile_files_give_nothing():
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><artist><biography>&a;&a;</biography></artist>'
    assert aboutfiles.summary("artist.nfo", bomb) == ""
    assert aboutfiles.summary("artist.nfo", b"not xml at all") == ""
    assert aboutfiles.summary("artist.nfo", b"<artist><name>no biography</name></artist>") == ""
    assert aboutfiles.summary("artist.nfo", b"<artist>" + b"x" * 300_000) == ""
    assert aboutfiles.summary("other.nfo", ARTIST.encode()) == ""


def test_a_folder_without_a_note_shows_its_nfo_and_a_note_of_ours_wins(lib):
    (lib / "Kaizers" / "artist.nfo").write_text(ARTIST, encoding="utf-8")
    c = make(lib)
    html = c.get("/browse/music/Kaizers", headers={**AUTH, **HTML}).text
    assert "A band from Stavanger." in html and "pl-note-top" in html
    (lib / "Kaizers" / "artist.md").write_text("Our own words\n", encoding="utf-8")
    html = c.get("/browse/music/Kaizers", headers={**AUTH, **HTML}).text
    assert "Our own words" in html and "Stavanger" not in html
    (lib / "Solo" / "metadata.opf").write_text(OPF, encoding="utf-8")
    assert "A long" in c.get("/browse/music/Solo", headers={**AUTH, **HTML}).text


def test_subsonic_uses_the_nfo_biography_when_there_is_no_note(lib):
    (lib / "Kaizers" / "artist.nfo").write_text(ARTIST, encoding="utf-8")
    c = make(lib)
    text = body(call(c, "getArtistInfo2", id=subsonic.make_id("music", ("Kaizers",))))["artistInfo2"]["biography"]
    assert text.startswith("A band from Stavanger.") and "<" not in text

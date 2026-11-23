"""Office files in the viewer: Word, OpenDocument, RTF and PowerPoint as a page of words; Excel and OpenDocument workbooks as a
table with a tab per sheet. Unreadable, hostile and oversized files get an info page, never an error."""
import os
import shutil
from html import escape

import pytest
from test_officedoc import make_docx, para
from test_officesheet import make_xlsx
from test_viewers_md_csv_zip import lib, page  # noqa: F401  (lib is a fixture)

from pluteus import app as app_module
from pluteus import kinds, tabular

DATA = os.path.join(os.path.dirname(__file__), "data")


def put(folder, name):
    shutil.copy(os.path.join(DATA, name), folder / name)


@pytest.mark.parametrize("name, kind", [("a.docx", "document"), ("a.DOC", "document"), ("a.odt", "document"), ("a.rtf", "document"),
                                        ("a.pptx", "document"), ("a.xlsx", "sheet"), ("a.xls", "sheet"), ("a.ods", "sheet"),
                                        ("a.epub", "epub"), ("a.csv", "table")])
def test_office_names_have_their_own_kinds(name, kind):
    assert kinds.kind_of(name) == kind


def test_a_word_97_document_is_shown_as_paragraphs_without_a_source_button(lib):
    client, folder = lib
    put(folder, "word97.doc")
    r = page(client, "comics", "word97.doc")
    assert r.status_code == 200
    assert "<p>Vrakkart 1910</p>" in r.text and "Word 97 document" in r.text
    assert "id='source'" not in r.text and "id='download'" in r.text


def test_a_docx_becomes_headings_and_its_html_is_text_never_markup(lib, tmp_path):
    client, folder = lib
    make_docx(folder / "evil.docx", para("Report", "Overskrift1") + para(escape("<script>alert(1)</script> & <b onclick=x>")),
              '<w:style w:type="paragraph" w:styleId="Overskrift1"><w:name w:val="heading 1"/></w:style>')
    r = page(client, "comics", "evil.docx")
    assert r.status_code == 200
    assert "<h1 id='report'>Report " in r.text and "Word document" in r.text
    assert "<script>alert" not in r.text and "<b onclick" not in r.text and "&lt;script&gt;" in r.text


def test_odt_rtf_and_a_presentation_are_pages(lib):
    client, folder = lib
    for name in ("word97.odt", "word97.rtf", "deck.pptx"):
        put(folder, name)
    assert ">Second heading <a class='pl-anchor'" in page(client, "comics", "word97.odt").text
    assert "<strong>bold words</strong>" in page(client, "comics", "word97.rtf").text
    deck = page(client, "comics", "deck.pptx").text
    assert ">Slide 1: Vrakkart 1910 <a class='pl-anchor'" in deck and "PowerPoint presentation" in deck


def test_a_workbook_shows_its_first_visible_sheet_with_a_tab_for_each(lib):
    client, folder = lib
    put(folder, "book.xls")
    r = page(client, "comics", "book.xls")
    assert r.status_code == 200 and "Excel 97 workbook" in r.text
    assert ">Ship</th>" in r.text and "<td>Haabet</td>" in r.text and "#DIV/0!" in r.text
    assert "?sheet=0' aria-current='page'>Vrak</a>" in r.text
    assert "pl-sheet-tab pl-sheet-hidden' href='/view/comics/Plain/book.xls?sheet=1' title='A hidden sheet'>Andre</a>" in r.text
    other = page(client, "comics", "book.xls", "?sheet=1").text
    assert ">second sheet</th>" in other and "?sheet=1' aria-current='page'" in other
    assert page(client, "comics", "book.xls", "?sheet=99").text.count("aria-current") == 1      # out of range: the first sheet
    assert page(client, "comics", "book.xls", "?sheet=-1").status_code == 200


def test_an_ods_and_an_xlsx_with_empty_top_rows_and_an_empty_sheet(lib):
    client, folder = lib
    put(folder, "book.ods")
    assert "OpenDocument spreadsheet" in page(client, "comics", "book.ods").text
    make_xlsx(folder / "gap.xlsx", [("Data", '<row r="3"><c r="A3" t="inlineStr"><is><t>Name</t></is></c></row>'
                                             '<row r="4"><c r="A4" t="inlineStr"><is><t>Haabet</t></is></c></row>'),
                                    ("Empty", "")])
    one = page(client, "comics", "gap.xlsx").text
    assert ">Name</th>" in one and "<td>Haabet</td>" in one                     # empty rows above the header are left out
    assert "This sheet is empty" in page(client, "comics", "gap.xlsx", "?sheet=1").text


def test_a_single_sheet_has_no_tabs(lib):
    client, folder = lib
    make_xlsx(folder / "one.xlsx", [("Only", '<row r="1"><c r="A1"><v>1</v></c></row>')])
    assert "pl-sheet-tabs" not in page(client, "comics", "one.xlsx").text


def test_broken_hostile_and_oversized_files_get_an_info_page(lib, monkeypatch):
    client, folder = lib
    (folder / "broken.docx").write_bytes(b"PK\x03\x04" + b"\x00" * 100)
    (folder / "fake.xlsx").write_bytes(b"just some text")
    make_xlsx(folder / "bomb.xlsx", [("S", "")], extra={"xl/sharedStrings.xml": '<!DOCTYPE x [<!ENTITY a "aaaa">]><sst>&a;</sst>'})
    for name in ("broken.docx", "fake.xlsx", "bomb.xlsx"):
        r = page(client, "comics", name)
        assert r.status_code == 200 and "cannot be shown" in r.text, name
    put(folder, "word97.doc")
    monkeypatch.setattr(app_module, "MAX_OFFICE_BYTES", 1000)
    r = page(client, "comics", "word97.doc")
    assert r.status_code == 200 and "too large to show here" in r.text


def test_a_reader_bug_is_an_info_page_not_a_500(lib, monkeypatch):
    client, folder = lib
    put(folder, "word97.doc")

    def boom(path):
        raise IndexError("a bug")
    monkeypatch.setattr(app_module.officedoc, "read_document", boom)
    r = page(client, "comics", "word97.doc")
    assert r.status_code == 200 and "cannot be shown here" in r.text


def test_from_rows_keeps_the_csv_limits(monkeypatch):
    monkeypatch.setattr(tabular, "MAX_ROWS", 3)
    table = tabular.from_rows([["", ""], ["a", "b"], ["1", "2"], ["3", "4"], ["5", "6"]])
    assert table.rows == [["a", "b"], ["1", "2"], ["3", "4"]] and table.rows_cut and table.numeric == [True, True]
    with pytest.raises(tabular.TableError):
        tabular.from_rows([["", " "]])

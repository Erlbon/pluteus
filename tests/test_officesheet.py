"""The cell values of Excel and OpenDocument workbooks for the spreadsheet viewer (pluteus/officesheet.py). tests/data/book.xls was
saved by Excel with personal information removed; the .xlsx tests build their own small workbooks. Copied from Dossier's tests
with the reader (commit d3f7956), plus the .ods test from its test_readers.py."""
import os
import struct
import zipfile

import pytest

from pluteus import officesheet as osh
from pluteus.officedoc import DocumentError

BOOK = os.path.join(os.path.dirname(__file__), "data", "book.xls")
EXPECTED = [["Ship", "Year", "Lost", "Share", "Sum", "", ""],
            ["Haabet", "1910", "1910-03-14", "25%", "4775", "", ""],
            ["Blåbær Ω", "1911.75", "14:30", "", "#DIV/0!", "", ""],
            ["Haabet og Blåbær Ω", "TRUE", "", "", "", "", ""],
            ["", "", "", "", "", "", ""],
            ["", "", "", "", "", "", "-12345678"]]


def test_an_excel_97_workbook_gives_values_as_a_person_reads_them():
    book = osh.read_workbook(BOOK)
    assert book.kind == "Excel 97 workbook"
    assert [(s.name, s.hidden, s.cut) for s in book.sheets] == [("Vrak", False, False), ("Andre", True, False)]
    assert book.sheets[0].rows == EXPECTED                 # a date, a time, a percent, formula results, an error, TRUE
    assert book.sheets[1].rows == [["second sheet"]]


def test_strings_that_run_over_into_continue_records_switch_width_where_the_record_ends():
    # "abcdef" starts one byte a character, then the next piece starts with a flags byte saying two bytes a character
    first = struct.pack("<HB", 6, 0) + b"abc"
    second = bytes([1]) + "déf".encode("utf-16-le") + struct.pack("<HB", 2, 1) + "ΩΩ".encode("utf-16-le")
    reader = osh._Pieces([first, second])
    assert reader.string() == "abcdéf"
    assert reader.string() == "ΩΩ"
    with pytest.raises(DocumentError):
        reader.string()


def test_dates_numbers_and_formats():
    assert osh.date_text(45607, False) == "2024-11-11"
    assert osh.date_text(45607.5, False) == "2024-11-11 12:00"
    assert osh.date_text(0.25, False) == "06:00"
    assert osh.date_text(60, False) == "1900-02-29"           # Excel's own leap day is kept
    assert osh.date_text(61, False) == "1900-03-01"
    assert osh.date_text(0, True) == "1904-01-01"
    assert osh.number_text(3.0) == "3" and osh.number_text(0.1 + 0.2) == "0.3" and osh.number_text(1e20) == "1e+20"
    assert [osh.format_kind(c) for c in ("General", "0.00", "yyyy-mm-dd", '"Day" 0', "[Red]0.0%", "#,##0 \\m", "h:mm")] == \
        ["", "", "date", "", "percent", "", "date"]
    assert osh._rk((1910 << 2) | 2) == 1910 and osh._rk((191075 << 2) | 3) == 1910.75


S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


HIDDEN = ' state="hidden"'      # outside the f-string: Python 3.10 allows no backslash inside one


def make_xlsx(path, sheets, strings=(), date1904=False, extra=None):
    with zipfile.ZipFile(path, "w") as z:
        entries = "".join(f'<sheet name="{n}" sheetId="{i + 1}" r:id="rId{i + 1}"{HIDDEN if n.startswith("_") else ""}/>'
                          for i, (n, _) in enumerate(sheets))
        z.writestr("xl/workbook.xml", f'<workbook {S} {R}><workbookPr date1904="{int(date1904)}"/><sheets>{entries}</sheets></workbook>')
        rels = "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
                       f'worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(sheets)))
        z.writestr("xl/_rels/workbook.xml.rels",
                   f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
        if "xl/sharedStrings.xml" not in (extra or {}):
            z.writestr("xl/sharedStrings.xml", f'<sst {S}>' + "".join(strings) + "</sst>")
        z.writestr("xl/styles.xml", f'<styleSheet {S}><numFmts><numFmt numFmtId="164" formatCode="dd/mm/yyyy"/></numFmts>'
                                    '<cellXfs><xf numFmtId="0"/><xf numFmtId="164"/><xf numFmtId="10"/><xf numFmtId="14"/></cellXfs></styleSheet>')
        for i, (_, data) in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", f'<worksheet {S}><sheetData>{data}</sheetData></worksheet>')
        for name, text in (extra or {}).items():
            z.writestr(name, text)
    return str(path)


def test_an_xlsx_gives_shared_inline_formula_and_formatted_values_in_their_places(tmp_path):
    data = ('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c></row>'
            '<row r="3"><c r="A3" s="1"><v>45607</v></c><c r="B3" s="2"><v>0.125</v></c><c r="C3" t="b"><v>1</v></c>'
            '<c r="D3" t="e"><v>#N/A</v></c><c r="E3" t="str"><f>A1&amp;"!"</f><v>Ship!</v></c>'
            '<c r="F3" t="inlineStr"><is><t>inline</t></is></c><c r="G3"><v>2.50</v></c></row>'
            '<row><c t="s"><v>9</v></c><c><v>7</v></c></row>')                     # no r: the next row, the next column
    strings = ['<si><t>Ship</t></si>', '<si><r><t>Blå</t></r><r><rPr><b/></rPr><t>bær</t></r><rPh><t>ignored</t></rPh></si>']
    book = osh.read_workbook(make_xlsx(tmp_path / "a.xlsx", [("Vrak", data), ("_hidden", '<row r="1"><c r="B1"><v>1</v></c></row>')],
                                       strings))
    assert book.kind == "Excel workbook"
    vrak, hidden = book.sheets
    assert vrak.rows == [["Ship", "", "Blåbær", "", "", "", ""],
                         ["", "", "", "", "", "", ""],
                         ["2024-11-11", "12.5%", "TRUE", "#N/A", "Ship!", "inline", "2.5"],
                         ["", "7", "", "", "", "", ""]]
    assert hidden.hidden and hidden.rows == [["", "1"]]
    assert osh.read_workbook(make_xlsx(tmp_path / "b.xlsx", [("S", '<row r="1"><c r="A1" s="3"><v>0</v></c></row>')],
                                       date1904=True)).sheets[0].rows == [["1904-01-01"]]


def test_a_huge_sheet_stops_at_the_limits_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(osh, "MAX_ROWS", 3)
    monkeypatch.setattr(osh, "MAX_COLS", 2)
    data = "".join(f'<row r="{r}"><c r="A{r}"><v>{r}</v></c><c r="C{r}"><v>0</v></c></row>' for r in range(1, 10))
    sheet = osh.read_workbook(make_xlsx(tmp_path / "a.xlsx", [("S", data)])).sheets[0]
    assert sheet.cut and sheet.rows == [["1"], ["2"], ["3"]]
    xls = osh.read_workbook(BOOK).sheets[0]
    assert xls.cut and len(xls.rows) == 3 and len(xls.rows[0]) == 2


def test_broken_hostile_and_foreign_files_are_errors_in_words(tmp_path):
    with zipfile.ZipFile(tmp_path / "x.xlsx", "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(DocumentError, match="not an Excel workbook"):
        osh.read_workbook(str(tmp_path / "x.xlsx"))
    bomb = make_xlsx(tmp_path / "bomb.xlsx", [("S", "")],
                     extra={"xl/sharedStrings.xml": '<!DOCTYPE x [<!ENTITY a "aaaa">]><sst>&a;</sst>'})
    with pytest.raises(DocumentError):
        osh.read_workbook(bomb)
    cut = tmp_path / "cut.xls"
    cut.write_bytes(open(BOOK, "rb").read()[:3000])
    with pytest.raises(DocumentError):
        osh.read_workbook(str(cut))
    word = os.path.join(os.path.dirname(__file__), "data", "word97.doc")
    with pytest.raises(DocumentError, match="not an Excel workbook"):
        osh.xls_sheets(word)


def test_an_ods_reads_like_the_excel_workbook_it_was_saved_from():
    book = osh.read_workbook(os.path.join(os.path.dirname(__file__), "data", "book.ods"))
    xls = osh.read_workbook(BOOK)
    assert book.kind == "OpenDocument spreadsheet"
    assert [(s.name, s.hidden) for s in book.sheets] == [("Vrak", False), ("Andre", True)]
    assert [s.rows for s in book.sheets] == [s.rows for s in xls.sheets]      # the time by its cell style, the hidden sheet

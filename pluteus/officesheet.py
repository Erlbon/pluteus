"""The cell values of an Excel or OpenDocument workbook, for the spreadsheet viewer: a quick look without Excel, on any system.

Copied from Dossier (dossier/core/officesheet.py, commit d3f7956, 2026-11-23) with only the workbook formats kept (Dossier also
shows SQLite, torrent and forensic files in the same viewer).

``.xlsx`` (and .xlsm, .xltx, .xltm) is a zip of XML: a list of sheets, a table of shared strings, the number formats, and one XML
file per sheet. ``.xls`` (and .xlt) from Excel 97 and later keeps a ``Workbook`` stream of BIFF8 records in the same Office 97
container as a Word 97 document (``officedoc.Compound``). Either way only values come out, as the text a person would read: numbers,
dates and times by their format, percentages, TRUE/FALSE, error codes, and the last result of a formula (not the formula). Formatting,
charts, pictures and comments are left out. A sheet stops at ``MAX_ROWS`` rows and ``MAX_COLS`` columns, and the whole workbook at
``MAX_CELLS`` cells; a cut sheet says so.

Only the standard library; Qt-free.
"""
from __future__ import annotations

import datetime as dt
import re
import struct
import zipfile
from dataclasses import dataclass, field
from typing import Optional
from xml.etree import ElementTree as ET

from .officedoc import MAX_XML, OLE_MAGIC, PROLOG, ZIP_MAGIC, Compound, DocumentError, _xml, unsafe_xml

XLS_EXTENSIONS = (".xls", ".xlt")
XLSX_EXTENSIONS = (".xlsx", ".xlsm", ".xltx", ".xltm")
ODS_EXTENSIONS = (".ods", ".ots")
MAX_ROWS = 20000
MAX_COLS = 256
MAX_CELLS = 1_000_000


@dataclass
class Sheet:
    name: str
    rows: list = field(default_factory=list)       # list of rows, each a list of strings, all as wide as the widest
    cut: bool = False                              # rows or columns past the limits were left out
    hidden: bool = False


@dataclass
class Workbook:
    kind: str                                      # "Excel workbook" or "Excel 97 workbook"
    sheets: list


def read_workbook(path: str) -> Workbook:
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(OLE_MAGIC):
        return Workbook("Excel 97 workbook", xls_sheets(path))
    if head.startswith(ZIP_MAGIC):
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
        except zipfile.BadZipFile as e:
            raise DocumentError("The workbook is damaged") from e
        if "content.xml" in names and "xl/workbook.xml" not in names:
            return Workbook("OpenDocument spreadsheet", ods_sheets(path))
        return Workbook("Excel workbook", xlsx_sheets(path))
    raise DocumentError("This is not a workbook inside, whatever its name says")


# values as text --------------------------------------------------------------------------------------------------------------------
DATE_FORMAT_IDS = set(range(14, 23)) | set(range(27, 37)) | {45, 46, 47} | set(range(50, 59))
PERCENT_FORMAT_IDS = {9, 10}
_QUOTED = re.compile(r'"[^"]*"|\\.|_.|\*.|\[[^\]]*\]')


def format_kind(code: str) -> str:
    """``date``, ``percent`` or an empty string for an Excel number format code."""
    bare = _QUOTED.sub("", code.split(";")[0]).lower()
    if bare == "general":
        return ""
    if re.search(r"[dmyhs]", bare):
        return "date"
    if "%" in bare:
        return "percent"
    return ""


def number_text(value: float) -> str:
    if value != value or value in (float("inf"), float("-inf")):
        return "#NUM!"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.15g}"


def date_text(serial: float, date1904: bool) -> str:
    """An Excel date serial as ``2026-11-11``, ``2026-11-11 14:30`` or ``14:30:05``. Excel's own 29 February 1900 is kept."""
    if serial < 0 or serial > 2958465:
        return number_text(serial)
    days = int(serial)
    seconds = round((serial - days) * 86400)
    if seconds >= 86400:
        days, seconds = days + 1, seconds - 86400
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    clock = f"{h:02d}:{m:02d}" + (f":{s:02d}" if s else "")
    if days == 0 and not date1904:
        return clock
    if date1904:
        day = (dt.date(1904, 1, 1) + dt.timedelta(days=days)).isoformat()
    elif days == 60:
        day = "1900-02-29"
    else:
        day = (dt.date(1899, 12, 31 if days < 60 else 30) + dt.timedelta(days=days)).isoformat()
    return day if not seconds else f"{day} {clock}"


def value_text(value: float, kind: str, date1904: bool) -> str:
    if kind == "date":
        return date_text(value, date1904)
    if kind == "percent":
        return number_text(round(value * 100, 10)) + "%"
    return number_text(value)


class _Grid:
    """Cells put in by row and column (0-based), handed out as rows of equal width; the limits are kept here."""

    def __init__(self, budget: list) -> None:
        self.cells: dict = {}
        self.cut = False
        self.budget = budget                       # [cells left for the whole workbook], shared by its sheets

    def put(self, row: int, col: int, text: str) -> None:
        if text == "":
            return                                 # an empty cell past the limits cuts nothing (LibreOffice repeats them by the thousand)
        if row >= MAX_ROWS or col >= MAX_COLS or self.budget[0] <= 0:
            self.cut = True
            return
        if (row, col) not in self.cells:
            self.budget[0] -= 1
        self.cells[(row, col)] = text

    def rows(self) -> list:
        if not self.cells:
            return []
        height = max(r for r, _ in self.cells) + 1
        width = max(c for _, c in self.cells) + 1
        out = [[""] * width for _ in range(height)]
        for (r, c), text in self.cells.items():
            out[r][c] = text
        return out


# .xlsx -----------------------------------------------------------------------------------------------------------------------------
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_REF = re.compile(r"([A-Z]+)(\d+)")


def _column(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def _rich_text(el: ET.Element) -> str:
    """The text of a shared or inline string: its ``t``, or the ``t`` of each run, without the phonetic guide."""
    direct = el.find(S + "t")
    if direct is not None:
        return direct.text or ""
    return "".join(t.text or "" for r in el.findall(S + "r") for t in r.findall(S + "t"))


def _target(base: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    parts = base.split("/")[:-1]
    for piece in target.split("/"):
        if piece == "..":
            if parts:
                parts.pop()
        elif piece and piece != ".":
            parts.append(piece)
    return "/".join(parts)


def xlsx_sheets(path: str) -> list:
    try:
        with zipfile.ZipFile(path) as z:
            book = _xml(z, "xl/workbook.xml")
            if book is None:
                raise DocumentError("This zip file is not an Excel workbook")
            rels = _xml(z, "xl/_rels/workbook.xml.rels")
            targets = {}
            if rels is not None:
                for r in rels.iter(PKG_REL + "Relationship"):
                    if r.get("Type", "").endswith("/worksheet"):
                        targets[r.get("Id")] = _target("xl/workbook.xml", r.get("Target", ""))
            pr = book.find(f"{S}workbookPr")
            date1904 = pr is not None and pr.get("date1904", "0").lower() in ("1", "true")
            strings = []
            sst = _xml(z, "xl/sharedStrings.xml")
            if sst is not None:
                strings = [_rich_text(si) for si in sst.iter(S + "si")]
            kinds = _xlsx_style_kinds(_xml(z, "xl/styles.xml"))
            budget = [MAX_CELLS]
            sheets = []
            for sh in book.iter(S + "sheet"):
                target = targets.get(sh.get(REL + "id", ""))
                if not target:
                    continue                                  # a chart sheet or a dialog sheet: no cells
                sheet = Sheet(sh.get("name", "?"), hidden=sh.get("state", "visible") != "visible")
                grid = _Grid(budget)
                _xlsx_cells(z, target, strings, kinds, date1904, grid)
                sheet.rows, sheet.cut = grid.rows(), grid.cut
                sheets.append(sheet)
            return sheets
    except zipfile.BadZipFile as e:
        raise DocumentError("The workbook is damaged") from e


def _xlsx_style_kinds(styles: Optional[ET.Element]) -> list:
    """For each cell style index: ``date``, ``percent`` or an empty string."""
    if styles is None:
        return []
    custom = {}
    fmts = styles.find(S + "numFmts")
    if fmts is not None:
        for f in fmts.findall(S + "numFmt"):
            if f.get("numFmtId", "").isdigit():
                custom[int(f.get("numFmtId"))] = f.get("formatCode", "")
    kinds = []
    xfs = styles.find(S + "cellXfs")
    for xf in (xfs.findall(S + "xf") if xfs is not None else []):
        fid = int(xf.get("numFmtId", "0")) if xf.get("numFmtId", "0").isdigit() else 0
        if fid in custom:
            kinds.append(format_kind(custom[fid]))
        else:
            kinds.append("date" if fid in DATE_FORMAT_IDS else "percent" if fid in PERCENT_FORMAT_IDS else "")
    return kinds


def _xlsx_cells(z: zipfile.ZipFile, name: str, strings: list, kinds: list, date1904: bool, grid: _Grid) -> None:
    try:
        info = z.getinfo(name)
    except KeyError:
        return
    if info.file_size > MAX_XML * 4:
        grid.cut = True
        return
    with z.open(info) as f:
        if unsafe_xml(f.read(PROLOG)):
            raise DocumentError("The workbook has a construct Excel does not write; not read")
    next_row = 0
    with z.open(info) as f:
        try:
            for _event, el in ET.iterparse(f, events=("end",)):
                if el.tag == S + "row":
                    if el.get("r", "").isdigit():
                        next_row = int(el.get("r")) - 1
                    col = 0
                    for c in el.findall(S + "c"):
                        m = _REF.match(c.get("r", ""))
                        if m:
                            col = _column(m.group(1))
                        grid.put(next_row, col, _xlsx_value(c, strings, kinds, date1904))
                        col += 1
                    next_row += 1
                    el.clear()
                    if grid.cut and next_row >= MAX_ROWS:
                        break
        except ET.ParseError as e:
            raise DocumentError("A sheet of the workbook is damaged") from e


def _xlsx_value(c: ET.Element, strings: list, kinds: list, date1904: bool) -> str:
    t = c.get("t", "n")
    if t == "inlineStr":
        inline = c.find(S + "is")
        return _rich_text(inline) if inline is not None else ""
    v = c.find(S + "v")
    raw = v.text if v is not None and v.text is not None else ""
    if t == "s":
        return strings[int(raw)] if raw.isdigit() and int(raw) < len(strings) else ""
    if t in ("str", "e"):
        return raw
    if t == "b":
        return "TRUE" if raw == "1" else "FALSE"
    if raw == "":
        return ""
    try:
        number = float(raw)
    except ValueError:
        return raw
    style = c.get("s", "0")
    kind = kinds[int(style)] if style.isdigit() and int(style) < len(kinds) else ""
    return value_text(number, kind, date1904)


# .xls (BIFF8) ----------------------------------------------------------------------------------------------------------------------
ERRORS = {0x00: "#NULL!", 0x07: "#DIV/0!", 0x0F: "#VALUE!", 0x17: "#REF!", 0x1D: "#NAME?", 0x24: "#NUM!", 0x2A: "#N/A"}


def _records(data: bytes, pos: int):
    """(id, body, offset) of each record from ``pos``; stops at the end of the data or a cut record."""
    while pos + 4 <= len(data):
        rid, size = struct.unpack_from("<HH", data, pos)
        body = data[pos + 4:pos + 4 + size]
        if len(body) < size:
            return
        yield rid, body, pos
        pos += 4 + size


class _Pieces:
    """A record and its CONTINUE records read as one, the way the SST needs: where a string's characters run over into the next
    piece, that piece starts with a new flags byte saying whether the rest is one or two bytes a character."""

    def __init__(self, pieces: list) -> None:
        self.pieces = pieces
        self.i = 0
        self.pos = 0

    def _ensure(self) -> None:
        while self.i < len(self.pieces) and self.pos >= len(self.pieces[self.i]):
            self.i += 1
            self.pos = 0
        if self.i >= len(self.pieces):
            raise DocumentError("The workbook's string table is cut short")

    def read(self, n: int) -> bytes:
        out = b""
        while n > 0:
            self._ensure()
            piece = self.pieces[self.i]
            take = piece[self.pos:self.pos + n]
            self.pos += len(take)
            n -= len(take)
            out += take
        return out

    def chars(self, count: int, wide: bool) -> str:
        out = []
        while count > 0:
            if self.i < len(self.pieces) and self.pos >= len(self.pieces[self.i]):
                self.i += 1
                self.pos = 0
                wide = bool(self.read(1)[0] & 1)              # a new piece in the middle of characters: a new flags byte
            self._ensure()
            piece = self.pieces[self.i]
            width = 2 if wide else 1
            n = min(count, (len(piece) - self.pos) // width)
            if n == 0:
                raise DocumentError("The workbook's string table is damaged")
            chunk = piece[self.pos:self.pos + n * width]
            out.append(chunk.decode("utf-16-le" if wide else "latin-1", "replace"))
            self.pos += n * width
            count -= n
        return "".join(out)

    def string(self) -> str:
        cch, = struct.unpack("<H", self.read(2))
        flags = self.read(1)[0]
        runs = struct.unpack("<H", self.read(2))[0] if flags & 0x08 else 0
        ext = struct.unpack("<I", self.read(4))[0] if flags & 0x04 else 0
        text = self.chars(cch, bool(flags & 0x01))
        self.read(4 * runs + ext)
        return text


def _unicode(body: bytes, pos: int, short: bool = False) -> str:
    """An XLUnicodeString (or the short form with a one-byte count) inside one record."""
    if short:
        cch, pos = body[pos], pos + 1
    else:
        cch, = struct.unpack_from("<H", body, pos)
        pos += 2
    flags = body[pos]
    pos += 1
    if flags & 0x08:
        pos += 2
    if flags & 0x04:
        pos += 4
    if flags & 0x01:
        return body[pos:pos + 2 * cch].decode("utf-16-le", "replace")
    return body[pos:pos + cch].decode("latin-1", "replace")


def _rk(rk: int) -> float:
    if rk & 0x02:
        value = float(rk >> 2 if rk < 0x80000000 else (rk >> 2) - (1 << 30))
    else:
        value = struct.unpack("<d", struct.pack("<Q", (rk & 0xFFFFFFFC) << 32))[0]
    return value / 100 if rk & 0x01 else value


def xls_sheets(path: str) -> list:
    try:
        with Compound(path) as cf:
            if cf.has("Workbook"):
                data = cf.stream("Workbook")
            elif cf.has("Book"):
                raise DocumentError("An Excel 5/95 workbook: open it in Excel to see it")
            else:
                raise DocumentError("This Office 97 file is not an Excel workbook")
        return _biff8(data)
    except (struct.error, IndexError) as e:
        raise DocumentError("The workbook is damaged") from e


def _biff8(data: bytes) -> list:
    bound, formats, xf_formats = [], {}, []
    date1904 = False
    sst: list = []
    records = list(_globals(data))
    for n, (rid, body, _pos) in enumerate(records):
        if rid == 0x002F:
            raise DocumentError("The workbook is protected by a password")
        if rid == 0x0085:
            pos, hidden, kind = struct.unpack_from("<IBB", body, 0)
            bound.append((pos, _unicode(body, 6, short=True), hidden & 0x03 != 0, kind))
        elif rid == 0x0022:
            date1904 = struct.unpack_from("<H", body, 0)[0] == 1
        elif rid == 0x041E:
            formats[struct.unpack_from("<H", body, 0)[0]] = _unicode(body, 2)
        elif rid == 0x00E0:
            xf_formats.append(struct.unpack_from("<H", body, 2)[0])
        elif rid == 0x00FC:
            pieces = [body]
            for rid2, body2, _ in records[n + 1:]:
                if rid2 != 0x003C:
                    break
                pieces.append(body2)
            reader = _Pieces(pieces)
            _total, unique = struct.unpack("<II", reader.read(8))
            try:
                for _ in range(unique):
                    sst.append(reader.string())
            except DocumentError:
                pass                                       # a short table: the strings read so far are kept
    kinds = []
    for fid in xf_formats:
        if fid in formats:
            kinds.append(format_kind(formats[fid]))
        else:
            kinds.append("date" if fid in DATE_FORMAT_IDS else "percent" if fid in PERCENT_FORMAT_IDS else "")
    budget = [MAX_CELLS]
    sheets = []
    for pos, name, hidden, kind in bound:
        if kind != 0:
            continue                                       # a chart, a macro sheet or a module: no cells
        sheet = Sheet(name, hidden=hidden)
        grid = _Grid(budget)
        _biff8_cells(data, pos, sst, kinds, date1904, grid)
        sheet.rows, sheet.cut = grid.rows(), grid.cut
        sheets.append(sheet)
    return sheets


def _globals(data: bytes):
    for rec in _records(data, 0):
        yield rec
        if rec[0] == 0x000A:                               # EOF of the globals
            return


def _biff8_cells(data: bytes, start: int, sst: list, kinds: list, date1904: bool, grid: _Grid) -> None:
    def kind(xf: int) -> str:
        return kinds[xf] if xf < len(kinds) else ""

    pending = None                                         # a formula whose string result follows in a STRING record
    first = True
    for rid, b, _pos in _records(data, start):
        if first:
            first = False
            if rid != 0x0809:
                return                                     # the sheet's offset does not point at a BOF: nothing to read
            continue
        if rid == 0x000A:
            return
        if rid == 0x00FD and len(b) >= 10:                 # LABELSST
            row, col, _xf, i = struct.unpack_from("<HHHI", b, 0)
            grid.put(row, col, sst[i] if i < len(sst) else "")
        elif rid == 0x0203 and len(b) >= 14:               # NUMBER
            row, col, xf, value = struct.unpack_from("<HHHd", b, 0)
            grid.put(row, col, value_text(value, kind(xf), date1904))
        elif rid == 0x027E and len(b) >= 10:               # RK
            row, col, xf, rk = struct.unpack_from("<HHHI", b, 0)
            grid.put(row, col, value_text(_rk(rk), kind(xf), date1904))
        elif rid == 0x00BD and len(b) >= 6:                # MULRK
            row, col = struct.unpack_from("<HH", b, 0)
            for k in range((len(b) - 6) // 6):
                xf, rk = struct.unpack_from("<HI", b, 4 + 6 * k)
                grid.put(row, col + k, value_text(_rk(rk), kind(xf), date1904))
        elif rid == 0x0006 and len(b) >= 14:               # FORMULA: its last result
            row, col, xf = struct.unpack_from("<HHH", b, 0)
            result = b[6:14]
            if result[6:8] == b"\xff\xff":
                if result[0] == 0:
                    pending = (row, col)
                elif result[0] == 1:
                    grid.put(row, col, "TRUE" if result[2] else "FALSE")
                elif result[0] == 2:
                    grid.put(row, col, ERRORS.get(result[2], "#ERROR"))
            else:
                grid.put(row, col, value_text(struct.unpack("<d", result)[0], kind(xf), date1904))
        elif rid == 0x0207 and pending is not None:        # STRING: the text a formula gave
            grid.put(pending[0], pending[1], _unicode(b, 0))
            pending = None
        elif rid == 0x0205 and len(b) >= 8:                # BOOLERR
            row, col, _xf, value, is_error = struct.unpack_from("<HHHBB", b, 0)
            grid.put(row, col, ERRORS.get(value, "#ERROR") if is_error else ("TRUE" if value else "FALSE"))
        elif rid in (0x0204, 0x00D6) and len(b) >= 9:      # LABEL, RSTRING (older writers)
            row, col = struct.unpack_from("<HH", b, 0)
            grid.put(row, col, _unicode(b, 6))


# .ods (OpenDocument) ---------------------------------------------------------------------------------------------------------------
OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_DURATION = re.compile(r"PT(\d+)H(\d+)M(\d+(?:\.\d+)?)S")


def _repeat(el: ET.Element, attr: str) -> int:
    value = el.get(TABLE + attr, "1")
    return int(value) if value.isdigit() else 1


STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
NUMBER = "{urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0}"
_DATA_STYLE_KINDS = {NUMBER + "date-style": "date", NUMBER + "time-style": "date", NUMBER + "percentage-style": "percent"}


class _Lookup:
    """Cell style name -> date / percent, through its data style, looked up when a cell is read (the styles may come in any order)."""

    def __init__(self, cell_data: dict, data_kinds: dict) -> None:
        self.cell_data, self.data_kinds = cell_data, data_kinds

    def get(self, name: str, default: str = "") -> str:
        return self.data_kinds.get(self.cell_data.get(name, ""), default)


def _ods_value(cell: ET.Element, cell_kinds: "_Lookup") -> str:
    """The cell's typed value as text, the same way an Excel cell is shown (ISO dates, percent, TRUE/FALSE); a string as its text.
    A number whose cell style shows it as a date, a time or a percentage is shown that way (Excel's own .ods export does that)."""
    kind = cell.get(OFFICE + "value-type", "")
    if kind in ("float", "currency", "percentage"):
        try:
            value = float(cell.get(OFFICE + "value", ""))
        except ValueError:
            value = None
        if value is not None:
            styled = cell_kinds.get(cell.get(TABLE + "style-name", ""), "")
            return value_text(value, "percent" if kind == "percentage" else styled, False)
    elif kind == "date":
        day, _, clock = cell.get(OFFICE + "date-value", "").partition("T")
        if not clock or clock.startswith("00:00:00"):
            return day
        return f"{day} {clock[:5] if clock[6:8] == '00' else clock[:8]}"
    elif kind == "time":
        m = _DURATION.match(cell.get(OFFICE + "time-value", ""))
        if m:
            h, mi, s = int(m.group(1)), int(m.group(2)), round(float(m.group(3)))
            return f"{h:02d}:{mi:02d}" + (f":{s:02d}" if s else "")
    elif kind == "boolean":
        return "TRUE" if cell.get(OFFICE + "boolean-value", "") == "true" else "FALSE"
    return " ".join("".join(p.itertext()) for p in cell.findall(TEXT + "p"))


def ods_sheets(path: str) -> list:
    try:
        with zipfile.ZipFile(path) as z:
            try:
                info = z.getinfo("content.xml")
            except KeyError:
                raise DocumentError("This zip file is not a spreadsheet") from None
            if info.file_size > MAX_XML * 4:
                raise DocumentError("The spreadsheet is too big to show")
            with z.open(info) as f:
                if unsafe_xml(f.read(PROLOG)):
                    raise DocumentError("The spreadsheet has a construct LibreOffice does not write; not read")
            budget = [MAX_CELLS]
            sheets: list = []
            grid: Optional[_Grid] = None
            row = 0
            data_kinds: dict = {}                          # data style name -> date / percent (styles.xml and content.xml)
            cell_data: dict = {}                           # cell style name -> its data style name
            hidden_tables: set = set()                     # table style names with table:display="false"
            styles = _xml(z, "styles.xml")
            for el in (styles.iter() if styles is not None else ()):
                if el.tag in _DATA_STYLE_KINDS:
                    data_kinds[el.get(STYLE + "name", "")] = _DATA_STYLE_KINDS[el.tag]
                elif el.tag == STYLE + "style" and el.get(STYLE + "data-style-name"):
                    cell_data[el.get(STYLE + "name", "")] = el.get(STYLE + "data-style-name")
            cell_kinds = _Lookup(cell_data, data_kinds)
            with z.open(info) as f:
                for event, el in ET.iterparse(f, events=("start", "end")):
                    if event == "start":
                        if el.tag == TABLE + "table":
                            sheets.append(Sheet(el.get(TABLE + "name", "?"),
                                                hidden=el.get(TABLE + "style-name", "") in hidden_tables))
                            grid, row = _Grid(budget), 0
                        continue
                    if el.tag in _DATA_STYLE_KINDS:
                        data_kinds[el.get(STYLE + "name", "")] = _DATA_STYLE_KINDS[el.tag]
                    elif el.tag == STYLE + "style":
                        name, family = el.get(STYLE + "name", ""), el.get(STYLE + "family", "")
                        if family == "table-cell" and el.get(STYLE + "data-style-name"):
                            cell_data[name] = el.get(STYLE + "data-style-name")
                        elif family == "table":
                            props = el.find(STYLE + "table-properties")
                            if props is not None and props.get(TABLE + "display", "true") == "false":
                                hidden_tables.add(name)
                    if el.tag == TABLE + "table-row" and grid is not None:
                        rows = _repeat(el, "number-rows-repeated")
                        values = []
                        for cell in el:
                            if cell.tag in (TABLE + "table-cell", TABLE + "covered-table-cell"):
                                values.append((_ods_value(cell, cell_kinds), _repeat(cell, "number-columns-repeated")))
                        if any(v for v, _ in values):
                            for r in range(min(rows, MAX_ROWS + 1)):
                                col = 0
                                for text, times in values:
                                    for _ in range(min(times, MAX_COLS + 1)):
                                        grid.put(row + r, col, text)
                                        col += 1
                        row += rows
                        el.clear()
                    elif el.tag == TABLE + "table" and grid is not None:
                        sheets[-1].rows, sheets[-1].cut = grid.rows(), grid.cut
                        grid = None
                        el.clear()
            return sheets
    except zipfile.BadZipFile as e:
        raise DocumentError("The spreadsheet is damaged") from e
    except ET.ParseError as e:
        raise DocumentError("The spreadsheet is damaged") from e

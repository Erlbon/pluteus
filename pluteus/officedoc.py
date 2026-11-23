"""The text of a Word document, for the document viewer: a quick look without Word, on any system.

Copied from Dossier (dossier/core/officedoc.py, commit d3f7956, 2026-11-23), with the epub reading left out: pluteus has its own
epub reader. Keep the two copies in step by hand when a reader bug is fixed in either.

``.docx`` (and .docm, .dotx, .dotm) is a zip with the text in ``word/document.xml``: it comes out as Markdown, with headings, bold,
italic, lists and tables. ``.doc`` (and .dot) from Word 97 and later keeps its text in the ``WordDocument`` stream of an Office 97
container file, in pieces a table lists: it comes out as plain text. A simple (not fast-saved) Word 6/95 file has its text in one run
and is read too. Pictures, layout, headers, footnotes and comments are left out.

Only the standard library: the container reader (``Compound``) is here too, and an Excel 97 reader can use it the same way.
The viewer draws the result. OpenDocument text, RTF and PowerPoint are read in ``officetext.py`` with the helpers here.
"""
from __future__ import annotations

import re
import struct
import zipfile
from dataclasses import dataclass
from typing import Optional
from xml.etree import ElementTree as ET

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"
DOC_EXTENSIONS = (".doc", ".dot")
DOCX_EXTENSIONS = (".docx", ".docm", ".dotx", ".dotm")
MAX_STREAM = 256 * 1024 * 1024        # a stream bigger than this is not read into memory
MAX_XML = 64 * 1024 * 1024            # word/document.xml unpacked: a zip bomb stops here

ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF


class DocumentError(Exception):
    """The file cannot be read as a document; the message says why, in words for the status line."""


@dataclass(frozen=True)
class Chapter:
    title: str
    plain: str
    markdown: str


@dataclass(frozen=True)
class Document:
    kind: str                         # "Word document", "Word 97 document", "EPUB book"...
    plain: str                        # the text, always there (for a book: the first chapter's)
    markdown: Optional[str] = None    # the text with headings, emphasis, lists and tables, when the format carries them
    chapters: tuple = ()              # a book: its chapters in reading order, shown one at a time


ODT_EXTENSIONS = (".odt", ".ott")
RTF_EXTENSIONS = (".rtf", ".doc")     # many old files called .doc are RTF inside
PPTX_EXTENSIONS = (".pptx", ".pptm", ".ppsx", ".ppsm", ".potx", ".potm")


def read_document(path: str) -> Document:
    from . import officetext as textdocs
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(OLE_MAGIC):
        return Document("Word 97 document", doc_text(path))
    if head.startswith(b"{\\rtf"):
        return textdocs.rtf_document(path)
    if head.startswith(ZIP_MAGIC):
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
        except zipfile.BadZipFile as e:
            raise DocumentError("The document is damaged") from e
        if "word/document.xml" in names:
            markdown, plain = docx_text(path)
            return Document("Word document", plain, markdown)
        if "ppt/presentation.xml" in names:
            return textdocs.pptx_document(path)
        if "content.xml" in names and "META-INF/container.xml" not in names:     # an epub is a book, not a document
            return textdocs.odt_document(path)
    raise DocumentError("This is not a document pluteus can read inside, whatever its name says")


# The Office 97 container (Compound File Binary) ---------------------------------------------------------------------------------
class Compound:
    """The streams at the top of an Office 97 container file, read on demand. Damaged chains and loops are errors, never hangs."""

    def __init__(self, path: str) -> None:
        self._f = open(path, "rb")
        try:
            self._f.seek(0, 2)
            self._size = self._f.tell()
            self._f.seek(0)
            header = self._f.read(512)
            if len(header) < 512 or not header.startswith(OLE_MAGIC):
                raise DocumentError("Not an Office 97 file")
            shift = struct.unpack_from("<H", header, 30)[0]
            if shift not in (9, 12):
                raise DocumentError("The file's header is damaged")
            self.sector = 1 << shift
            self.mini_sector = 1 << struct.unpack_from("<H", header, 32)[0]
            self.cutoff = struct.unpack_from("<I", header, 56)[0]
            n_fat = struct.unpack_from("<I", header, 44)[0]
            first_dir = struct.unpack_from("<I", header, 48)[0]
            first_minifat = struct.unpack_from("<I", header, 60)[0]
            first_difat, n_difat = struct.unpack_from("<II", header, 68)
            self.wide_sizes = struct.unpack_from("<H", header, 26)[0] >= 4
            fat_sectors = [s for s in struct.unpack_from("<109I", header, 76) if s < ENDOFCHAIN]
            per = self.sector // 4 - 1
            s, seen = first_difat, 0
            while s < ENDOFCHAIN and seen <= n_difat:
                ids = struct.unpack("<%dI" % (per + 1), self._sector(s))
                fat_sectors.extend(x for x in ids[:per] if x < ENDOFCHAIN)
                s, seen = ids[per], seen + 1
            fat_sectors = fat_sectors[:n_fat] if n_fat else fat_sectors
            self.fat = []
            for s in fat_sectors:
                self.fat.extend(struct.unpack("<%dI" % (self.sector // 4), self._sector(s)))
            directory = self._chain_bytes(first_dir)
            self.entries = [directory[i:i + 128] for i in range(0, len(directory) - 127, 128)]
            if not self.entries:
                raise DocumentError("The file has no directory")
            minifat_bytes = self._chain_bytes(first_minifat) if first_minifat < ENDOFCHAIN else b""
            self.minifat = list(struct.unpack("<%dI" % (len(minifat_bytes) // 4), minifat_bytes[:len(minifat_bytes) // 4 * 4]))
            self._ministream: Optional[bytes] = None
            self.names = self._top_level()
        except (struct.error, OSError, DocumentError):
            self._f.close()
            raise

    def close(self) -> None:
        self._f.close()

    def __enter__(self) -> "Compound":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _sector(self, n: int) -> bytes:
        offset = (n + 1) * self.sector
        if offset + self.sector > self._size + self.sector:          # the last sector may be cut short
            raise DocumentError("The file is cut short or damaged")
        self._f.seek(offset)
        return self._f.read(self.sector)

    def _chain(self, start: int, fat: list) -> list:
        out = []
        s = start
        while s < ENDOFCHAIN:
            if s >= len(fat) or len(out) > len(fat):
                raise DocumentError("The file's sector chain is damaged")
            out.append(s)
            s = fat[s]
        return out

    def _chain_bytes(self, start: int) -> bytes:
        """A chain of full sectors (the FAT's), joined."""
        return b"".join(self._sector(s) for s in self._chain(start, self.fat))

    def _entry(self, i: int) -> tuple:
        e = self.entries[i]
        name_len = struct.unpack_from("<H", e, 64)[0]
        name = e[:max(0, min(name_len, 64) - 2)].decode("utf-16-le", "replace")
        kind = e[66]
        left, right, child = struct.unpack_from("<III", e, 68)
        start = struct.unpack_from("<I", e, 116)[0]
        size = struct.unpack_from("<Q", e, 120)[0] if self.wide_sizes else struct.unpack_from("<I", e, 120)[0]
        return name, kind, left, right, child, start, size

    def _top_level(self) -> dict:
        """The streams directly under the root, by lower-case name (an embedded object's own streams are not mixed in)."""
        names = {}
        child = self._entry(0)[4]
        todo, seen = [child], set()
        while todo:
            i = todo.pop()
            if i >= len(self.entries) or i in seen:
                continue
            seen.add(i)
            name, kind, left, right, _child, _start, _size = self._entry(i)
            if kind == 2:
                names[name.lower()] = i
            todo.extend((left, right))
        return names

    def has(self, name: str) -> bool:
        return name.lower() in self.names

    def stream(self, name: str) -> bytes:
        i = self.names.get(name.lower())
        if i is None:
            raise DocumentError(f"The file has no {name} stream")
        _name, _kind, _l, _r, _c, start, size = self._entry(i)
        if size > MAX_STREAM:
            raise DocumentError("The document is too big to show")
        if size < self.cutoff:
            if self._ministream is None:
                root = self._entry(0)
                self._ministream = self._chain_bytes(root[5])[:root[6]]
            mini = self._ministream
            data = b"".join(mini[s * self.mini_sector:(s + 1) * self.mini_sector] for s in self._chain(start, self.minifat))
        else:
            data = self._chain_bytes(start)
        if len(data) < size:
            raise DocumentError("The file is cut short or damaged")
        return data[:size]


# .doc ------------------------------------------------------------------------------------------------------------------------------
def doc_text(path: str) -> str:
    try:
        with Compound(path) as cf:
            if not cf.has("WordDocument"):
                raise DocumentError("This Office 97 file is not a Word document")
            wd = cf.stream("WordDocument")
            if len(wd) < 0x20 or struct.unpack_from("<H", wd, 0)[0] != 0xA5EC:
                raise DocumentError("The Word document's header is damaged")
            n_fib, = struct.unpack_from("<H", wd, 2)
            flags, = struct.unpack_from("<H", wd, 0x0A)
            if flags & 0x0100:
                raise DocumentError("The document is protected by a password")
            if n_fib < 0xC1:
                return _clean(_word95_text(wd, flags))
            table = cf.stream("1Table" if flags & 0x0200 else "0Table")
            return _clean(_word97_text(wd, table))
    except (struct.error, IndexError) as e:
        raise DocumentError("The Word document is damaged") from e


def _word95_text(wd: bytes, flags: int) -> str:
    if flags & 0x0004:
        raise DocumentError("A fast-saved Word 6/95 document: open it in Word to see it")
    fc_min, fc_mac = struct.unpack_from("<II", wd, 0x18)
    return wd[fc_min:fc_mac].decode("cp1252", "replace")


def _word97_text(wd: bytes, table: bytes) -> str:
    csw, = struct.unpack_from("<H", wd, 32)
    lw = 34 + csw * 2
    cslw, = struct.unpack_from("<H", wd, lw)
    rg_lw = lw + 2
    ccp_text, = struct.unpack_from("<I", wd, rg_lw + 12)
    blob = rg_lw + cslw * 4 + 2
    fc_clx, lcb_clx = struct.unpack_from("<II", wd, blob + 33 * 8)
    clx = table[fc_clx:fc_clx + lcb_clx]
    i = 0
    plc = b""
    while i < len(clx):
        if clx[i] == 1:                                          # a Prc (formatting): skipped
            i += 3 + struct.unpack_from("<H", clx, i + 1)[0]
        elif clx[i] == 2:                                        # the Pcdt: where every piece of text is
            lcb, = struct.unpack_from("<I", clx, i + 1)
            plc = clx[i + 5:i + 5 + lcb]
            break
        else:
            raise DocumentError("The Word document's text table is damaged")
    if not plc:
        raise DocumentError("The Word document's text table is missing")
    n = (len(plc) - 4) // 12
    cps = struct.unpack_from("<%dI" % (n + 1), plc, 0)
    parts = []
    for k in range(n):
        start, end = cps[k], min(cps[k + 1], ccp_text)
        if start >= end:
            if start >= ccp_text:
                break
            continue
        fc, = struct.unpack_from("<I", plc, 4 * (n + 1) + 8 * k + 2)
        count = end - start
        if fc & 0x40000000:
            off = (fc & 0x3FFFFFFF) // 2
            parts.append(wd[off:off + count].decode("cp1252", "replace"))
        else:
            parts.append(wd[fc:fc + 2 * count].decode("utf-16-le", "replace"))
    return "".join(parts)


_DROP = {chr(c) for c in range(0x20)} - {"\t", "\n"}


def _clean(raw: str) -> str:
    """Word's special characters to plain text: a field shows its result and not its code, a paragraph mark is a new paragraph,
    a cell mark a tab and a row's end a new line. Pictures and other anchors are dropped."""
    raw = raw.replace("\x07\x07", "\n")
    out = []
    fields: list = []                                       # one entry per open field: True once its result has begun
    for ch in raw:
        if ch == "\x13":
            fields.append(False)
            continue
        if ch == "\x14":
            if fields:
                fields[-1] = True
            continue
        if ch == "\x15":
            if fields:
                fields.pop()
            continue
        if fields and not all(fields):
            continue
        if ch in "\r\x0b\x0c":
            out.append("\n")
        elif ch == "\x07":
            out.append("\t")
        elif ch == "\x1e":
            out.append("-")
        elif ch in _DROP:
            continue
        else:
            out.append(ch)
    lines = [line.rstrip() for line in "".join(out).split("\n")]
    paragraphs = [line for line in lines if line.strip()]
    return "\n\n".join(paragraphs)


# .docx -----------------------------------------------------------------------------------------------------------------------------
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_OFF = ("0", "false", "off", "none")
_MD_SPECIAL = re.compile(r"([\\`*_\[\]~|&#])")
_LINE_START = re.compile(r"^(\s*)([-+=>]|\d+[.)])")


PROLOG = 64 * 1024
_INTERNAL_SUBSET = re.compile(rb"<!DOCTYPE[^>\[]*\[")


def unsafe_xml(head: bytes) -> bool:
    """XML that defines entities (an internal DOCTYPE subset): the way to a 'billion laughs' bomb. A plain DOCTYPE line, as an
    epub's table of contents has, defines nothing and is fine; expat never fetches an external DTD."""
    return b"<!ENTITY" in head or bool(_INTERNAL_SUBSET.search(head))


def _xml(z: zipfile.ZipFile, name: str) -> Optional[ET.Element]:
    try:
        info = z.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_XML:
        raise DocumentError("The document is too big to show")
    data = z.read(info)
    if unsafe_xml(data[:PROLOG]):
        raise DocumentError("The document defines its own XML entities, which no office program writes; not read")
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:
        raise DocumentError("The document's text is damaged") from e


def _styles(styles: Optional[ET.Element]) -> tuple:
    """(paragraph style id -> heading level 1-6, the style ids that are list styles). A heading is known by the style's built-in name
    (``heading 2``, the same in every language of Word), its outline level, or the style it is based on; a list style carries numbering."""
    if styles is None:
        return {}, set()
    own, based, numbered = {}, {}, set()
    for st in styles.iter(W + "style"):
        sid = st.get(W + "styleId", "")
        name_el = st.find(W + "name")
        name = (name_el.get(W + "val", "") if name_el is not None else "").lower()
        level = 0
        m = re.fullmatch(r"heading (\d)", name)
        if m:
            level = int(m.group(1))
        elif name == "title":
            level = 1
        else:
            ol = st.find(f"{W}pPr/{W}outlineLvl")
            if ol is not None and ol.get(W + "val", "9").isdigit() and int(ol.get(W + "val")) < 6:
                level = int(ol.get(W + "val")) + 1
        own[sid] = level
        if st.find(f"{W}pPr/{W}numPr") is not None:
            numbered.add(sid)
        b = st.find(W + "basedOn")
        if b is not None:
            based[sid] = b.get(W + "val", "")
    levels, listed = {}, set()
    for sid in own:
        s, hops = sid, 0
        while s and not own.get(s) and hops < 10:
            s, hops = based.get(s, ""), hops + 1
        levels[sid] = min(own.get(s, 0), 6)
        s, hops = sid, 0
        while s and s not in numbered and hops < 10:
            s, hops = based.get(s, ""), hops + 1
        if s in numbered and not levels[sid]:
            listed.add(sid)
    return levels, listed


def _on(rpr: Optional[ET.Element], tag: str) -> bool:
    if rpr is None:
        return False
    el = rpr.find(W + tag)
    return el is not None and el.get(W + "val", "true").lower() not in _OFF


def _escape(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", text)


def _runs(p: ET.Element) -> list:
    """(text, bold, italic) for each run of the paragraph, in order; deleted text and field codes are not w:t and so not taken."""
    segments = []
    for r in p.iter(W + "r"):
        rpr = r.find(W + "rPr")
        bold, italic = _on(rpr, "b"), _on(rpr, "i")
        for child in r:
            if child.tag == W + "t":
                segments.append((child.text or "", bold, italic))
            elif child.tag == W + "tab":
                segments.append(("\t", False, False))
            elif child.tag in (W + "br", W + "cr"):
                segments.append(("\n", False, False))
            elif child.tag == W + "noBreakHyphen":
                segments.append(("-", bold, italic))
    return segments


def block(text_md: str, level: int = 0, listed: bool = False, quoted: bool = False) -> str:
    """One rendered paragraph as a Markdown block: a heading of ``level``, a list item, a quote, or a paragraph whose first
    character would otherwise start a list or a quote."""
    if level:
        return "#" * min(level, 6) + " " + text_md.replace("\\\n", " ")
    if listed:
        return "- " + text_md
    text = _LINE_START.sub(lambda m: m.group(1) + "\\" + m.group(2), text_md, count=1)
    return "> " + text.replace("\\\n", "\\\n> ") if quoted else text


def md_table(rows_md: list) -> str:
    """Rows of rendered cells as a Markdown table, the first row as its header."""
    if not rows_md:
        return ""
    width = max(len(r) for r in rows_md) or 1
    lines = []
    for i, row in enumerate(rows_md):
        row = [c.replace("\\\n", " ").replace("\n", " ") for c in row] + [""] * (width - len(row))
        lines.append("| " + " | ".join(row) + " |")
        if i == 0:
            lines.append("|" + " --- |" * width)
    return "\n".join(lines)


def _paragraph(p: ET.Element) -> tuple:
    """The paragraph as (markdown, plain), without the heading or list mark."""
    return render_segments(_runs(p))


def merge_segments(segments: list) -> list:
    """Neighbouring (text, bold, italic) pieces with the same emphasis joined, so ``**a****b**`` becomes ``**ab**``."""
    merged: list = []
    for text, bold, italic in segments:
        if merged and merged[-1][1:] == (bold, italic) and "\n" not in text and "\n" not in merged[-1][0]:
            merged[-1] = (merged[-1][0] + text, bold, italic)
        else:
            merged.append((text, bold, italic))
    return merged


def render_segments(segments: list) -> tuple:
    """(text, bold, italic) pieces of one paragraph as (markdown, plain). ``"\\n"`` alone is a line break inside the paragraph."""
    md, plain = [], []
    for text, bold, italic in merge_segments(segments):
        plain.append(text)
        if text == "\n":
            md.append("\\\n")
            continue
        inner = text.strip()
        if not inner or not (bold or italic):
            md.append(_escape(text))
            continue
        mark = ("**" if bold else "") + ("*" if italic else "")
        lead, trail = text[:len(text) - len(text.lstrip())], text[len(text.rstrip()):]
        md.append(f"{lead}{mark}{_escape(inner)}{mark[::-1]}{trail}")
    return "".join(md).strip(), "".join(plain).strip()


def _table(tbl: ET.Element) -> tuple:
    rows_md, rows_plain = [], []
    for tr in tbl.findall(W + "tr"):
        cells_md, cells_plain = [], []
        for tc in tr.findall(W + "tc"):
            parts = [_paragraph(p) for p in tc.iter(W + "p")]
            cells_md.append(" ".join(m for m, _ in parts if m).replace("\\\n", " ").replace("\n", " "))
            cells_plain.append(" ".join(t for _, t in parts if t).replace("\n", " "))
        rows_md.append(cells_md)
        rows_plain.append(cells_plain)
    return md_table(rows_md), "\n".join("\t".join(r) for r in rows_plain)


def _blocks(parent: ET.Element, styles: tuple, md: list, plain: list) -> None:
    levels, list_styles = styles
    for el in parent:
        if el.tag == W + "p":
            text_md, text_plain = _paragraph(el)
            if not text_plain:
                continue
            ppr = el.find(W + "pPr")
            level, listed = 0, False
            if ppr is not None:
                ps = ppr.find(W + "pStyle")
                style = ps.get(W + "val", "") if ps is not None else ""
                level = levels.get(style, 0)
                ol = ppr.find(W + "outlineLvl")
                if not level and ol is not None and ol.get(W + "val", "9").isdigit() and int(ol.get(W + "val")) < 6:
                    level = int(ol.get(W + "val")) + 1
                listed = ppr.find(W + "numPr") is not None or style in list_styles
            md.append(block(text_md, level, listed))
            plain.append(text_plain)
        elif el.tag == W + "tbl":
            t_md, t_plain = _table(el)
            if t_md:
                md.append(t_md)
                plain.append(t_plain)
        elif el.tag in (W + "sdt", W + "sdtContent", W + "customXml", W + "ins"):
            _blocks(el, styles, md, plain)


def join_markdown(blocks: list) -> str:
    """Blank lines between blocks, but list items of one list stay together."""
    out = []
    for i, b in enumerate(blocks):
        if i and not (b.startswith("- ") and blocks[i - 1].startswith("- ")):
            out.append("")
        out.append(b)
    return "\n".join(out)


def docx_text(path: str) -> tuple:
    """(markdown, plain) of a .docx."""
    try:
        with zipfile.ZipFile(path) as z:
            doc = _xml(z, "word/document.xml")
            if doc is None:
                raise DocumentError("This zip file is not a Word document")
            styles = _styles(_xml(z, "word/styles.xml"))
    except zipfile.BadZipFile as e:
        raise DocumentError("The Word document is damaged") from e
    body = doc.find(W + "body")
    md: list = []
    plain: list = []
    if body is not None:
        _blocks(body, styles, md, plain)
    return join_markdown(md), "\n\n".join(plain)

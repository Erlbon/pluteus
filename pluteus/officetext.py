"""The text of documents that are not Word's own, for the document viewer: OpenDocument text (.odt), RTF and PowerPoint (.pptx).
Like ``officedoc``: the words with headings, emphasis, lists and tables as Markdown; pictures, layout and styling are left out.

Copied from Dossier (dossier/core/textdocs.py, commit d3f7956, 2026-11-23) without its epub part. Only the standard library. Zip
members are size-capped and XML that defines its own entities is refused (``officedoc.unsafe_xml``).
"""
from __future__ import annotations

import posixpath
import re
import zipfile
from typing import Optional
from xml.etree import ElementTree as ET

from .officedoc import MAX_XML, Document, DocumentError, _xml, block, join_markdown, md_table, render_segments

# OpenDocument text ---------------------------------------------------------------------------------------------------------------
OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
FO = "{urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0}"
_SKIP_ODT = {TEXT + "tracked-changes", TEXT + "note", OFFICE + "annotation", TEXT + "bookmark-ref", TEXT + "sequence-decls"}


def _odt_emphasis(*roots: Optional[ET.Element]) -> dict:
    """Text style name -> (bold, italic), from the automatic styles of content.xml and the named styles of styles.xml."""
    out = {}
    for root in roots:
        if root is None:
            continue
        for st in root.iter(STYLE + "style"):
            props = st.find(STYLE + "text-properties")
            if props is None:
                continue
            out[st.get(STYLE + "name", "")] = (props.get(FO + "font-weight", "") == "bold",
                                               props.get(FO + "font-style", "") == "italic")
    return out


def _odt_segments(el: ET.Element, emphasis: dict, bold: bool = False, italic: bool = False) -> list:
    b, i = emphasis.get(el.get(TEXT + "style-name", ""), (False, False))
    bold, italic = bold or b, italic or i
    out = []
    if el.text:
        out.append((el.text, bold, italic))
    for child in el:
        if child.tag in _SKIP_ODT:
            pass
        elif child.tag == TEXT + "s":
            out.append((" " * int(child.get(TEXT + "c", "1") or 1), bold, italic))
        elif child.tag == TEXT + "tab":
            out.append(("\t", False, False))
        elif child.tag == TEXT + "line-break":
            out.append(("\n", False, False))
        else:
            out.extend(_odt_segments(child, emphasis, bold, italic))
        if child.tail:
            out.append((child.tail, bold, italic))
    return out


def _odt_blocks(parent: ET.Element, emphasis: dict, md: list, plain: list, listed: bool = False) -> None:
    for el in parent:
        if el.tag in (TEXT + "p", TEXT + "h"):
            text_md, text_plain = render_segments(_odt_segments(el, emphasis))
            if not text_plain:
                continue
            level = int(el.get(TEXT + "outline-level", "1") or 1) if el.tag == TEXT + "h" else 0
            md.append(block(text_md, level, listed and not level))
            plain.append(text_plain)
        elif el.tag == TEXT + "list":
            for item in el:
                _odt_blocks(item, emphasis, md, plain, listed=True)
        elif el.tag == TABLE + "table":
            rows_md, rows_plain = [], []
            for row in el.iter(TABLE + "table-row"):
                cells_md, cells_plain = [], []
                for cell in row:
                    if cell.tag not in (TABLE + "table-cell", TABLE + "covered-table-cell"):
                        continue
                    parts = [render_segments(_odt_segments(p, emphasis)) for p in cell.iter() if p.tag in (TEXT + "p", TEXT + "h")]
                    cells_md.append(" ".join(m for m, _ in parts if m))
                    cells_plain.append(" ".join(t for _, t in parts if t))
                rows_md.append(cells_md)
                rows_plain.append(cells_plain)
            if rows_md:
                md.append(md_table(rows_md))
                plain.append("\n".join("\t".join(r) for r in rows_plain))
        elif el.tag in (TEXT + "section", TEXT + "list-item", TEXT + "list-header", TEXT + "index-body", OFFICE + "text"):
            _odt_blocks(el, emphasis, md, plain, listed)
        elif el.tag.startswith(TEXT) and el.tag.endswith("-index"):
            body = el.find(TEXT + "index-body")
            if body is not None:
                _odt_blocks(body, emphasis, md, plain)


def odt_document(path: str) -> Document:
    try:
        with zipfile.ZipFile(path) as z:
            content = _xml(z, "content.xml")
            if content is None:
                raise DocumentError("This zip file is not an OpenDocument text")
            emphasis = _odt_emphasis(content, _xml(z, "styles.xml"))
    except zipfile.BadZipFile as e:
        raise DocumentError("The document is damaged") from e
    body = content.find(f"{OFFICE}body/{OFFICE}text")
    md: list = []
    plain: list = []
    if body is not None:
        _odt_blocks(body, emphasis, md, plain)
    return Document("OpenDocument text", "\n\n".join(plain), join_markdown(md))


# RTF -----------------------------------------------------------------------------------------------------------------------------
_RTF_TOKEN = re.compile(rb"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|([^\\{}\r\n]+)|[\r\n]+", re.S)
_RTF_SKIP = {b"fonttbl", b"colortbl", b"stylesheet", b"info", b"pict", b"object", b"themedata", b"datastore", b"xmlnstbl",
             b"listtable", b"listoverridetable", b"rsidtbl", b"generator", b"header", b"headerl", b"headerr", b"headerf",
             b"footer", b"footerl", b"footerr", b"footerf", b"footnote", b"fldinst", b"latentstyles", b"mmathPr", b"revtbl",
             b"pgdsctbl", b"filetbl", b"nonshppict", b"shp", b"shpinst", b"bkmkstart", b"bkmkend", b"annotation", b"atnid",
             b"atnauthor", b"listtext", b"pntext", b"pntxta", b"pntxtb", b"userprops", b"docvar", b"template", b"operator",
             b"author", b"title", b"subject", b"keywords", b"comment", b"company", b"falt", b"panose", b"wgrffmtfilter"}
_RTF_SYMBOLS = {b"emdash": "\u2014", b"endash": "\u2013", b"bullet": "\u2022", b"lquote": "\u2018", b"rquote": "\u2019",
                b"ldblquote": "\u201c", b"rdblquote": "\u201d", b"emspace": " ", b"enspace": " ", b"qmspace": " "}
MAX_RTF = 64 * 1024 * 1024
_RTF_FONT = re.compile(rb"\\f(\d+)(?:\\[a-z]+-?\d* ?)*?\\fcharset(\d+)")
# \fcharset -> code page (Word writes Greek, Cyrillic and the rest as bytes in the font's own code page, not as \uN)
_CHARSETS = {2: "latin-1", 77: "mac_roman", 128: "cp932", 129: "cp949", 134: "cp936", 136: "cp950", 161: "cp1253", 162: "cp1254",
             163: "cp1258", 177: "cp1255", 178: "cp1256", 186: "cp1257", 204: "cp1251", 222: "cp874", 238: "cp1250"}


def _codec(name: str) -> bool:
    try:
        "".encode(name)
        return True
    except LookupError:
        return False


def rtf_document(path: str) -> Document:
    with open(path, "rb") as f:
        data = f.read(MAX_RTF + 1)
    if len(data) > MAX_RTF:
        raise DocumentError("The document is too big to show")
    ansi = "cp1252"
    m = re.search(rb"\\ansicpg(\d+)", data[:4096])
    if m and _codec("cp" + m.group(1).decode()):
        ansi = "cp" + m.group(1).decode()
    fonts = {}                                             # font number -> code page, from each font's \fcharset
    for fm in _RTF_FONT.finditer(data[:256 * 1024]):
        fonts[fm.group(1)] = _CHARSETS.get(int(fm.group(2)), ansi) if int(fm.group(2)) else ansi
    m = re.search(rb"\\deff(\d+)", data[:4096])
    font = m.group(1) if m else b"0"
    paragraphs: list = []
    current: list = []
    stack: list = []
    skip, bold, italic, uc = False, False, False, 1
    pending_skip = 0                                       # fallback characters still to drop after a \uN
    first_in_group = False
    hexbuf = bytearray()                                   # \'hh bytes in a row, decoded together (a double-byte code page)

    def flush_hex() -> None:
        if hexbuf:
            data_ = bytes(hexbuf)
            hexbuf.clear()
            emit(data_.decode(fonts.get(font, ansi), "replace"))

    def emit(text: str) -> None:
        nonlocal pending_skip
        if skip or not text:
            return
        if pending_skip:
            drop = min(pending_skip, len(text))
            text, pending_skip = text[drop:], pending_skip - drop
            if not text:
                return
        current.append((text, bold, italic))

    def new_paragraph() -> None:
        nonlocal current
        if not skip:
            paragraphs.append(current)
            current = []

    for tok in _RTF_TOKEN.finditer(data):
        word, num, hexbyte, symbol, brace, text = tok.groups()
        if hexbyte is not None:
            if pending_skip:
                pending_skip -= 1                          # a \uN's fallback byte
            elif not skip:
                hexbuf.append(int(hexbyte, 16))
            first_in_group = False
            continue
        flush_hex()
        if brace == b"{":
            stack.append((skip, bold, italic, uc, font))
            first_in_group = True
            continue
        if brace == b"}":
            if stack:
                skip, bold, italic, uc, font = stack.pop()
            first_in_group = False
            continue
        starting = first_in_group
        first_in_group = False
        if word is not None:
            if word in _RTF_SKIP and (starting or word in (b"fldinst",)):
                skip = True
            elif word in (b"par", b"sect", b"page", b"row"):
                new_paragraph()
            elif word == b"line":
                emit("\n")
            elif word in (b"tab", b"cell"):
                emit("\t")
            elif word == b"b":
                bold = num != b"0"
            elif word == b"i":
                italic = num != b"0"
            elif word == b"plain":
                bold = italic = False
            elif word == b"f" and num is not None and not skip:
                font = num
            elif word == b"uc" and num is not None:
                uc = int(num)
            elif word == b"u" and num is not None:
                n = int(num)
                emit(chr(n + 65536 if n < 0 else n))
                pending_skip = uc if not skip else 0
            elif word in _RTF_SYMBOLS:
                emit(_RTF_SYMBOLS[word])
            elif word in (b"bin",) and num is not None:
                pass                                       # binary data follows; rare outside pictures, which are skipped
        elif symbol is not None:
            if symbol == b"*" and starting:
                skip = True
            elif symbol in (b"\\", b"{", b"}"):
                emit(symbol.decode())
            elif symbol == b"~":
                emit("\u00a0")
            elif symbol == b"_":
                emit("-")
            elif symbol in (b"\n", b"\r"):
                new_paragraph()
        elif text is not None:
            emit(text.decode(fonts.get(font, ansi), "replace"))
    flush_hex()
    paragraphs.append(current)
    md, plain = [], []
    for segs in paragraphs:
        text_md, text_plain = render_segments(segs)
        if text_plain:
            md.append(block(text_md))
            plain.append(text_plain)
    return Document("RTF document", "\n\n".join(plain), join_markdown(md))


# PowerPoint ------------------------------------------------------------------------------------------------------------------------
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _rels(z: zipfile.ZipFile, part: str) -> dict:
    """Relationship id -> (type, target part) for ``part``."""
    folder, name = posixpath.split(part)
    root = _xml(z, posixpath.join(folder, "_rels", name + ".rels"))
    out = {}
    if root is not None:
        for r in root.iter(PKG + "Relationship"):
            target = r.get("Target", "")
            path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
            out[r.get("Id", "")] = (r.get("Type", "").rsplit("/", 1)[-1], path)
    return out


def _a_paragraph(p: ET.Element) -> tuple:
    segments = []
    for el in p:
        if el.tag in (A + "r", A + "fld"):
            rpr = el.find(A + "rPr")
            bold = rpr is not None and rpr.get("b", "0") in ("1", "true")
            italic = rpr is not None and rpr.get("i", "0") in ("1", "true")
            t = el.find(A + "t")
            if t is not None and t.text:
                segments.append((t.text, bold, italic))
        elif el.tag == A + "br":
            segments.append(("\n", False, False))
    return render_segments(segments)


def _slide_blocks(root: ET.Element, md: list, plain: list, bullets: bool = True) -> str:
    """The text of a slide's shapes in document order; returns the title (empty when the slide has none)."""
    title = ""
    for sp in root.iter():
        if sp.tag == P + "sp":
            ph = sp.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
            kind = ph.get("type", "body") if ph is not None else ""     # a placeholder without a type is the body
            body = sp.find(P + "txBody")
            if body is None:
                continue
            for p in body.findall(A + "p"):
                text_md, text_plain = _a_paragraph(p)
                if not text_plain:
                    continue
                if kind in ("title", "ctrTitle") and not title:
                    title = text_plain.replace("\n", " ")
                    continue
                md.append(block(text_md, listed=bullets and kind in ("body", "obj")))     # a text box is a paragraph
                plain.append(text_plain)
        elif sp.tag == A + "tbl":
            rows_md, rows_plain = [], []
            for tr in sp.findall(A + "tr"):
                cells = [[_a_paragraph(p) for p in tc.iter(A + "p")] for tc in tr.findall(A + "tc")]
                rows_md.append([" ".join(m for m, _ in c if m) for c in cells])
                rows_plain.append([" ".join(t for _, t in c if t) for c in cells])
            if rows_md:
                md.append(md_table(rows_md))
                plain.append("\n".join("\t".join(r) for r in rows_plain))
    return title


def pptx_document(path: str) -> Document:
    md: list = []
    plain: list = []
    try:
        with zipfile.ZipFile(path) as z:
            pres = _xml(z, "ppt/presentation.xml")
            if pres is None:
                raise DocumentError("This zip file is not a presentation")
            rels = _rels(z, "ppt/presentation.xml")
            ids = pres.find(P + "sldIdLst")
            slides = [rels[s.get(R + "id")][1] for s in (ids if ids is not None else []) if s.get(R + "id") in rels]
            for n, part in enumerate(slides, 1):
                slide = _xml(z, part)
                if slide is None:
                    continue
                body_md: list = []
                body_plain: list = []
                title = _slide_blocks(slide, body_md, body_plain)
                hidden = " (hidden)" if slide.get("show", "1") in ("0", "false") else ""
                heading = f"Slide {n}{hidden}" + (f": {title}" if title else "")
                md.append(block(render_segments([(heading, False, False)])[0], 2))
                plain.append(heading)
                md.extend(body_md)
                plain.extend(body_plain)
                notes = next((p for kind, p in _rels(z, part).values() if kind == "notesSlide"), None)
                note_root = _xml(z, notes) if notes else None
                if note_root is not None:
                    note_md: list = []
                    note_plain: list = []
                    for sp in note_root.iter(P + "sp"):
                        ph = sp.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
                        if ph is not None and ph.get("type") == "body" and sp.find(P + "txBody") is not None:
                            for p in sp.find(P + "txBody").findall(A + "p"):
                                m, t = _a_paragraph(p)
                                if t:
                                    note_md.append(m)
                                    note_plain.append(t)
                    if note_md:
                        md.append(block("*Notes:* " + note_md[0], quoted=True))
                        md.extend(block(m, quoted=True) for m in note_md[1:])
                        plain.append("Notes: " + "\n".join(note_plain))
    except zipfile.BadZipFile as e:
        raise DocumentError("The presentation is damaged") from e
    return Document("PowerPoint presentation", "\n\n".join(plain), join_markdown(md))



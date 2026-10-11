"""Reading a file as text for the viewer: how much to read, what encoding it is, and whether it looks binary.

The result is always shown HTML-escaped inside a <pre>, never interpreted, so any file (HTML, SVG, scripts, ...) can
be looked at safely.
"""
from __future__ import annotations

import codecs

_BOMS = [(codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
         (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16")]
SAMPLE = 8192


def read_head(path: str, limit: int):
    """(bytes, truncated): at most `limit` bytes of the file, and whether there was more."""
    with open(path, "rb") as f:
        data = f.read(limit + 1)
    return (data[:limit], True) if len(data) > limit else (data, False)


def looks_binary(raw: bytes) -> bool:
    """True when the start of the file is probably not text: NUL bytes (outside UTF-16/32) or many control bytes."""
    sample = raw[:SAMPLE]
    if not sample or any(sample.startswith(bom) for bom, _ in _BOMS):
        return False
    if b"\x00" in sample:
        return True
    odd = sum(1 for b in sample if b < 32 and b not in (9, 10, 12, 13, 27))
    return odd / len(sample) > 0.10


def decode(raw: bytes, truncated: bool = False, force: bool = False):
    """(text, encoding name). Tries the BOM, then UTF-8, then Windows-1252, then Latin-1 (which cannot fail).
    force=True (the 'show as text anyway' button) never raises and marks undecodable bytes with U+FFFD."""
    for bom, name in _BOMS:
        if raw.startswith(bom):
            body = raw[len(bom):] if name == "utf-8-sig" else raw
            cut = len(body) - (len(body) % (4 if "32" in name else 2)) if name != "utf-8-sig" and truncated else len(body)
            try:
                return body[:cut].decode(name, errors="replace" if (force or truncated) else "strict"), name
            except UnicodeDecodeError:
                break
    data = raw
    if truncated:                                   # do not fail because the cut landed inside a multi-byte character
        for trim in range(4):
            try:
                return (data[:len(data) - trim] if trim else data).decode("utf-8"), "utf-8"
            except UnicodeDecodeError:
                continue
    else:
        try:
            return data.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            pass
    if force:
        return data.decode("utf-8", errors="replace"), "utf-8 (damaged bytes shown as �)"
    try:
        return data.decode("cp1252"), "windows-1252"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"

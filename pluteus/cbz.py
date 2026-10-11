"""Reading comic archives (CBZ): which pages are inside, and the bytes of one page.

Only the zip's central directory is read to list pages, and a single entry is read to serve one page: nothing
is ever extracted to disk. Everything is bounded, because archives are untrusted input.
"""
from __future__ import annotations

import zipfile

from .fs import natural_key

IMAGE_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".gif": "image/gif", ".avif": "image/avif", ".bmp": "image/bmp",
}
MAX_PAGES = 5000
MAX_PAGE_BYTES = 80 * 1024 * 1024
_SKIP_NAMES = {"thumbs.db", "desktop.ini"}
_ERRORS = (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, ValueError, KeyError)


class CbzError(Exception):
    """The file is not a readable comic archive, or the requested page is unusable."""


def _extension(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot >= 0 else ""


def _is_page(info: zipfile.ZipInfo) -> bool:
    if info.is_dir() or info.flag_bits & 0x1:                      # folders and encrypted entries are not pages
        return False
    parts = info.filename.replace("\\", "/").split("/")
    if any(p.startswith(".") or p.lower().startswith("__macosx") for p in parts):
        return False
    return parts[-1].lower() not in _SKIP_NAMES and _extension(parts[-1]) in IMAGE_TYPES


def list_pages(path: str) -> list:
    """Names of the image entries in reading order (natural sort: page 2 before page 10)."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = [i.filename for i in zf.infolist() if _is_page(i)]
    except _ERRORS as e:
        raise CbzError("not a readable CBZ: %s" % e)
    if not names:
        raise CbzError("no images in this archive")
    if len(names) > MAX_PAGES:
        raise CbzError("more than %d pages" % MAX_PAGES)
    return sorted(names, key=lambda n: natural_key(n.replace("\\", "/")))


def read_page(path: str, name: str, limit: int = MAX_PAGE_BYTES) -> bytes:
    """The bytes of one page. Reads at most `limit` bytes, whatever the zip header claims."""
    try:
        with zipfile.ZipFile(path) as zf:
            info = zf.getinfo(name)
            if info.file_size > limit:
                raise CbzError("page is larger than %d MB" % (limit // (1024 * 1024)))
            with zf.open(info) as f:
                data = f.read(limit + 1)
    except CbzError:
        raise
    except _ERRORS as e:
        raise CbzError("cannot read the page: %s" % e)
    if len(data) > limit:
        raise CbzError("page is larger than %d MB" % (limit // (1024 * 1024)))
    return data


def mime_for(name: str) -> str:
    return IMAGE_TYPES.get(_extension(name), "application/octet-stream")

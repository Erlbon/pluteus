"""Cover thumbnails for the OPDS feeds: the cover inside an EPUB, scaled down to a small JPEG (Pillow), cached on disk.

Pillow is imported only when a thumbnail is asked for. Without it the original picture is sent instead (bigger, but it works).
"""
from __future__ import annotations

import hashlib
import io
import os
import posixpath
import xml.etree.ElementTree as ET
import zipfile
from urllib.parse import unquote

from .mediainfo import MAX_COVER, sniff_image
from .meta import MAX_XML_BYTES, NS, _READ_ERRORS, _read_member

# A picture is refused before it is decoded when it has more pixels than this: a small file can hold a huge picture (a flat
# 10000 x 10000 PNG is under 400 KB but needs 400 MB of memory to decode).
MAX_PIXELS = 20_000_000
# A JPEG is decoded at a reduced size (draft mode), so a camera photo is cheap to scale; it may have more pixels than other formats.
MAX_JPEG_PIXELS = 120_000_000
MAX_IMAGE_FILE = 48 * 1024 * 1024               # the biggest picture file read to make a thumbnail of it
# Only these widths are made, so the cache holds a few files per book, not one per possible number.
WIDTHS = (96, 160, 240, 320, 480, 640, 900)
MIN_WIDTH, MAX_WIDTH = WIDTHS[0], WIDTHS[-1]


def epub_cover(path: str):
    """(bytes, mime) of the cover picture of an EPUB, or None. EPUB 3 `cover-image`, else the EPUB 2 `<meta name="cover">`."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = {n.lower(): n for n in zf.namelist()}
            container = ET.fromstring(_read_member(zf, names, "META-INF/container.xml"))
            opf_path = unquote(container.find(".//c:rootfile", NS).get("full-path"))
            opf = ET.fromstring(_read_member(zf, names, opf_path))
            items = {i.get("id"): i for i in opf.findall("opf:manifest/opf:item", NS)}
            found = next((i for i in items.values() if "cover-image" in (i.get("properties") or "").split()), None)
            if found is None:
                meta = next((m for m in opf.findall("opf:metadata/opf:meta", NS) if m.get("name") == "cover"), None)
                found = items.get(meta.get("content")) if meta is not None else None
            if found is None:
                found = next((i for i in items.values() if (i.get("media-type") or "").startswith("image/")
                              and "cover" in (i.get("id", "") + i.get("href", "")).lower()), None)
            if found is None:
                return None
            member = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), unquote(found.get("href"))))
            real = names.get(member.lower())
            if real is None or zf.getinfo(real).file_size > MAX_COVER:
                return None
            data = zf.read(real)
    except _READ_ERRORS:
        return None
    mime = sniff_image(data)
    return (data, mime) if mime else None


def scaled(data: bytes, width: int):
    """(JPEG bytes, 'image/jpeg') no wider than `width` pixels. None when Pillow is not installed (the caller then sends the
    original); False when the picture cannot be read or is too big to decode safely (no cover)."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        Image.MAX_IMAGE_PIXELS = None                              # we check the size ourselves, right below, before anything is decoded
        with Image.open(io.BytesIO(data)) as im:
            limit = MAX_JPEG_PIXELS if im.format == "JPEG" else MAX_PIXELS
            if im.width * im.height > limit:                       # from the header only: nothing has been decoded yet
                return False
            im.draft("RGB", (width * 2, width * 3))                # JPEG: decode at a reduced size when possible
            im = im.convert("RGB")
            if im.width > width:
                im = im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, "JPEG", quality=80, optimize=True)
            return out.getvalue(), "image/jpeg"
    except Exception:                                              # noqa: BLE001  a damaged or absurd picture is just "no cover"
        return False


def image_file(path: str):
    """(bytes, mime) of a loose picture file (JPEG, PNG, WebP or GIF), or None when it is another type or too big."""
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_IMAGE_FILE + 1)
    except OSError:
        return None
    mime = sniff_image(data)
    return (data, mime) if (mime and len(data) <= MAX_IMAGE_FILE) else None


def clamp_width(text: str, default: int = 240) -> int:
    """The width asked for, rounded up to one of WIDTHS (the biggest when it asks for more)."""
    try:
        wanted = int(text)
    except (TypeError, ValueError):
        wanted = default
    return next((w for w in WIDTHS if w >= wanted), MAX_WIDTH)


class ThumbCache:
    """JPEG thumbnails on disk, named by a hash of the file, its size and date and the width. Disposable. directory '' = none."""

    def __init__(self, directory: str = ""):
        self.dir = os.path.join(directory, "thumbs") if directory else ""

    def _file(self, key: str) -> str:
        return os.path.join(self.dir, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".jpg")

    def get(self, key: str):
        if not self.dir:
            return None
        try:
            with open(self._file(key), "rb") as f:
                return f.read()
        except OSError:
            return None

    def put(self, key: str, data: bytes) -> None:
        if not self.dir:
            return
        try:
            os.makedirs(self.dir, exist_ok=True)
            target = self._file(key)
            tmp = target + ".tmp%d" % os.getpid()
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, target)
        except OSError:
            pass

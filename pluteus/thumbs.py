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

MAX_PIXELS = 60_000_000
MIN_WIDTH, MAX_WIDTH = 48, 900


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
    """(JPEG bytes, 'image/jpeg') no wider than `width` pixels, or None when Pillow cannot read the picture."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        with Image.open(io.BytesIO(data)) as im:
            im.draft("RGB", (width * 2, width * 3))                # JPEG: decode at a reduced size when possible
            im = im.convert("RGB")
            if im.width > width:
                im = im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, "JPEG", quality=80, optimize=True)
            return out.getvalue(), "image/jpeg"
    except Exception:                                              # noqa: BLE001  a damaged or absurd picture is just "no cover"
        return None


def clamp_width(text: str, default: int = 200) -> int:
    try:
        return min(MAX_WIDTH, max(MIN_WIDTH, int(text)))
    except (TypeError, ValueError):
        return default


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

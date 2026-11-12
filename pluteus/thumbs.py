"""Cover thumbnails for the OPDS feeds: the cover inside an EPUB, scaled down to a small JPEG (Pillow), cached on disk.

Pillow is imported only when a thumbnail is asked for. Without it the original picture is sent instead (bigger, but it works).
"""
from __future__ import annotations

import hashlib
import io
import os
import posixpath
import secrets
import warnings
from urllib.parse import unquote

from .mediainfo import MAX_COVER, sniff_image
from .meta import NS, read_xml_member
from .ziputil import open_zip

# A picture is refused before it is decoded when it has more pixels than this: a small file can hold a huge picture (a flat
# 10000 x 10000 PNG is under 400 KB but needs 400 MB of memory to decode). The check is ours, made on the size in the file's header;
# Pillow's own process-wide limit (Image.MAX_IMAGE_PIXELS) only warns up to twice its value, and is left alone.
MAX_PIXELS = 20_000_000
# A plain (baseline) JPEG is decoded at a reduced size (draft mode), so a camera photo is cheap to scale; it may have more pixels than
# other formats. A progressive JPEG has to be read whole before it can be reduced, so it gets MAX_PIXELS like everything else.
MAX_JPEG_PIXELS = 120_000_000
# What a JPEG may still have after draft mode chose its reduced size (it cannot reduce a very wide or very tall picture enough).
MAX_DECODED_PIXELS = 40_000_000
MAX_IMAGE_FILE = 48 * 1024 * 1024               # the biggest picture file read to make a thumbnail of it
# Only these widths are made, so the cache holds a few files per book, not one per possible number.
WIDTHS = (96, 160, 240, 320, 480, 640, 900)
MIN_WIDTH, MAX_WIDTH = WIDTHS[0], WIDTHS[-1]
_quiet = False


def pillow():
    """Pillow's Image module, or None when Pillow is not installed. Its "decompression bomb" warning is switched off once: the callers
    check the pixel count themselves, before anything is decoded, against limits of their own."""
    global _quiet
    try:
        from PIL import Image
    except ImportError:
        return None
    if not _quiet:
        warnings.filterwarnings("ignore", category=Image.DecompressionBombWarning)
        _quiet = True
    return Image


def epub_cover(path: str):
    """(bytes, mime) of the cover picture of an EPUB, or None. EPUB 3 `cover-image`, else the EPUB 2 `<meta name="cover">`.
    Never raises: a damaged or hostile book has no cover."""
    try:
        with open_zip(path) as zf:
            names = {n.lower(): n for n in zf.namelist()}
            container = read_xml_member(zf, names, "META-INF/container.xml")
            opf_path = unquote(container.find(".//c:rootfile", NS).get("full-path"))
            opf = read_xml_member(zf, names, opf_path)
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
            with zf.open(real) as f:                               # bounded while it is read, whatever the header claims
                data = f.read(MAX_COVER + 1)
            if len(data) > MAX_COVER:
                return None
    except Exception:                                              # noqa: BLE001  one bad book must never fail a page of covers
        return None
    mime = sniff_image(data)
    return (data, mime) if mime else None


def scaled(data: bytes, width: int):
    """(JPEG bytes, 'image/jpeg') no wider than `width` pixels. None when Pillow is not installed (the caller then sends the
    original); False when the picture cannot be read or is too big to decode safely (no cover)."""
    Image = pillow()
    if Image is None:
        return None
    try:
        with Image.open(io.BytesIO(data)) as im:
            reducible = im.format == "JPEG" and not (im.info.get("progressive") or im.info.get("progression"))
            if im.width * im.height > (MAX_JPEG_PIXELS if reducible else MAX_PIXELS):      # from the header only: nothing has been decoded yet
                return False
            im.draft("RGB", (width * 2, width * 3))                # JPEG: decode at a reduced size when possible
            if im.width * im.height > MAX_DECODED_PIXELS:          # still the header: the size it will really be decoded at
                return False
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
                return f.read() or None                            # an empty file (a write that was cut short) is a miss, not a picture
        except OSError:
            return None

    def put(self, key: str, data: bytes) -> None:
        if not self.dir or not data:
            return
        tmp = ""
        try:
            os.makedirs(self.dir, exist_ok=True)
            target = self._file(key)
            tmp = "%s.tmp%s" % (target, secrets.token_hex(8))      # a name of its own: two threads making the same thumbnail never share one
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, target)
        except OSError:
            try:
                if tmp:
                    os.remove(tmp)
            except OSError:
                pass

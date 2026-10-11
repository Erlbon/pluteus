"""Builds small but real CBZ files for the comic reader tests."""
import io
import os
import zipfile

from PIL import Image

from pluteus.fs import longpath


def png(color, size=(12, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def make_cbz(path, entries):
    """entries: [(name, bytes)] written in the order given (names may include folders)."""
    path = longpath(str(path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return path

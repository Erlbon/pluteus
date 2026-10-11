"""Looking inside a zip file without extracting anything to disk.

Safety rules: an entry is only ever picked by its NUMBER in the archive (no file names are used as paths, so there is no
path to escape); everything served is bounded while it is read, whatever the archive's headers claim (zip bombs); encrypted
entries are not touched; an entry's name is only ever shown escaped and never used to name a file on the server.
"""
from __future__ import annotations

import time
import zipfile

MAX_SHOWN = 2000                      # entries listed on the page
MAX_TEXT_BYTES = 2 * 1024 * 1024      # a text entry opened in the viewer
MAX_IMAGE_BYTES = 30 * 1024 * 1024    # an image entry shown inline
MAX_DOWNLOAD_BYTES = 2 * 1024 ** 3    # one entry downloaded
BOMB_RATIO = 200                      # an entry that expands more than this is not opened in the viewer
CHUNK = 64 * 1024
_ERRORS = (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, ValueError, EOFError)


class ZipError(Exception):
    """The archive or the entry cannot be used; the message is safe to show to the user."""


def display_name(info: zipfile.ZipInfo) -> str:
    """The entry's name as people wrote it. Zips without the UTF-8 flag are decoded as old DOS text by the library; many
    tools put UTF-8 there anyway, so try that before giving up."""
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name


def _stamp(info: zipfile.ZipInfo) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.struct_time(info.date_time + (0, 0, -1)))
    except (ValueError, OverflowError):
        return ""


def list_entries(path: str):
    """(entries, total, comment): the first MAX_SHOWN entries as dicts, the real number of entries, the archive comment."""
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            total = len(infos)
            entries = []
            for index, info in enumerate(infos[:MAX_SHOWN]):
                entries.append({"index": index, "name": display_name(info), "is_dir": info.is_dir(),
                                "size": info.file_size, "packed": info.compress_size, "date": _stamp(info),
                                "encrypted": bool(info.flag_bits & 0x1)})
            return entries, total, zf.comment.decode("utf-8", "replace")[:500]
    except _ERRORS as e:
        raise ZipError("this does not look like a readable zip file (%s)" % e)


def _entry(zf: zipfile.ZipFile, index: int) -> zipfile.ZipInfo:
    infos = zf.infolist()
    if not 0 <= index < len(infos):
        raise ZipError("there is no such entry")
    info = infos[index]
    if info.is_dir():
        raise ZipError("that is a folder")
    if info.flag_bits & 0x1:
        raise ZipError("that entry is password protected")
    return info


def entry_facts(path: str, index: int) -> dict:
    """Name and sizes of one entry (checked: not a folder, not encrypted)."""
    try:
        with zipfile.ZipFile(path) as zf:
            info = _entry(zf, index)
            return {"name": display_name(info), "size": info.file_size, "packed": info.compress_size}
    except ZipError:
        raise
    except _ERRORS as e:
        raise ZipError("cannot read the archive (%s)" % e)


def read_entry(path: str, index: int, limit: int) -> bytes:
    """The entry's bytes, at most `limit`. Refuses an entry that claims more, an extreme compression ratio, and one that
    turns out larger than it claimed (the count is made on the bytes actually produced)."""
    try:
        with zipfile.ZipFile(path) as zf:
            info = _entry(zf, index)
            if info.file_size > limit:
                raise ZipError("that entry is too large to open here (download it instead)")
            if info.file_size > 1024 * 1024 and info.file_size > BOMB_RATIO * max(info.compress_size, 1):
                raise ZipError("that entry expands suspiciously much, so it is not opened here (download it instead)")
            with zf.open(info) as f:
                data = f.read(limit + 1)
    except ZipError:
        raise
    except _ERRORS as e:
        raise ZipError("cannot read that entry (%s)" % e)
    if len(data) > limit:
        raise ZipError("that entry is larger than it claims, so it is not opened")
    return data


def read_head(path: str, index: int, limit: int):
    """(bytes, truncated): the first `limit` bytes of an entry, however large it is. Only that much is ever decompressed,
    so a bomb costs one limit's worth of work, not its full size."""
    try:
        with zipfile.ZipFile(path) as zf:
            info = _entry(zf, index)
            with zf.open(info) as f:
                data = f.read(limit + 1)
    except ZipError:
        raise
    except _ERRORS as e:
        raise ZipError("cannot read that entry (%s)" % e)
    return (data[:limit], True) if len(data) > limit else (data, False)


def stream_entry(path: str, index: int, limit: int = MAX_DOWNLOAD_BYTES):
    """Chunks of one entry for a download, with the same byte limit applied to what is really produced."""
    zf = zipfile.ZipFile(path)
    try:
        info = _entry(zf, index)
        if info.file_size > limit:
            raise ZipError("that entry is too large to download through the page")
        sent = 0
        with zf.open(info) as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    return
                sent += len(chunk)
                if sent > limit:
                    raise ZipError("the entry is larger than it claims")
                yield chunk
    finally:
        zf.close()

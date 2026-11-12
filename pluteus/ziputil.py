"""Looking inside a zip file without extracting anything to disk.

Safety rules: an entry is only ever picked by its NUMBER in the archive (no file names are used as paths, so there is no
path to escape); everything served is bounded while it is read, whatever the archive's headers claim (zip bombs); encrypted
entries are not touched; an entry's name is only ever shown escaped and never used to name a file on the server.
"""
from __future__ import annotations

import lzma
import struct
import time
import zipfile
import zlib

MAX_SHOWN = 2000                      # entries listed on the page
MAX_TEXT_BYTES = 2 * 1024 * 1024      # a text entry opened in the viewer
MAX_IMAGE_BYTES = 30 * 1024 * 1024    # an image entry shown inline
MAX_DOWNLOAD_BYTES = 2 * 1024 ** 3    # one entry downloaded
BOMB_RATIO = 200                      # an entry that expands more than this is not opened in the viewer
CHUNK = 64 * 1024
MAX_ENTRIES = 100_000                 # an archive with more entries than this is not opened at all
MAX_DIRECTORY_BYTES = 32 * 1024 * 1024    # nor one whose file list (the central directory) is larger than this
_ERRORS = (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, ValueError, EOFError,
           zlib.error, lzma.LZMAError)


class ZipError(Exception):
    """The archive or the entry cannot be used; the message is safe to show to the user."""


# ---- the size of the file list, read from the end of the file before Python's zipfile loads all of it -------------------------
# zipfile reads the whole central directory into memory and makes one object per entry the moment an archive is opened (300,000
# empty entries: a second of work and 170 MB), so the caps have to be checked first. The records are found the way zipfile finds
# them, so both always look at the same numbers.
_END = struct.Struct("<4s4H2LH")              # end of central directory: ..., entries, directory size, directory offset, comment length
_LOCATOR = struct.Struct("<4sLQL")            # zip64 locator: ..., where the zip64 end record is, ...
_END64 = struct.Struct("<4sQ2H2L4Q")          # zip64 end record: ..., entries, directory size, directory offset
_ENTRY_HEAD = 46                              # the fixed part of one entry in the directory


def check_directory(f) -> None:
    """Refuse (zipfile.BadZipFile) an archive, open as the binary file `f`, whose file list is too long or too large. Reads a few
    hundred bytes at the end of the file; the list itself only when it is big enough to hold more entries than it declares."""
    f.seek(0, 2)
    size = f.tell()
    f.seek(max(0, size - _END.size - 65535))
    tail = f.read()
    if len(tail) >= _END.size and tail[-_END.size:-_END.size + 4] == b"PK\x05\x06" and tail[-2:] == b"\x00\x00":
        at = len(tail) - _END.size                                  # the usual case: no archive comment
    else:
        at = tail.rfind(b"PK\x05\x06")
    if at < 0 or len(tail) - at < _END.size:
        raise zipfile.BadZipFile("File is not a zip file")
    entries, directory = _END.unpack_from(tail, at)[4:6]
    where = size - len(tail) + at                                   # the directory ends where this record starts
    locator_at = where - _LOCATOR.size
    if locator_at >= 0:
        f.seek(locator_at)
        locator = f.read(_LOCATOR.size)
        if len(locator) == _LOCATOR.size and locator[:4] == b"PK\x06\x07":
            stated, fixed = _LOCATOR.unpack(locator)[2], locator_at - _END64.size
            found = []
            for place in dict.fromkeys((stated, fixed)):            # where the locator says, and right in front of it (older Pythons)
                if 0 <= place <= fixed:
                    f.seek(place)
                    record = f.read(_END64.size)
                    if len(record) == _END64.size and record[:4] == b"PK\x06\x06":
                        found.append((place,) + _END64.unpack(record)[7:9])
            if not found:
                raise zipfile.BadZipFile("Zip64 end of central directory record not found")
            where = found[0][0]                                     # zipfile's own order of preference
            entries, directory = max(r[1] for r in found), max(r[2] for r in found)
    if entries > MAX_ENTRIES:
        raise zipfile.BadZipFile("the archive has more than %d entries" % MAX_ENTRIES)
    if directory > MAX_DIRECTORY_BYTES:
        raise zipfile.BadZipFile("the archive's file list is larger than %d MB" % (MAX_DIRECTORY_BYTES // (1024 * 1024)))
    if directory > MAX_ENTRIES * _ENTRY_HEAD:                       # room for more entries than declared: count the real ones
        start = where - directory
        if start < 0:
            raise zipfile.BadZipFile("Bad offset for central directory")
        f.seek(start)
        data = f.read(directory)
        pos = count = 0
        while pos + _ENTRY_HEAD <= len(data) and data[pos:pos + 4] == b"PK\x01\x02":
            count += 1
            if count > MAX_ENTRIES:
                raise zipfile.BadZipFile("the archive has more than %d entries" % MAX_ENTRIES)
            name, extra, comment = struct.unpack_from("<3H", data, pos + 28)
            pos += _ENTRY_HEAD + name + extra + comment


class _CheckedZip(zipfile.ZipFile):
    _raw = None                                # set here: close() also runs (from __del__) when the file could not be opened

    def __init__(self, path: str):
        self._raw = open(path, "rb")
        try:
            check_directory(self._raw)
            self._raw.seek(0)
            super().__init__(self._raw)
        except BaseException:
            self._raw.close()
            raise

    def close(self) -> None:
        try:
            super().close()
        finally:
            if self._raw is not None:
                self._raw.close()


def open_zip(path: str) -> zipfile.ZipFile:
    """The archive opened for reading, after check_directory. Every reader of zip files (the zip viewer, comics, EPUB metadata and
    covers, the description inside a book) opens them here. Raises what zipfile raises (OSError, zipfile.BadZipFile ...)."""
    return _CheckedZip(path)


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
        with open_zip(path) as zf:
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
        with open_zip(path) as zf:
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
        with open_zip(path) as zf:
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
        with open_zip(path) as zf:
            info = _entry(zf, index)
            with zf.open(info) as f:
                data = f.read(limit + 1)
    except ZipError:
        raise
    except _ERRORS as e:
        raise ZipError("cannot read that entry (%s)" % e)
    return (data[:limit], True) if len(data) > limit else (data, False)


def stream_entry(path: str, index: int, limit: int = MAX_DOWNLOAD_BYTES):
    """Chunks of one entry for a download, with the same byte limit applied to what is really produced. Raises ZipError, also for
    an archive that cannot be opened and for an entry that turns out damaged half way."""
    try:
        with open_zip(path) as zf:
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
    except ZipError:
        raise
    except _ERRORS as e:
        raise ZipError("cannot read that entry (%s)" % e)

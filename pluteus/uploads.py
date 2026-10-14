"""Personal upload folders: safe file names, streaming multipart intake, new folder / rename / delete.

Design: the upload goes straight from the network to a hidden temp file in the user's own folder (never into
memory, never through the system temp folder), then takes its final name. Names are sanitised for Windows, a clash
gets " (1)", " (2)"... and nothing is ever overwritten. Limits (file size, quota, free disk) are enforced while the
bytes arrive, so a refused file costs no disk. A refused file does not stop the other files in the same request.
"""
from __future__ import annotations

import errno
import os
import re
import secrets
import shutil
import time
import unicodedata

from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.concurrency import run_in_threadpool

from .fs import longpath

MAX_NAME = 150
MAX_FILES = 100
FREE_RESERVE = 512 * 1024 * 1024          # keep this much of the disk free for everything else
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_DEVICE_NAMES = {"con", "prn", "aux", "nul", *("com%d" % i for i in range(1, 10)), *("lpt%d" % i for i in range(1, 10))}
_O_BINARY = getattr(os, "O_BINARY", 0)


class UploadError(Exception):
    """A refused operation. `code` is one of a fixed set (the page shows the matching sentence), never free text."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def sanitize_name(raw: str, is_hidden=lambda n: False) -> str | None:
    """A file or folder name that is safe on Windows and on disk, or None when nothing usable is left.

    Folders are stripped from the name (browsers may send a path), characters Windows forbids become _, trailing dots
    and spaces go (Windows would silently drop them), leading dots go (those names are hidden by pluteus), device
    names such as CON or NUL.txt get a _ in front, and the length is capped with the extension kept."""
    name = unicodedata.normalize("NFC", raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _ILLEGAL.sub("_", name).strip().rstrip(". ").lstrip(".").strip()
    if not name:
        return None
    if name.split(".")[0].strip().lower() in _DEVICE_NAMES or is_hidden(name):
        name = "_" + name
    if len(name) > MAX_NAME:
        ext = os.path.splitext(name)[1][:20]
        name = name[:MAX_NAME - len(ext)].rstrip(". ") + ext
    return name


def tree_size(path: str) -> int:
    """Bytes in a folder tree (a user's whole upload folder: small, so a plain walk is fine)."""
    total, stack = 0, [longpath(path)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _claim(dirpath: str, name: str) -> str:
    """Create an empty file under the first free variant of `name` and return its path (race-free: O_EXCL)."""
    stem, ext = os.path.splitext(name)
    for n in range(0, 500):
        candidate = name if n == 0 else "%s (%d)%s" % (stem, n, ext)
        path = os.path.join(longpath(dirpath), candidate)
        try:
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, 0o644))
            return path
        except FileExistsError:
            continue
    raise UploadError("exists")


def _replace(src: str, dst: str) -> None:
    """os.replace, tried a few times on Windows: a virus scanner or the indexer may hold the new file open for a moment
    (WinError 5 access denied / 32 sharing violation)."""
    for attempt in range(4):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            if getattr(e, "winerror", None) not in (5, 32) or attempt == 3:
                raise
            time.sleep(0.05 * (attempt + 1))


class Intake:
    """Receives the parts of one multipart/form-data upload and writes each file part to the user's folder."""

    def __init__(self, dirpath: str, *, max_bytes: int, quota_left: int | None, is_hidden=lambda n: False):
        self.dir = dirpath
        self.max_bytes = max_bytes
        self.quota_left = quota_left              # bytes the user may still add; None = unlimited
        self.is_hidden = is_hidden
        self.saved: list = []                     # [(final name, size)]
        self.failed: list = []                    # [(name as sent, code)]
        self.added = 0                            # bytes kept so far in this request
        self._hf = self._hv = b""
        self._headers: dict = {}
        self._cur = None                          # the file part being written
        self._files = 0
        self._free_checked = 0

    # ---- parser callbacks (they run on a worker thread, one chunk at a time) ---------------------------------
    def callbacks(self) -> dict:
        return {"on_part_begin": self._part_begin, "on_header_field": self._header_field,
                "on_header_value": self._header_value, "on_header_end": self._header_end,
                "on_headers_finished": self._headers_finished, "on_part_data": self._part_data,
                "on_part_end": self._part_end}

    def _part_begin(self) -> None:
        self._headers, self._hf, self._hv, self._cur = {}, b"", b"", None

    def _header_field(self, data, start, end) -> None:
        self._hf += data[start:end]

    def _header_value(self, data, start, end) -> None:
        self._hv += data[start:end]

    def _header_end(self) -> None:
        self._headers[self._hf.lower()] = self._hv
        self._hf = self._hv = b""

    def _headers_finished(self) -> None:
        _, params = parse_options_header(self._headers.get(b"content-disposition", b""))
        raw = params.get(b"filename")
        if raw is None or raw == b"":             # a plain form field, or "no file chosen": nothing to store
            return
        label = raw.decode("utf-8", "replace")
        cur = {"label": label, "size": 0, "fd": None, "tmp": None, "bad": None,
               "name": sanitize_name(label, self.is_hidden)}
        self._files += 1
        if self._files > MAX_FILES:
            cur["bad"] = "toomany"
        elif cur["name"] is None:
            cur["bad"] = "name"
        else:
            tmp = os.path.join(longpath(self.dir), ".up-%s.part" % secrets.token_hex(8))
            cur["tmp"] = tmp
            cur["fd"] = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, 0o644)
        self._cur = cur

    def _fail(self, cur: dict, code: str) -> None:
        cur["bad"] = code
        self._discard(cur)

    @staticmethod
    def _discard(cur: dict) -> None:
        if cur.get("fd") is not None:
            os.close(cur["fd"])
            cur["fd"] = None
        if cur.get("tmp"):
            try:
                os.remove(cur["tmp"])
            except OSError:
                pass
            cur["tmp"] = None

    def _part_data(self, data, start, end) -> None:
        cur = self._cur
        if cur is None or cur["bad"]:
            return
        n = end - start
        cur["size"] += n
        if cur["size"] > self.max_bytes:
            return self._fail(cur, "toobig")
        if self.quota_left is not None and self.added + cur["size"] > self.quota_left:
            return self._fail(cur, "quota")
        pending = self.added + cur["size"]
        if pending - self._free_checked >= 8 * 1024 * 1024 or self._free_checked == 0:
            self._free_checked = pending
            if shutil.disk_usage(longpath(self.dir)).free - FREE_RESERVE < pending:
                return self._fail(cur, "disk")
        view = memoryview(data)[start:end]
        while view:                                  # os.write may take only part of a big buffer
            view = view[os.write(cur["fd"], view):]

    def _part_end(self) -> None:
        cur, self._cur = self._cur, None
        if cur is None:
            return
        if cur["bad"]:
            self.failed.append((cur["label"], cur["bad"]))
            return
        os.close(cur["fd"])
        cur["fd"] = None
        final = None
        try:
            final = _claim(self.dir, cur["name"])
            _replace(cur["tmp"], final)              # takes over the claimed name atomically
        except (OSError, UploadError):
            self._discard(cur)
            if final:                                # the name was claimed with an empty file: do not leave it behind
                try:
                    os.remove(final)
                except OSError:
                    pass
            self.failed.append((cur["label"], "write"))
            return
        cur["tmp"] = None
        self.added += cur["size"]
        self.saved.append((os.path.basename(final), cur["size"]))

    def abort(self) -> None:
        """The request died half way: remove the temp file being written."""
        if self._cur:
            self._discard(self._cur)
            self._cur = None


async def receive(stream, content_type: str, dirpath: str, *, max_bytes: int, quota_left: int | None,
                  is_hidden=lambda n: False) -> Intake:
    """Read a multipart/form-data body from an async byte stream into dirpath. Raises UploadError('format') when the
    request is not multipart; anything the parser rejects as malformed propagates (the caller answers 400)."""
    kind, opts = parse_options_header(content_type.encode("latin-1", "replace"))
    boundary = opts.get(b"boundary")
    if kind != b"multipart/form-data" or not boundary:
        raise UploadError("format")
    intake = Intake(dirpath, max_bytes=max_bytes, quota_left=quota_left, is_hidden=is_hidden)
    parser = MultipartParser(boundary, intake.callbacks())
    try:
        async for chunk in stream:
            if chunk:
                await run_in_threadpool(parser.write, chunk)
        await run_in_threadpool(parser.finalize)
    except BaseException:
        intake.abort()
        raise
    return intake


# ---- new folder, rename, delete (synchronous: call them through run_in_threadpool) -------------------------------------
def write_atomic(folder: str, file_name: str, raw: bytes) -> None:
    """Write `raw` as folder/file_name through a hidden temporary file, so a reader never sees half of it. Raises UploadError('write')."""
    temp = os.path.join(folder, ".%s.tmp" % file_name)
    path = os.path.join(folder, file_name)
    try:
        with open(longpath(temp), "wb") as f:
            f.write(raw)
        _replace(longpath(temp), longpath(path))
    except OSError:
        try:
            os.remove(longpath(temp))
        except OSError:
            pass
        raise UploadError("write")


def make_folder(dirpath: str, raw_name: str, is_hidden=lambda n: False) -> str:
    name = sanitize_name(raw_name, is_hidden)
    if name is None:
        raise UploadError("name")
    try:
        os.mkdir(os.path.join(longpath(dirpath), name))
    except FileExistsError:
        raise UploadError("exists")
    return name


def rename_entry(dirpath: str, old: str, raw_new: str, is_hidden=lambda n: False) -> str:
    new = sanitize_name(raw_new, is_hidden)
    if new is None:
        raise UploadError("name")
    src, dst = os.path.join(longpath(dirpath), old), os.path.join(longpath(dirpath), new)
    if not os.path.lexists(src):
        raise UploadError("missing")
    same_entry = os.path.normcase(old) == os.path.normcase(new)          # only the letter case changes
    if os.path.lexists(dst) and not same_entry:
        raise UploadError("exists")
    os.rename(src, dst)                                                  # Windows refuses to overwrite; so do we
    return new


def delete_entry(dirpath: str, name: str) -> None:
    """Delete a file, or a folder that is empty (a full folder is never removed in one click)."""
    path = os.path.join(longpath(dirpath), name)
    if not os.path.lexists(path):
        raise UploadError("missing")
    if os.path.isdir(path) and not os.path.islink(path):
        try:
            os.rmdir(path)
        except OSError as err:
            if err.errno in (errno.ENOTEMPTY, errno.EEXIST) or getattr(err, "winerror", 0) == 145:
                raise UploadError("notempty")
            raise
    else:
        os.remove(path)

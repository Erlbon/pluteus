"""Reading comic archives (CBZ, CBR, CB7, CBT): which pages are inside, and the bytes of one page.

The kind of archive is decided by what the file really contains, not by its extension (a great many ".cbr" files are
renamed ZIPs). ZIP and TAR are read with Python's own libraries. RAR and 7z need the `bsdtar` tool (libarchive; in Docker
the package `libarchive-tools`, on Windows 10 and later it is the system `tar.exe`): pluteus runs it with a time limit and an
output limit, reading the page to its standard output, never extracting anything to disk. Only the central directory (or
header list) is read to list pages, and one entry to serve a page. Everything is bounded, because archives are untrusted
input; the files in the upload folder are only ever read when they are ZIPs (see `external`).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import zipfile

from .fs import natural_key

IMAGE_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".gif": "image/gif", ".avif": "image/avif", ".bmp": "image/bmp",
}
COMIC_EXTENSIONS = (".cbz", ".cbr", ".cb7", ".cbt")
MAX_PAGES = 5000
MAX_PAGE_BYTES = 80 * 1024 * 1024
MAX_LIST_BYTES = 8 * 1024 * 1024
TOOL_SECONDS = 30
_SKIP_NAMES = {"thumbs.db", "desktop.ini"}
_ERRORS = (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, ValueError, KeyError,
           tarfile.TarError, EOFError)


class CbzError(Exception):
    """The file is not a readable comic archive, or the requested page is unusable."""


def _extension(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot >= 0 else ""


def _page_name_ok(name: str) -> bool:
    parts = name.replace("\\", "/").split("/")
    if any(not p or p.startswith(".") or p.lower().startswith("__macosx") for p in parts):
        return False
    return parts[-1].lower() not in _SKIP_NAMES and _extension(parts[-1]) in IMAGE_TYPES


def _is_page(info: zipfile.ZipInfo) -> bool:
    if info.is_dir() or info.flag_bits & 0x1:                      # folders and encrypted entries are not pages
        return False
    return _page_name_ok(info.filename)


def sniff(path: str) -> str:
    """'zip' | 'rar' | 'sevenzip' | 'tar' | '' from the first bytes of the file."""
    try:
        with open(path, "rb") as f:
            head = f.read(512)
    except OSError as e:
        raise CbzError("cannot open the file: %s" % e)
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    if head[:6] == b"Rar!\x1a\x07":
        return "rar"
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        return "sevenzip"
    if len(head) >= 262 and head[257:262] == b"ustar":
        return "tar"
    return ""


def _kind(path: str, external: bool) -> str:
    kind = sniff(path)
    if not kind:
        raise CbzError("not a comic archive (not a ZIP, RAR, 7z or TAR file)")
    if not external and kind != "zip":
        raise CbzError("only ZIP comics are opened from the upload folder")
    return kind


# ---- bsdtar (RAR and 7z) -------------------------------------------------------------------------------------------------
_TOOL = {"path": None, "looked": False}


def bsdtar() -> str | None:
    """The libarchive tar tool, or None. On Windows the system tar.exe is bsdtar; on Linux it is the `bsdtar` command (GNU
    tar cannot read RAR or 7z, so it is never used)."""
    if not _TOOL["looked"]:
        candidates = [shutil.which("bsdtar")]
        if os.name == "nt":
            candidates.append(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tar.exe"))
        found = None
        for exe in candidates:
            if not exe or not os.path.isfile(exe):
                continue
            try:
                out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout.lower()
            except (OSError, subprocess.SubprocessError):
                continue
            if "bsdtar" in out or "libarchive" in out:
                found = exe
                break
        _TOOL["path"], _TOOL["looked"] = found, True
    return _TOOL["path"]


def _run_tool(args: list, limit: int, what: str) -> bytes:
    """Run bsdtar with a time limit, collect at most `limit` bytes of output (more is an error) and require a clean exit."""
    exe = bsdtar()
    if not exe:
        raise CbzError("this archive type needs bsdtar (libarchive-tools), which is not installed here")
    try:
        proc = subprocess.Popen([exe] + args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                cwd=tempfile.gettempdir())
    except OSError as e:
        raise CbzError("cannot start bsdtar: %s" % e)
    timed_out = []
    timer = threading.Timer(TOOL_SECONDS, lambda: (timed_out.append(1), proc.kill()))
    timer.start()
    try:
        data = proc.stdout.read(limit + 1)
        if len(data) > limit:
            proc.kill()
            raise CbzError("%s is larger than %d MB" % (what, limit // (1024 * 1024)))
        code = proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.wait()
    if timed_out:
        raise CbzError("reading the archive took longer than %d seconds" % TOOL_SECONDS)
    if code != 0:
        raise CbzError("the archive could not be read (it may be damaged, encrypted or in an unsupported variant)")
    return data


def _glob_escape(name: str) -> str:
    """bsdtar matches member names as patterns: neutralise the pattern characters so one name selects exactly one entry."""
    return "".join("\\" + c if c in "*?[]\\" else c for c in name)


def _tool_names(path: str) -> list:
    text = _run_tool(["-tf", path], MAX_LIST_BYTES, "the file list").decode("utf-8", "replace")
    return [n for n in text.splitlines() if n and not n.endswith("/") and _page_name_ok(n)]


def _tool_read(path: str, name: str, limit: int) -> bytes:
    return _run_tool(["-xOf", path, "--", _glob_escape(name)], limit, "page")


# ---- public ---------------------------------------------------------------------------------------------------------------
def list_pages(path: str, external: bool = True) -> list:
    """Names of the image entries in reading order (natural sort: page 2 before page 10). `external=False` (the upload folder)
    allows ZIP files only."""
    kind = _kind(path, external)
    try:
        if kind == "zip":
            with zipfile.ZipFile(path) as zf:
                names = [i.filename for i in zf.infolist() if _is_page(i)]
        elif kind == "tar":
            with tarfile.open(path) as tf:
                names = [m.name for m in tf.getmembers() if m.isfile() and _page_name_ok(m.name)]
        else:
            names = _tool_names(path)
    except CbzError:
        raise
    except _ERRORS as e:
        raise CbzError("not a readable comic archive: %s" % e)
    if not names:
        raise CbzError("no images in this archive")
    if len(names) > MAX_PAGES:
        raise CbzError("more than %d pages" % MAX_PAGES)
    try:
        return sorted(names, key=lambda n: natural_key(n.replace("\\", "/")))
    except (ValueError, OverflowError) as e:                       # hostile entry names must never reach the caller
        raise CbzError("unusable page names: %s" % e)


def read_page(path: str, name: str, limit: int = MAX_PAGE_BYTES, external: bool = True) -> bytes:
    """The bytes of one page. Reads at most `limit` bytes, whatever the archive header claims."""
    kind = _kind(path, external)
    too_big = CbzError("page is larger than %d MB" % (limit // (1024 * 1024)))
    try:
        if kind == "zip":
            with zipfile.ZipFile(path) as zf:
                info = zf.getinfo(name)
                if info.file_size > limit:
                    raise too_big
                with zf.open(info) as f:
                    data = f.read(limit + 1)
        elif kind == "tar":
            with tarfile.open(path) as tf:
                member = tf.getmember(name)
                if member.size > limit:
                    raise too_big
                src = tf.extractfile(member)
                if src is None:
                    raise CbzError("not a file in the archive")
                data = src.read(limit + 1)
        else:
            data = _tool_read(path, name, limit)
    except CbzError:
        raise
    except _ERRORS as e:
        raise CbzError("cannot read the page: %s" % e)
    if len(data) > limit:
        raise too_big
    if not data:
        raise CbzError("the page is empty")
    return data


def mime_for(name: str) -> str:
    return IMAGE_TYPES.get(_extension(name), "application/octet-stream")

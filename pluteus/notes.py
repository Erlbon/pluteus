"""Notes about a folder, written from the browser.

A library is read-only, so a note a user adds or edits about a folder of the library is kept in that user's own upload folder, at
    Notes/<library>/<the folder's path>/<note name>
and shown on that folder for that user (their note wins over the file in the library). A note inside the upload folder itself is edited
in place. Nothing outside the user's upload folder is ever written, only a few note names are accepted for the overlay, the text has a size
limit, the write is atomic, and a save that finds the file changed since it was opened is refused (no silent overwrite).
"""
from __future__ import annotations

import os

from . import uploads
from .config import UPLOADS_ROOT
from .fs import longpath

NOTE_NAMES = ("artist.md", "author.md", "series.md", "album.md", "about.md", "readme.md")     # what a folder's page shows
EDITABLE_EXTENSIONS = (".md", ".markdown", ".txt")
MAX_NOTE_BYTES = 64 * 1024
OVERLAY = "Notes"


class NoteError(Exception):
    """A refused save. `code` is one of the keys of MESSAGES."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


MESSAGES = {
    "name": "That is not a note name pluteus can store here.", "toobig": "The note is longer than 64 KB.",
    "changed": "The note changed on disk after you opened it. Your text is kept below; copy it, reload and save again.",
    "quota": "The note does not fit in your space.", "write": "The note could not be written.", "bin": "A note is text: that was not.",
}


def clean_name(name: str, in_place: bool) -> str | None:
    """The note file name, or None when it may not be written. In the overlay only the recognised note names; in the user's own upload
    folder any plain .md, .markdown or .txt name."""
    name = (name or "").strip()
    if not name or "/" in name or "\\" in name or name.startswith(".") or "\x00" in name or len(name) > 100:
        return None
    lower = name.lower()
    if in_place:
        return name if lower.endswith(EDITABLE_EXTENSIONS) and uploads.sanitize_name(name) == name else None
    return lower if lower in NOTE_NAMES else None


def overlay_parts(root: str, parts) -> tuple | None:
    """Where, inside the user's upload folder, the notes of this library folder live (as path parts), or None when a name cannot be kept."""
    names = [uploads.sanitize_name(p) for p in (root, *parts)]
    if any(n is None for n in names):
        return None
    return (OVERLAY, *names)


def target(root: str, parts, name: str):
    """(folder parts inside the upload folder, file name, in_place) for the note `name` of a folder, or None. A folder of the upload folder
    itself is edited in place; any other library gets the overlay."""
    if root == UPLOADS_ROOT:
        file_name = clean_name(name, True)
        return (tuple(parts), file_name, True) if file_name else None
    file_name = clean_name(name, False)
    where = overlay_parts(root, parts)
    return (where, file_name, False) if file_name and where else None


def version_of(path: str) -> str:
    """Tells whether a file changed: '' when it is not there."""
    try:
        st = os.stat(longpath(path))
    except OSError:
        return ""
    return "%d-%d" % (st.st_mtime_ns, st.st_size)


def _file(lib, user, folder_parts, file_name: str):
    """The path of the note file inside the upload folder (it may not exist), or None when its folder does not."""
    try:
        return os.path.join(lib.upload_dir(user, folder_parts), file_name)
    except Exception:                                      # noqa: BLE001  NotFound: nothing of that name yet
        return None


def read(lib, user, root: str, parts, name: str):
    """(text, version, in_place) of this user's own note `name` for the folder, or None. The version goes back with the save."""
    found = target(root, parts, name)
    if found is None:
        return None
    folder_parts, file_name, in_place = found
    path = _file(lib, user, folder_parts, file_name)
    if path is None or not os.path.isfile(longpath(path)):
        return None
    try:
        with open(longpath(path), "rb") as f:
            raw = f.read(MAX_NOTE_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_NOTE_BYTES or b"\x00" in raw[:4096]:
        return None
    return raw.decode("utf-8", "replace"), version_of(path), in_place


def _ensure_folders(lib, user, folder_parts: tuple) -> str:
    here = ()
    for part in folder_parts:
        here += (part,)
        try:
            lib.upload_dir(user, here)
        except Exception:                                  # noqa: BLE001  not there yet: make it
            uploads.make_folder(lib.upload_dir(user, here[:-1]), part, lib.is_hidden_name)
    return lib.upload_dir(user, folder_parts)


def save(lib, quota_mb: int, user, root: str, parts, name: str, text: str, version: str) -> None:
    """Write the note atomically. `version` is what read() gave when the page was opened ('' for a new note). Raises NoteError."""
    found = target(root, parts, name)
    if found is None:
        raise NoteError("name")
    folder_parts, file_name, _ = found
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise NoteError("bin")
    raw = text.encode("utf-8")
    if len(raw) > MAX_NOTE_BYTES:
        raise NoteError("toobig")
    path = _file(lib, user, folder_parts, file_name)
    if (version_of(path) if path else "") != (version or ""):
        raise NoteError("changed")
    old = 0
    if path and os.path.isfile(longpath(path)):
        old = os.path.getsize(longpath(path))
    if quota_mb:
        left = quota_mb * 1024 * 1024 - uploads.tree_size(lib.upload_base(user)) + old
        if len(raw) > left:
            raise NoteError("quota")
    try:
        folder = _ensure_folders(lib, user, folder_parts)
        uploads.write_atomic(folder, file_name, raw)
    except (uploads.UploadError, OSError):
        raise NoteError("write")


def remove(lib, user, root: str, parts, name: str, version: str) -> bool:
    """Delete this user's own note (the overlay file, or the file in their upload folder). True when something was removed."""
    found = target(root, parts, name)
    if found is None:
        raise NoteError("name")
    folder_parts, file_name, _ = found
    path = _file(lib, user, folder_parts, file_name)
    if path is None or not os.path.isfile(longpath(path)):
        return False
    if version_of(path) != (version or ""):
        raise NoteError("changed")
    try:
        os.remove(longpath(path))
    except OSError:
        raise NoteError("write")
    return True


# ---- shared notes: written into the library itself (a root with notes = "edit", a user with the notes right) --------------------------
def may_share(cfg, user, root: str) -> bool:
    """Whether this user may write the note files of folders of this library for everybody."""
    r = cfg.roots.get(root)
    return bool(r and r.notes == "edit" and getattr(user, "notes", False) and not user.read_only and user.may_use(root))


def _shared_target(lib, user, root: str, parts, name: str):
    """(folder path, the file name to use) in the library, or None. The folder must be one this user may see inside the root; an existing
    note file of the same name in another letter case is the one that is written (never two files)."""
    file_name = clean_name(name, False)
    if file_name is None:
        return None
    folder = lib.folder_path(user, root, tuple(parts))                # NotFound / PathError for anything hidden or outside the library
    try:
        for e in lib.list_dir(user, root, tuple(parts), True, False):
            if not e.is_dir and e.name.lower() == file_name:
                file_name = e.name
                break
    except OSError:
        pass
    return folder, file_name


def read_shared(lib, user, root: str, parts, name: str):
    """(text, version) of the library's own note file, or None."""
    found = _shared_target(lib, user, root, parts, name)
    if found is None:
        return None
    path = os.path.join(*found)
    if os.path.islink(longpath(path)) or not os.path.isfile(longpath(path)):
        return None
    try:
        with open(longpath(path), "rb") as f:
            raw = f.read(MAX_NOTE_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_NOTE_BYTES or b"\x00" in raw[:4096]:
        return None
    return raw.decode("utf-8", "replace"), version_of(path)


def save_shared(lib, user, root: str, parts, name: str, text: str, version: str) -> None:
    """Write the library's note file (a note name only, atomically, never over a changed file). Raises NoteError."""
    found = _shared_target(lib, user, root, parts, name)
    if found is None:
        raise NoteError("name")
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise NoteError("bin")
    raw = text.encode("utf-8")
    if len(raw) > MAX_NOTE_BYTES:
        raise NoteError("toobig")
    folder, file_name = found
    path = os.path.join(folder, file_name)
    if os.path.islink(longpath(path)) or os.path.isdir(longpath(path)):
        raise NoteError("name")
    if version_of(path) != (version or ""):
        raise NoteError("changed")
    try:
        uploads.write_atomic(folder, file_name, raw)
    except (uploads.UploadError, OSError):
        raise NoteError("write")


def remove_shared(lib, user, root: str, parts, name: str, version: str) -> bool:
    found = _shared_target(lib, user, root, parts, name)
    if found is None:
        raise NoteError("name")
    path = os.path.join(*found)
    if os.path.islink(longpath(path)) or not os.path.isfile(longpath(path)):
        return False
    if version_of(path) != (version or ""):
        raise NoteError("changed")
    try:
        os.remove(longpath(path))
    except OSError:
        raise NoteError("write")
    return True

"""Tags, cover art and lyrics of audio and video files, read when a file is opened or listed.

`tinytag` (MIT, pure Python) reads only the start of the file (the tag block and the stream header), so this is cheap even over a
network share. Everything that comes out of a file is untrusted text: it is cleaned and capped here and escaped wherever it is shown;
a cover is accepted only if its first bytes really are a JPEG, PNG, WebP or GIF (never an SVG), whatever the file claims.
Without tinytag installed every function returns "nothing found" and the player simply shows the file name.
"""
from __future__ import annotations

import os
import re
import struct
import threading
import time

MEDIA_EXT = {".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".oga", ".opus", ".wav", ".flac", ".wma", ".aiff", ".aif",
             ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".ogv"}
MAX_COVER = 8 * 1024 * 1024
MAX_LYRICS = 30_000
MAX_LRC_BYTES = 512 * 1024
FOLDER_COVER_NAMES = ("cover", "folder", "front", "album", "albumart")
FOLDER_COVER_EXT = (".jpg", ".jpeg", ".png", ".webp")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _text(value, limit: int = 300) -> str:
    if value is None:
        return ""
    return _CONTROL.sub("", str(value)).strip()[:limit]


def _number(value):
    try:
        n = int(value)
        return n if 0 < n < 100000 else None
    except (TypeError, ValueError):
        return None


def _tinytag():
    try:
        from tinytag import TinyTag
        return TinyTag
    except ImportError:
        return None


def read_info(path: str, with_cover: bool = False) -> dict:
    """The tags of one file as a plain dict (empty values left out of the interesting keys); {} when nothing can be read.
    with_cover also looks for an embedded picture (it has to read the picture, so the listing leaves it out)."""
    TinyTag = _tinytag()
    if TinyTag is None:
        return {}
    try:
        tag = TinyTag.get(path, tags=True, duration=True, image=with_cover)
    except Exception:                                   # a damaged or unsupported file is not an error for the page
        return {}
    year = re.match(r"\d{4}", _text(tag.year, 20))
    other = getattr(tag, "other", None) or {}
    lyrics = other.get("lyrics") or other.get("unsynced_lyrics") or []
    if isinstance(lyrics, str):
        lyrics = [lyrics]
    info = {
        "title": _text(tag.title), "artist": _text(tag.artist), "albumartist": _text(getattr(tag, "albumartist", None)),
        "album": _text(tag.album), "year": year.group(0) if year else "", "genre": _text(tag.genre, 80),
        "composer": _text(getattr(tag, "composer", None), 120), "comment": _text(getattr(tag, "comment", None), 400),
        "track": _number(tag.track), "track_total": _number(tag.track_total), "disc": _number(tag.disc),
        "duration": float(tag.duration) if tag.duration else None,
        "bitrate": int(round(tag.bitrate)) if tag.bitrate else None,
        "samplerate": int(tag.samplerate) if tag.samplerate else None,
        "channels": int(tag.channels) if tag.channels else None,
        "lyrics": _CONTROL.sub("", "\n\n".join(str(x) for x in lyrics)).strip()[:MAX_LYRICS],
        "format": os.path.splitext(path)[1][1:].upper(),
    }
    if with_cover:
        try:
            info["has_cover"] = bool(tag.images.any)
        except Exception:                               # noqa: BLE001
            info["has_cover"] = False
    return info


def song_info(path: str) -> dict:
    """The compact tag set a music app needs (cached by the caller): no lyrics, no comment."""
    info = read_info(path)
    keep = ("title", "artist", "albumartist", "album", "year", "genre", "track", "disc", "duration", "bitrate")
    return {k: info[k] for k in keep if info.get(k)}


def sniff_image(data: bytes) -> str:
    """The image type from the first bytes, or '' (SVG and everything else is refused)."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return ""


_folder_cache: dict = {}
_folder_lock = threading.Lock()


def _stamp(directory: str):
    try:
        return os.stat(directory).st_mtime_ns
    except OSError:
        return None


def folder_cover(directory: str) -> str:
    """The path of cover.jpg / folder.jpg / front.jpg ... in `directory` ('' if none).

    Remembered per folder, but only while the folder itself has not changed: adding or removing a file changes the folder's
    modified time, so a folder.jpg you put there is seen on the next refresh, not minutes later. (Some file systems keep the
    time only to the second, so "nothing found" is also looked at again after ten seconds, and a hit after ten minutes.)"""
    now = time.monotonic()
    stamp = _stamp(directory)
    with _folder_lock:
        hit = _folder_cache.get(directory)
        if hit and hit[1] > now and hit[2] == stamp:
            return hit[0]
    found = ""
    for name in FOLDER_COVER_NAMES:
        for variant in (name, name.capitalize()):
            for ext in FOLDER_COVER_EXT:
                candidate = os.path.join(directory, variant + ext)
                if os.path.isfile(candidate):
                    found = candidate
                    break
            if found:
                break
        if found:
            break
    with _folder_lock:
        if len(_folder_cache) > 2000:
            _folder_cache.clear()
        _folder_cache[directory] = (found, now + (600 if found else 10), stamp)
    return found


def read_folder_cover(directory: str):
    """(bytes, mime) of the folder's own picture (folder.jpg, cover.jpg ...), or None."""
    sidecar = folder_cover(directory)
    if not sidecar:
        return None
    try:
        if os.path.getsize(sidecar) > MAX_COVER:
            return None
        with open(sidecar, "rb") as f:
            data = f.read(MAX_COVER + 1)
    except OSError:
        return None
    mime = sniff_image(data)
    return (data, mime) if mime and len(data) <= MAX_COVER else None


def read_cover(path: str):
    """(bytes, mime) of the picture embedded in the file, else of the folder's cover.jpg and the like, else None."""
    TinyTag = _tinytag()
    if TinyTag is not None:
        try:
            image = TinyTag.get(path, tags=True, duration=False, image=True).images.any
            data = image.data if image else b""
            if data and len(data) <= MAX_COVER and sniff_image(data):
                return data, sniff_image(data)
        except Exception:                               # noqa: BLE001
            pass
    return read_folder_cover(os.path.dirname(path))


_STAMP = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")


def find_lrc(path: str) -> list:
    """Timed lyrics from a .lrc file next to the audio file: [(seconds, text)], or [] when there is none."""
    base = os.path.splitext(path)[0]
    for ext in (".lrc", ".LRC"):
        candidate = base + ext
        if not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, "rb") as f:
                raw = f.read(MAX_LRC_BYTES)
        except OSError:
            return []
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        return parse_lrc(text)
    return []


def parse_lrc(text: str) -> list:
    lines = []
    for line in text.splitlines():
        stamps = list(_STAMP.finditer(line))
        if not stamps:
            continue
        words = _CONTROL.sub("", _STAMP.sub("", line)).strip()[:300]
        for m in stamps:
            fraction = m.group(3) or "0"
            seconds = int(m.group(1)) * 60 + int(m.group(2)) + int(fraction) / (10 ** len(fraction))
            lines.append((round(seconds, 2), words))
    lines.sort(key=lambda item: item[0])
    return lines[:3000]


def facts(info: dict) -> list:
    """Short facts for the chips: ['2001', 'Rock', 'Track 5 of 12', 'MP3 · 192 kbps · 44.1 kHz']."""
    out = []
    if info.get("year"):
        out.append(info["year"])
    if info.get("genre"):
        out.append(info["genre"])
    if info.get("track"):
        out.append("Track %d of %d" % (info["track"], info["track_total"]) if info.get("track_total") else "Track %d" % info["track"])
    tech = []
    if info.get("format"):
        tech.append(info["format"])
    if info.get("bitrate"):
        tech.append("%d kbps" % info["bitrate"])
    if info.get("samplerate"):
        tech.append(("%.1f kHz" % (info["samplerate"] / 1000)).replace(".0 ", " "))
    if tech:
        out.append(" · ".join(tech))
    return out


# ---- chapters of an audiobook (MP4 / m4b: the Nero "chpl" atom, which ffmpeg, mp4v2 and most audiobook tools write) -------------
MAX_CHAPTERS = 500


def _atoms(f, start: int, end: int):
    """(type, start of the content, end) of each atom between start and end; only headers are read."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I4s", head)
        header = 8
        if size == 1:
            wide = f.read(8)
            if len(wide) < 8:
                return
            size, header = struct.unpack(">Q", wide)[0], 16
        elif size == 0:
            size = end - pos
        if size < header:
            return
        yield kind, pos + header, min(pos + size, end)
        pos += size


def read_chapters(path: str) -> list:
    """[(seconds, title)] from the chapter list of an m4b / m4a / mp4 (Nero chpl) or an mp3 (ID3 CHAP frames), or [] when it has none.
    Never raises."""
    try:
        with open(path, "rb") as f:
            if f.read(3) == b"ID3":                                  # an mp3 with chapter frames
                f.seek(0)
                return _id3_chapters(f)
            size = os.fstat(f.fileno()).st_size
            for kind, s, e in _atoms(f, 0, size):
                if kind != b"moov":
                    continue
                for k2, s2, e2 in _atoms(f, s, e):
                    if k2 != b"udta":
                        continue
                    for k3, s3, e3 in _atoms(f, s2, e2):
                        if k3 == b"chpl":
                            f.seek(s3)
                            return _parse_chpl(f.read(min(e3 - s3, 1024 * 1024)))
    except (OSError, struct.error, ValueError, IndexError):
        pass
    return []


def _parse_chpl(data: bytes) -> list:
    if len(data) < 9:
        return []
    off = 8 if data[0] == 1 else 4                       # version 1 has four reserved bytes before the count
    count = data[off]
    off += 1
    out = []
    for _ in range(min(count, MAX_CHAPTERS)):
        if off + 9 > len(data):
            break
        start = int.from_bytes(data[off:off + 8], "big") / 10_000_000         # 100 ns units
        length = data[off + 8]
        title = data[off + 9:off + 9 + length].decode("utf-8", "replace")
        off += 9 + length
        out.append((round(start, 2), _CONTROL.sub("", title).strip()[:200]))
    out.sort(key=lambda item: item[0])
    return out


def _synchsafe(raw: bytes) -> int:
    return (raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]


def _id3_text(data: bytes) -> str:
    """A text frame body: one encoding byte, then the text."""
    if not data:
        return ""
    enc, text = data[0], data[1:]
    codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(enc, "latin-1")
    return _CONTROL.sub("", text.decode(codec, "replace").replace("\x00", "")).strip()[:200]


def _id3_frames(body: bytes, major: int):
    """(id, data) of each frame of an ID3v2.3 / 2.4 tag (or of the sub-frames inside a frame)."""
    pos = 0
    while pos + 10 <= len(body) and body[pos] != 0:
        ident = body[pos:pos + 4]
        size = _synchsafe(body[pos + 4:pos + 8]) if major == 4 else int.from_bytes(body[pos + 4:pos + 8], "big")
        data = body[pos + 10:pos + 10 + size]
        if size <= 0 or pos + 10 + size > len(body):
            if size > 0 and ident == b"CHAP":
                data = body[pos + 10:]                      # a cut-off last frame: take what is there
            else:
                return
        yield ident, data
        pos += 10 + size


def _id3_chapters(f) -> list:
    head = f.read(10)
    if len(head) < 10 or head[3] not in (3, 4):
        return []
    major, flags = head[3], head[5]
    size = _synchsafe(head[6:10])
    body = f.read(min(size, 8 * 1024 * 1024))
    if flags & 0x80 and major == 3:                         # unsynchronisation of the whole tag
        body = body.replace(b"\xff\x00", b"\xff")
    if flags & 0x40 and len(body) >= 4:                     # an extended header comes first: skip it
        skip = _synchsafe(body[:4]) if major == 4 else int.from_bytes(body[:4], "big") + 4
        body = body[skip:]
    out = []
    for ident, data in _id3_frames(body, major):
        if ident != b"CHAP":
            continue
        end = data.find(b"\x00")
        if end < 0 or len(data) < end + 17:
            continue
        start_ms = int.from_bytes(data[end + 1:end + 5], "big")
        title = ""
        for sub, sub_data in _id3_frames(data[end + 17:], major):
            if sub == b"TIT2":
                title = _id3_text(sub_data)
                break
        out.append((round(start_ms / 1000, 2), title))
        if len(out) >= MAX_CHAPTERS:
            break
    out.sort(key=lambda item: item[0])
    return out

"""Tags, cover art and lyrics of audio and video files, read when a file is opened or listed.

`tinytag` (MIT, pure Python) reads only the start of the file (the tag block and the stream header), so this is cheap even over a
network share. Everything that comes out of a file is untrusted text: it is cleaned and capped here and escaped wherever it is shown;
a cover is accepted only if its first bytes really are a JPEG, PNG, WebP or GIF (never an SVG), whatever the file claims.
Without tinytag installed every function returns "nothing found" and the player simply shows the file name.
"""
from __future__ import annotations

import os
import re
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


def brief(path: str) -> dict:
    """What a folder listing needs: title, artist and track, in the shape the book listing already understands."""
    info = read_info(path)
    if not info.get("title"):
        return {}
    return {"title": info["title"], "authors": [info["artist"]] if info.get("artist") else [], "track": info.get("track")}


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


def folder_cover(directory: str) -> str:
    """The path of cover.jpg / folder.jpg / front.jpg ... in `directory` ('' if none). Remembered for two minutes per folder."""
    now = time.monotonic()
    with _folder_lock:
        hit = _folder_cache.get(directory)
        if hit and hit[1] > now:
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
        _folder_cache[directory] = (found, now + 120)
    return found


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
    sidecar = folder_cover(os.path.dirname(path))
    if sidecar:
        try:
            if os.path.getsize(sidecar) <= MAX_COVER:
                with open(sidecar, "rb") as f:
                    data = f.read(MAX_COVER + 1)
                if len(data) <= MAX_COVER and sniff_image(data):
                    return data, sniff_image(data)
        except OSError:
            pass
    return None


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

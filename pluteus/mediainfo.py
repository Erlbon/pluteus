"""Tags, cover art and lyrics of audio and video files, read when a file is opened or listed.

`tinytag` (MIT, pure Python) reads only the start of the file (the tag block and the stream header), so this is cheap even over a
network share. tinytag cannot read Matroska, so .mkv / .mka / .webm have their own small reader here (read_matroska). Everything that comes out of a file is untrusted text: it is cleaned and capped here and escaped wherever it is shown;
a cover is accepted only if its first bytes really are a JPEG, PNG, WebP or GIF (never an SVG), whatever the file claims.
Without tinytag installed every function returns "nothing found" and the player simply shows the file name.
"""
from __future__ import annotations

import math
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
    if os.path.splitext(path)[1].lower() in MATROSKA_EXT:
        return _matroska_info(path, with_cover)
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


# lower-case file name -> its place in the order of preference (cover.jpg, cover.jpeg ... albumart.webp)
_COVER_RANK = {name + ext: rank for rank, (name, ext) in enumerate((n, e) for n in FOLDER_COVER_NAMES for e in FOLDER_COVER_EXT)}
_FOLDER_SECONDS = 600


def folder_cover(directory: str) -> str:
    """The path of cover.jpg / folder.jpg / front.jpg ... in `directory` ('' if none), in any letter case (Cover.JPG, FOLDER.jpg).

    One listing of the folder answers it (not one probe per possible name: that was 40 round trips on a network share for a
    folder without a picture). Remembered per folder for ten minutes, a miss as long as a hit, but only while the folder itself
    has not changed: adding or removing a file changes the folder's modified time, so a folder.jpg you put there is seen on the
    next refresh, not minutes later. (Some file systems keep that time only to the second, so nothing is remembered about a
    folder that changed in the last two seconds.)"""
    now = time.monotonic()
    stamp = _stamp(directory)
    with _folder_lock:
        hit = _folder_cache.get(directory)
        if hit and hit[1] > now and hit[2] == stamp:
            return hit[0]
    found, best = "", len(_COVER_RANK)
    try:
        with os.scandir(directory) as it:
            for entry in it:
                rank = _COVER_RANK.get(entry.name.lower())
                if rank is None or rank >= best:
                    continue
                try:
                    if entry.is_file():
                        found, best = entry.path, rank
                except OSError:
                    continue
    except OSError:
        return ""                         # the folder cannot be listed right now: no picture, and nothing remembered
    if stamp is None or time.time_ns() - stamp < 2_000_000_000:
        return found                      # changed in the last two seconds: the time stamp is too coarse to trust yet, look again next time
    with _folder_lock:
        if len(_folder_cache) > 2000:
            _folder_cache.clear()
        _folder_cache[directory] = (found, now + _FOLDER_SECONDS, stamp)
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
    if os.path.splitext(path)[1].lower() in MATROSKA_EXT:
        data = read_matroska(path, cover=True).get("cover") or b""
        if data and sniff_image(data):
            return data, sniff_image(data)
    elif TinyTag is not None:
        try:
            image = TinyTag.get(path, tags=True, duration=False, image=True).images.any
            data = image.data if image else b""
            if data and len(data) <= MAX_COVER and sniff_image(data):
                return data, sniff_image(data)
        except Exception:                               # noqa: BLE001
            pass
    return read_folder_cover(os.path.dirname(path))


# ---- Matroska (.mkv / .mka / .webm): tinytag has no reader for it ---------------------------------------------------------------
# Only the small elements are read: Segment Info (duration, title), Tags and the Attachments' names. They are found through the
# SeekHead index at the front of the file, which also leads to the second SeekHead that mkvpropedit writes at the very end when it
# moves edited Tags and Attachments behind the clusters. So a film over a network share costs a dozen small reads, never the
# clusters. A file without any SeekHead is walked element by element (headers only), up to a limit.
MATROSKA_EXT = {".mkv", ".mka", ".mk3d", ".webm"}
MKV_COVER_NAME = re.compile(r"^cover\.(jpe?g|png|webp)$", re.IGNORECASE)        # the names videoredactor, Jellyfin and Kodi use
_EBML, _SEGMENT, _SEEKHEAD, _SEEK, _SEEK_ID, _SEEK_POS = 0x1A45DFA3, 0x18538067, 0x114D9B74, 0x4DBB, 0x53AB, 0x53AC
_INFO, _SCALE, _DURATION, _TITLE, _CLUSTER = 0x1549A966, 0x2AD7B1, 0x4489, 0x7BA9, 0x1F43B675
_TAGS, _TAG, _TARGETS, _TARGET_TYPE, _SIMPLE, _TAG_NAME, _TAG_STRING = 0x1254C367, 0x7373, 0x63C0, 0x68CA, 0x67C8, 0x45A3, 0x4487
_TARGET_UIDS = {0x63C5, 0x63C9, 0x63C4, 0x63C6}                 # a tag aimed at a track, edition, chapter or attachment
_ATTACHMENTS, _ATTACHED, _FILE_NAME, _FILE_DATA = 0x1941A469, 0x61A7, 0x466E, 0x465C
_MAX_INFO, _MAX_TAGS = 64 * 1024, 4 * 1024 * 1024
_MAX_WALK = 300                       # top-level elements looked at in a file without an index (each one is a seek: a round trip on a share)
_MAX_ATTACHMENTS = 200                # attachments looked at for a cover
_MAX_ATTACHED_PARTS = 64              # elements looked at inside one attachment (a real one has four or five)


def _ebml_head(buf: bytes, pos: int):
    """(id, size, header length) of the element starting at buf[pos]; size None means "unknown" (runs to the parent's end).
    Raises IndexError or ValueError on bytes that are not an element header."""
    first = buf[pos]
    n = 9 - first.bit_length()
    if first == 0 or n > 4:
        raise ValueError("not an element id")
    ident = int.from_bytes(buf[pos:pos + n], "big")
    lead = buf[pos + n]
    m = 9 - lead.bit_length()
    if lead == 0:
        raise ValueError("bad size")
    raw = buf[pos + n:pos + n + m]
    if len(raw) < m:
        raise IndexError("cut off")
    size = int.from_bytes(raw, "big") & ((1 << (7 * m)) - 1)
    return ident, (None if size == (1 << (7 * m)) - 1 else size), n + m


def _ebml_children(buf: bytes, start: int, end: int):
    """(id, start of the content, end) of each element in buf[start:end]."""
    pos = start
    while pos < end:
        ident, size, head = _ebml_head(buf, pos)
        stop = end if size is None else min(pos + head + size, end)
        yield ident, pos + head, stop
        pos = stop


def _file_element(f, pos: int, end: int):
    """(id, start of the content, end) of the element at pos in the open file, reading only its header; None past the end."""
    if pos >= end:
        return None
    f.seek(pos)
    ident, size, head = _ebml_head(f.read(min(12, end - pos)), 0)
    return ident, pos + head, end if size is None else min(pos + head + size, end)


def _ebml_text(data: bytes, limit: int = 2000) -> str:
    return data.decode("utf-8", "replace").replace("\x00", "")[:limit]


def _matroska_tags(buf: bytes) -> dict:
    """{NAME: value} of the tags aimed at the whole file; the most specific level first (an episode's title before its show's)."""
    found = []
    for ident, s, e in _ebml_children(buf, 0, len(buf)):
        if ident != _TAG:
            continue
        level, aimed, pairs = 50, False, []
        for kind, s2, e2 in _ebml_children(buf, s, e):
            if kind == _TARGETS:
                for k3, s3, e3 in _ebml_children(buf, s2, e2):
                    if k3 == _TARGET_TYPE:
                        level = int.from_bytes(buf[s3:e3], "big")
                    elif k3 in _TARGET_UIDS and int.from_bytes(buf[s3:e3], "big"):
                        aimed = True
            elif kind == _SIMPLE:
                name = value = None
                for k3, s3, e3 in _ebml_children(buf, s2, e2):
                    if k3 == _TAG_NAME:
                        name = _ebml_text(buf[s3:e3], 100).upper()
                    elif k3 == _TAG_STRING:
                        value = _ebml_text(buf[s3:e3])
                if name and value:
                    pairs.append((name, value))
        if not aimed:
            found.append((level, pairs))
    tags: dict = {}
    for _, pairs in sorted(found, key=lambda item: item[0]):
        for name, value in pairs:
            tags.setdefault(name, value)
    return tags


def read_matroska(path: str, cover: bool = False) -> dict:
    """{'tags': {NAME: value}, 'title': ..., 'duration': seconds, 'has_cover': bool, 'cover': bytes} from a Matroska file, as far
    as it can be read; {} when it is not one. cover=True also reads the cover attachment's bytes. Never raises."""
    out: dict = {}
    try:
        with open(path, "rb") as f:
            end = os.fstat(f.fileno()).st_size
            head = _file_element(f, 0, end)
            if not head or head[0] != _EBML:
                return {}
            seg = _file_element(f, head[2], end)
            if not seg or seg[0] != _SEGMENT:
                return {}
            base, seg_end = seg[1], seg[2]
            places: dict = {}                                           # id -> (start of the content, end)
            seekheads, seen = [], set()
            pos, walked, indexed = base, 0, False
            while walked < _MAX_WALK:                                   # the elements before the first cluster, or all of them without an index
                el = _file_element(f, pos, seg_end)
                if not el or (el[0] == _CLUSTER and indexed):
                    break
                if el[0] == _SEEKHEAD:
                    seekheads.append(el)
                    indexed = True
                elif el[0] in (_INFO, _TAGS, _ATTACHMENTS):
                    places.setdefault(el[0], el[1:])
                pos, walked = el[2], walked + 1
            while seekheads and len(seen) < 8:
                ident, s, e = seekheads.pop(0)
                if s in seen:
                    continue
                seen.add(s)
                f.seek(s)
                buf = f.read(min(e - s, _MAX_INFO))
                for kind, s2, e2 in _ebml_children(buf, 0, len(buf)):
                    if kind != _SEEK:
                        continue
                    target = where = None
                    for k3, s3, e3 in _ebml_children(buf, s2, e2):
                        if k3 == _SEEK_ID:
                            target = int.from_bytes(buf[s3:e3], "big")
                        elif k3 == _SEEK_POS:
                            where = base + int.from_bytes(buf[s3:e3], "big")
                    if target not in (_INFO, _TAGS, _ATTACHMENTS, _SEEKHEAD) or where is None or target in places:
                        continue
                    try:
                        el = _file_element(f, where, seg_end)
                    except (IndexError, ValueError):
                        continue
                    if el and el[0] == target:
                        if target == _SEEKHEAD:
                            seekheads.append(el)
                        else:
                            places[target] = el[1:]
            if _INFO in places:
                s, e = places[_INFO]
                f.seek(s)
                buf = f.read(min(e - s, _MAX_INFO))
                scale, length = 1_000_000, None
                for kind, s2, e2 in _ebml_children(buf, 0, len(buf)):
                    if kind == _SCALE and e2 - s2 <= 8:                 # an unsigned integer of at most eight bytes; anything longer is not one
                        scale = int.from_bytes(buf[s2:e2], "big") or scale
                    elif kind == _DURATION and e2 - s2 in (4, 8):
                        length = struct.unpack(">f" if e2 - s2 == 4 else ">d", buf[s2:e2])[0]
                    elif kind == _TITLE:
                        out["title"] = _ebml_text(buf[s2:e2])
                if length and math.isfinite(length) and length > 0:
                    seconds = length * scale / 1e9
                    if math.isfinite(seconds):                          # "infinity" is not a duration (and not valid JSON either)
                        out["duration"] = round(seconds, 3)
            if _TAGS in places:
                s, e = places[_TAGS]
                f.seek(s)
                out["tags"] = _matroska_tags(f.read(min(e - s, _MAX_TAGS)))
            if _ATTACHMENTS in places:
                s, e = places[_ATTACHMENTS]
                best = None
                for _ in range(_MAX_ATTACHMENTS):                       # each attachment: its name and where its data is
                    el = _file_element(f, s, e)
                    if not el:
                        break
                    if el[0] == _ATTACHED:
                        name, data_at, pos = "", None, el[1]
                        for _part in range(_MAX_ATTACHED_PARTS):        # every element is a seek, so a stuffed attachment is given up on
                            child = _file_element(f, pos, el[2])
                            if child is None:
                                break
                            if child[0] == _FILE_NAME:
                                f.seek(child[1])
                                name = _ebml_text(f.read(min(child[2] - child[1], 300)))
                            elif child[0] == _FILE_DATA:
                                data_at = child[1:]
                            pos = child[2]
                        if data_at and MKV_COVER_NAME.match(name) and 0 < data_at[1] - data_at[0] <= MAX_COVER:
                            if best is None or name == "cover.jpg":
                                best = data_at
                    s = el[2]
                if best:
                    out["has_cover"] = True
                    if cover:
                        f.seek(best[0])
                        out["cover"] = f.read(best[1] - best[0])
    except (OSError, IndexError, ValueError, struct.error, ArithmeticError):
        pass                                                            # what was read before the damage is still good
    return out


def _matroska_info(path: str, with_cover: bool) -> dict:
    """read_info for a Matroska file: the same keys as for any other file."""
    found = read_matroska(path)
    if not found:
        return {}
    tags = found.get("tags", {})
    year = re.search(r"\d{4}", _text(tags.get("DATE_RELEASED") or tags.get("DATE") or tags.get("DATE_RECORDED") or tags.get("YEAR"), 40))
    info = {
        "title": _text(tags.get("TITLE") or found.get("title")), "artist": _text(tags.get("ARTIST")), "albumartist": "",
        "album": "", "year": year.group(0) if year else "", "genre": _text(tags.get("GENRE"), 80),
        "composer": _text(tags.get("COMPOSER"), 120), "comment": _text(tags.get("COMMENT") or tags.get("DESCRIPTION"), 400),
        "track": None, "track_total": None, "disc": None, "duration": found.get("duration"),
        "bitrate": None, "samplerate": None, "channels": None,
        "lyrics": _CONTROL.sub("", tags.get("LYRICS", "")).strip()[:MAX_LYRICS],
        "format": os.path.splitext(path)[1][1:].upper(),
    }
    if with_cover:
        info["has_cover"] = bool(found.get("has_cover"))
    return info


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
MAX_ATOMS = 2000                      # atoms looked at on one level (a fragmented MP4 has one pair per fragment: thousands)


def _atoms(f, start: int, end: int, limit: int = MAX_ATOMS):
    """(type, start of the content, end) of each atom between start and end, at most `limit` of them; only headers are read."""
    pos = start
    for _ in range(limit):
        if pos + 8 > end:
            return
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
                break                                                # a file has one moov: nothing after it is looked at
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

"""Subtitles that sit next to a video: Movie.srt, Movie.en.srt, Movie.forced.vtt ...

A browser only takes WebVTT in a <track>, so an .srt file is converted when it is asked for. Only text files in the video's own folder
are looked at, found by name, and the page asks for them by number (never by a name), like the entries of a zip file."""
from __future__ import annotations

import os
import re

MAX_BYTES = 1024 * 1024
MAX_TRACKS = 12
EXTENSIONS = (".srt", ".vtt")
LANGUAGES = {
    "en": "English", "eng": "English", "no": "Norwegian", "nor": "Norwegian", "nb": "Norwegian", "nob": "Norwegian",
    "nn": "Norwegian", "sv": "Swedish", "swe": "Swedish", "da": "Danish", "dan": "Danish", "fi": "Finnish", "fin": "Finnish",
    "de": "German", "ger": "German", "deu": "German", "fr": "French", "fre": "French", "fra": "French", "es": "Spanish",
    "spa": "Spanish", "it": "Italian", "ita": "Italian", "pt": "Portuguese", "por": "Portuguese", "nl": "Dutch", "dut": "Dutch",
    "nld": "Dutch", "ru": "Russian", "rus": "Russian", "pl": "Polish", "pol": "Polish", "ja": "Japanese", "jpn": "Japanese",
    "zh": "Chinese", "chi": "Chinese", "zho": "Chinese", "ko": "Korean", "kor": "Korean", "uk": "Ukrainian", "ukr": "Ukrainian",
}
_FLAGS = {"forced": "forced", "sdh": "SDH", "cc": "CC", "hi": "SDH", "default": ""}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_COMMA = re.compile(r"(\d+:\d{2}(?::\d{2})?),(\d{1,3})")
_STYLE = re.compile(r"\{\\[^}]*\}")                  # {\an8} and the like, left over from the ASS format


def _label(suffix: str) -> str:
    """'.en.forced' -> 'English (forced)'; no tags at all -> 'Subtitles'."""
    language, flags = "", []
    for tag in (t for t in suffix.lower().split(".") if t):
        if tag in LANGUAGES and not language:
            language = LANGUAGES[tag]
        elif tag in _FLAGS:
            if _FLAGS[tag]:
                flags.append(_FLAGS[tag])
        elif not language and 2 <= len(tag) <= 3 and tag.isalpha():
            language = tag.upper()
    name = language or "Subtitles"
    return name + (" (%s)" % ", ".join(flags) if flags else "")


def find(video_path: str) -> list:
    """[(path, label)] of the subtitle files named like the video, in name order; at most MAX_TRACKS, labels made unique."""
    folder, name = os.path.split(video_path)
    stem = os.path.splitext(name)[0].lower()
    try:
        names = sorted(os.listdir(folder), key=str.lower)
    except OSError:
        return []
    found, seen = [], {}
    for entry in names:
        base, ext = os.path.splitext(entry)
        if ext.lower() not in EXTENSIONS:
            continue
        low = base.lower()
        if low != stem and not low.startswith(stem + "."):
            continue
        full = os.path.join(folder, entry)
        if os.path.islink(full) or not os.path.isfile(full):
            continue
        label = _label(base[len(stem):])
        seen[label] = seen.get(label, 0) + 1
        found.append((full, label if seen[label] == 1 else "%s %d" % (label, seen[label])))
        if len(found) >= MAX_TRACKS:
            break
    return found


def _decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")           # the old Western European files


def to_vtt(path: str) -> str:
    """The file as WebVTT text: an .srt gets the header and dots in its times; a .vtt is passed on. '' if it cannot be read."""
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_BYTES)
    except OSError:
        return ""
    text = _CONTROL.sub("", _decode(raw)).replace("\r\n", "\n").replace("\r", "\n")
    if text.lstrip().startswith("WEBVTT"):
        return text.lstrip()
    out = ["WEBVTT", ""]
    for line in text.split("\n"):
        out.append(_COMMA.sub(lambda m: m.group(1) + "." + m.group(2), line) if "-->" in line else _STYLE.sub("", line))
    return "\n".join(out)

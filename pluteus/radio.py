"""Radio stations kept as small text files: .m3u (a stream address, optionally with #EXTINF:-1,Name), .pls and .url.

A file with at least one http(s) address opens in the radio player instead of the text viewer; a playlist of local music files,
or an HLS manifest (.m3u8 with #EXT-X-), has no station in it and stays a plain text file. Only the text of the file is read here:
pluteus never fetches the address, the browser plays it."""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

EXTENSIONS = (".m3u", ".m3u8", ".pls", ".url")
MAX_STATIONS = 500
MAX_URL = 2000
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_STREAM_ATTRIBUTE = re.compile(r'pluteus-stream="([^"]{1,60})"', re.IGNORECASE)
_PLS = re.compile(r"^(File|Title)(\d+)\s*=\s*(.*)$", re.IGNORECASE)


def _is_channel_address(url: str) -> bool:
    """An address that is a TV channel: HLS (.m3u8), or an MPEG transport stream (a .ts address, or the /proxy/ts/ path of Dispatcharr and the
    like). Radio never uses these."""
    path = urlsplit(url).path.lower()
    return path.endswith((".m3u8", ".ts", ".mpegts", ".m2ts")) or "/proxy/ts/" in path


def looks_like_tv(text: str, stations: list) -> bool:
    """An HLS address (.m3u8) may carry a picture, and only the video page can play HLS: the file opens there. The video page also
    plays a radio stream, but the radio page keeps playing while you browse, so plain streams stay with it. A file the TV picker saved says so itself (pluteus-kind="tv"), which is how a channel whose address does not end in
    .m3u8 (an MPEG-TS stream from a panel such as Dispatcharr) still opens as a channel. (IPTV attributes such
    as tvg-id are no hint: radio lists carry them too.)"""
    return 'pluteus-kind="tv"' in text or any(_is_channel_address(url) for _, url in stations)


LOGO_TYPES = ((".png", "image/png"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"), (".webp", "image/webp"))
MAX_LOGO = 600_000


def find_logo(path: str) -> str:
    """The picture that sits next to a station file and is named like it (NRK P3.m3u -> NRK P3.png), or ''. The pickers save it there;
    a picture put there by hand counts too."""
    stem = os.path.splitext(path)[0]
    for ext, _ in LOGO_TYPES:
        candidate = stem + ext
        if os.path.isfile(candidate) and not os.path.islink(candidate):
            return candidate
    return ""


def hide_logos(entries: list) -> list:
    """A folder listing without the pictures that belong to station files (NRK P3.png beside NRK P3.m3u): they are shown as the station's
    cover, not as files of their own. A picture with no station file of that name stays in the listing."""
    stems = {os.path.splitext(e.name)[0].lower() for e in entries if not e.is_dir and is_radio_file(e.name)}
    if not stems:
        return entries
    extensions = {ext for ext, _ in LOGO_TYPES}
    return [e for e in entries
            if e.is_dir or os.path.splitext(e.name)[1].lower() not in extensions or os.path.splitext(e.name)[0].lower() not in stems]


def read_logo(path: str):
    """(bytes, mime) of the picture next to a station file, or None. The type is stated here, never taken from the file."""
    found = find_logo(path)
    if not found:
        return None
    mime = dict(LOGO_TYPES)[os.path.splitext(found)[1].lower()]
    try:
        with open(found, "rb") as f:
            data = f.read(MAX_LOGO + 1)
    except OSError:
        return None
    return (data, mime) if 0 < len(data) <= MAX_LOGO else None


def is_radio_file(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in EXTENSIONS


def _address(text: str) -> str:
    text = text.strip()
    return text if text[:7].lower() == "http://" or text[:8].lower() == "https://" else ""


def _name(text: str) -> str:
    return _CONTROL.sub("", text).strip()[:120]


def read_file(path: str) -> list:
    """[(name, url)] of a station file on disk (a small text file: only its first 64 KB is read); [] when unreadable or no station."""
    try:
        with open(path, "rb") as f:
            raw = f.read(64 * 1024)
    except OSError:
        return []
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", "replace")
    return parse(text)


def read_file_streams(path: str) -> list:
    """[(name, url, stored stream)] of a station file on disk, like read_file with the stored-stream name of each station."""
    try:
        with open(path, "rb") as f:
            raw = f.read(64 * 1024)
    except OSError:
        return []
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", "replace")
    return _parse(text)


def _parse(text: str) -> list:
    """[(name, url, stored stream)] of the stations in the text; [] when it holds none. The stored stream is the name given by a
    pluteus-stream="..." attribute on the #EXTINF line (a login or header set up under Settings), or ''."""
    if "#EXT-X-" in text:                                    # an HLS manifest is a stream itself, not a list of stations
        return []
    stations = []
    lines = [line.strip() for line in text.replace("\r", "\n").split("\n")]
    if any(line.lower() == "[internetshortcut]" for line in lines):
        for line in lines:
            if line.lower().startswith("url=") and _address(line[4:]):
                stations.append(("", _address(line[4:]), ""))
    elif any(line.lower() == "[playlist]" for line in lines):
        files, titles = {}, {}
        for line in lines:
            m = _PLS.match(line)
            if m:
                (files if m.group(1).lower() == "file" else titles)[int(m.group(2))] = m.group(3)
        for number in sorted(files):
            if _address(files[number]):
                stations.append((_name(titles.get(number, "")), _address(files[number]), ""))
    else:
        name = stream = ""
        for line in lines:
            if line.upper().startswith("#EXTINF"):
                name = _name(line.rpartition(",")[2]) if "," in line else ""            # IPTV attributes may hold commas; the name is last
                found = _STREAM_ATTRIBUTE.search(line)
                stream = _name(found.group(1)) if found else ""
            elif line and not line.startswith("#"):
                if _address(line):
                    stations.append((name, _address(line), stream))
                name = stream = ""
    return [(n, u, st) for n, u, st in stations if len(u) <= MAX_URL][:MAX_STATIONS]


def parse(text: str) -> list:
    """[(name, url)] of the stations in the text; [] when it holds none."""
    return [(n, u) for n, u, _ in _parse(text)]


def parse_streams(text: str) -> list:
    """For each station of parse(text), the name of the stored stream it needs ('' for none)."""
    return [st for _, _, st in _parse(text)]

"""Radio stations kept as small text files: .m3u (a stream address, optionally with #EXTINF:-1,Name), .pls and .url.

A file with at least one http(s) address opens in the radio player instead of the text viewer; a playlist of local music files,
or an HLS manifest (.m3u8 with #EXT-X-), has no station in it and stays a plain text file. Only the text of the file is read here:
pluteus never fetches the address, the browser plays it."""
from __future__ import annotations

import os
import re

EXTENSIONS = (".m3u", ".m3u8", ".pls", ".url")
MAX_STATIONS = 500
MAX_URL = 2000
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_PLS = re.compile(r"^(File|Title)(\d+)\s*=\s*(.*)$", re.IGNORECASE)


def is_radio_file(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in EXTENSIONS


def _address(text: str) -> str:
    text = text.strip()
    return text if text[:7].lower() == "http://" or text[:8].lower() == "https://" else ""


def _name(text: str) -> str:
    return _CONTROL.sub("", text).strip()[:120]


def parse(text: str) -> list:
    """[(name, url)] of the stations in the text; [] when it holds none."""
    if "#EXT-X-" in text:                                    # an HLS manifest is a stream itself, not a list of stations
        return []
    stations = []
    lines = [line.strip() for line in text.replace("\r", "\n").split("\n")]
    if any(line.lower() == "[internetshortcut]" for line in lines):
        for line in lines:
            if line.lower().startswith("url=") and _address(line[4:]):
                stations.append(("", _address(line[4:])))
    elif any(line.lower() == "[playlist]" for line in lines):
        files, titles = {}, {}
        for line in lines:
            m = _PLS.match(line)
            if m:
                (files if m.group(1).lower() == "file" else titles)[int(m.group(2))] = m.group(3)
        for number in sorted(files):
            if _address(files[number]):
                stations.append((_name(titles.get(number, "")), _address(files[number])))
    else:
        name = ""
        for line in lines:
            if line.upper().startswith("#EXTINF"):
                name = _name(line.partition(",")[2])
            elif line and not line.startswith("#"):
                if _address(line):
                    stations.append((name, _address(line)))
                name = ""
    return [(n, u) for n, u in stations if len(u) <= MAX_URL][:MAX_STATIONS]

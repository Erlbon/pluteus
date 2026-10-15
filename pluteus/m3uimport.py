"""Reading an IPTV playlist (the M3U that Dispatcharr, an IPTV provider or any panel exports) into the stations the pickers save.

Only the text of the playlist is read: names, the stream addresses (plain http and https), and the usual attributes tvg-id, tvg-logo, tvg-name and group-title. Nothing in
it is fetched or run here. The result is data for the picker, which shows it, lets the user tick channels, and saves each as its own station file.
"""
from __future__ import annotations

import re

from .stationsave import _ADDRESS, _logo_address

MAX_ENTRIES = 3000
MAX_TEXT = 4_000_000
_ATTRIBUTE = re.compile(r"""([A-Za-z0-9_-]{1,40})\s*=\s*"([^"]*)\"""")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _clean(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", _CONTROL.sub(" ", text or "")).strip()[:limit]


def parse(text: str) -> dict:
    """{"stations": [{name, url, id, group, logo}], "skipped": n, "total": n}. The first MAX_ENTRIES usable channels are returned."""
    stations, skipped = [], 0
    pending = None
    for line in (text or "")[:MAX_TEXT].replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTINF"):
            head, _, name = line.rpartition(",")                      # attributes may hold commas; the display name is after the last one
            attrs = {k.lower(): v for k, v in _ATTRIBUTE.findall(head)}
            pending = {"name": _clean(name or attrs.get("tvg-name", ""), 120), "id": _clean(attrs.get("tvg-id", ""), 80),
                       "group": _clean(attrs.get("group-title", ""), 60), "logo": _logo_address(attrs.get("tvg-logo", "")),
                       "tvg-name": _clean(attrs.get("tvg-name", ""), 120)}
        elif not line.startswith("#"):
            if pending is None or not _ADDRESS.fullmatch(line):
                skipped += 1                                              # an address with no #EXTINF line before it, or not http(s)
                pending = None
                continue
            entry = {k: pending[k] for k in ("name", "id", "group", "logo")}
            entry["name"] = entry["name"] or pending["tvg-name"] or "Channel %d" % (len(stations) + 1)
            entry["url"] = line
            if len(stations) < MAX_ENTRIES:
                stations.append(entry)
            else:
                skipped += 1
            pending = None
    return {"stations": stations, "skipped": skipped}

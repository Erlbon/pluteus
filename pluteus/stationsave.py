"""Saving radio stations and TV channels as small .m3u files in a user's own upload folder (Radio/ or IPTV/, optionally a subfolder).

The pickers (static/pickers) send the ticked entries as JSON. Everything is checked here and nothing is trusted: names become safe
file names, addresses must be plain http(s), the text of each file is built here (never taken from the request), and the files are
written only inside the user's upload folder, within their space limit."""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed

from . import fetchimage, uploads
from .fs import longpath

KINDS = {"radio": "Radio", "tv": "IPTV"}
MAX_STATIONS = 300
LOGO_BUDGET = 25.0                 # seconds for all the logos of one save together
LOGO_EXTENSIONS = (".png", ".jpg")
_ADDRESS = re.compile(r"https?://[^\s\x00-\x1f\x7f]{1,2000}", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_ATTRIBUTE = re.compile(r"[^A-Za-z0-9._ -]")


def _text(value, limit: int = 120) -> str:
    return _CONTROL.sub(" ", str(value or "")).strip()[:limit].strip()


def _file_text(kind: str, name: str, url: str, ident: str, group: str, logo: str = "") -> str:
    attrs = ""
    if kind == "tv":
        if ident:
            attrs += ' tvg-id="%s"' % ident
        if group:
            attrs += ' group-title="%s"' % group
    if logo:
        attrs += ' tvg-logo="%s"' % logo
    return "#EXTM3U\n#EXTINF:-1%s,%s\n%s\n" % (attrs, name, url)


def _logo_address(value) -> str:
    """A logo address worth fetching: plain http(s), no quotes or spaces (it goes into an attribute), not absurdly long; else ''."""
    text = str(value or "").strip()
    return text if len(text) <= 500 and _ADDRESS.fullmatch(text) and '"' not in text else ""


def _fetch_logos(entries: list) -> dict:
    """{index: (bytes, extension)} for the entries whose logo could be fetched, several at a time and within one overall time budget:
    a slow or dead logo host costs that logo, never the save."""
    wanted = {i: _logo_address(e.get("logo")) for i, e in enumerate(entries) if isinstance(e, dict)}
    wanted = {i: u for i, u in wanted.items() if u}
    found = {}
    if not wanted:
        return found
    pool = ThreadPoolExecutor(max_workers=6)
    jobs = {pool.submit(fetchimage.fetch_logo, u): i for i, u in wanted.items()}
    try:
        for job in as_completed(jobs, timeout=LOGO_BUDGET):
            try:
                result = job.result()
            except Exception:                                  # noqa: BLE001
                result = None
            if result:
                found[jobs[job]] = result
    except FuturesTimeout:
        pass
    pool.shutdown(wait=False, cancel_futures=True)
    return found


def _write(folder: str, file_name: str, raw: bytes) -> None:
    temp = os.path.join(folder, ".%s.tmp" % file_name)
    path = os.path.join(folder, file_name)
    try:
        with open(longpath(temp), "wb") as f:
            f.write(raw)
        uploads._replace(longpath(temp), longpath(path))
    except OSError:
        try:
            os.remove(longpath(temp))
        except OSError:
            pass
        raise uploads.UploadError("write")


def _folder(lib, user, parts: tuple, raw: str) -> tuple:
    """parts + the folder `raw` inside it, created when missing (a user's upload folder only)."""
    name = uploads.sanitize_name(raw, lib.is_hidden_name)
    if name is None:
        raise uploads.UploadError("name")
    here = lib.upload_dir(user, parts)
    try:
        uploads.make_folder(here, name, lib.is_hidden_name)
    except uploads.UploadError as e:
        if e.code != "exists":
            raise
    inside = parts + (name,)
    lib.upload_dir(user, inside)                         # raises unless it is a real folder inside the upload folder
    return inside


def save(lib, quota_mb: int, user, data) -> dict:
    """Write the stations. data: {"kind": "radio"|"tv", "stations": [{name, url, id, group, country}], "sub": str,
    "by_country": bool, "overwrite": bool}. Returns {"saved": n, "skipped": n, "folder": "Radio/Norway"}.
    Raises uploads.UploadError (a code from web.UPLOAD_MESSAGES) or ValueError for a request that makes no sense."""
    if not isinstance(data, dict) or data.get("kind") not in KINDS or not isinstance(data.get("stations"), list):
        raise ValueError("format")
    kind = data["kind"]
    entries = data["stations"][:MAX_STATIONS]
    if not entries:
        raise ValueError("format")
    base = _folder(lib, user, (), KINDS[kind])
    sub = _text(data.get("sub"), 80)
    if sub:
        base = _folder(lib, user, base, sub)
    quota_left = None
    if quota_mb:
        quota_left = max(0, quota_mb * 1024 * 1024 - uploads.tree_size(lib.upload_base(user)))
    saved = skipped = logos = 0
    used = set()
    pictures = _fetch_logos(entries)
    for number, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "").strip()
        name = _text(entry.get("name"))
        if not name or not _ADDRESS.fullmatch(url):
            continue
        where = base
        if kind == "tv" and data.get("by_country") and _text(entry.get("country"), 60):
            where = _folder(lib, user, base, _text(entry.get("country"), 60))
        stem = uploads.sanitize_name(name, lib.is_hidden_name) or "Station"
        file_name, n = stem + ".m3u", 1
        while (where, file_name.lower()) in used:
            n += 1
            file_name = "%s %d.m3u" % (stem, n)
        used.add((where, file_name.lower()))
        folder = lib.upload_dir(user, where)
        path = os.path.join(folder, file_name)
        picture = pictures.get(number)
        sidecar = [os.path.join(folder, os.path.splitext(file_name)[0] + ext) for ext in LOGO_EXTENSIONS]
        exists = os.path.lexists(longpath(path)) and not data.get("overwrite")
        if exists:
            skipped += 1
        else:
            text = _file_text(kind, name, url, _ATTRIBUTE.sub("", _text(entry.get("id"), 80)), _ATTRIBUTE.sub("", _text(entry.get("group"), 60)),
                              _logo_address(entry.get("logo")))
            raw = text.encode("utf-8")
            if quota_left is not None:
                if len(raw) > quota_left:
                    raise uploads.UploadError("quota")
                quota_left -= len(raw)
            _write(folder, file_name, raw)
            saved += 1
        # the logo goes next to the file (a picture of the same name); an existing picture is kept unless the file is being replaced
        if picture and (not exists or not any(os.path.lexists(longpath(p)) for p in sidecar)):
            data_bytes, ext = picture
            if quota_left is None or len(data_bytes) <= quota_left:
                for old in sidecar:
                    if os.path.lexists(longpath(old)):
                        os.remove(longpath(old))
                _write(folder, os.path.splitext(file_name)[0] + "." + ext, data_bytes)
                logos += 1
                if quota_left is not None:
                    quota_left -= len(data_bytes)
    return {"saved": saved, "skipped": skipped, "logos": logos, "folder": "/".join(base)}

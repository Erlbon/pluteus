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
        attrs += ' pluteus-kind="tv"'                    # the page that plays it: a channel, whatever its address ends in
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


_write = uploads.write_atomic


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


# ---- logos for the station files that are already there -------------------------------------------------------------------------
_LOGO_ATTRIBUTE = re.compile(r'tvg-logo="([^"]{1,500})"', re.IGNORECASE)
_EXTINF = re.compile(r"^(#EXTINF:[^\s,]*)", re.MULTILINE)
MAX_WALK = 2000
MAX_DEPTH = 3
MAX_BATCH = 40


def _kind_folder(lib, user, kind: str):
    """(folder path, parts) of Radio/ or IPTV/ in the user's upload folder, or (None, ()) when there is none yet."""
    if kind not in KINDS:
        raise ValueError("format")
    parts = (KINDS[kind],)
    try:
        return lib.upload_dir(user, parts), parts
    except Exception:                                      # noqa: BLE001  NotFound: nothing saved yet
        return None, parts


def missing(lib, user, kind: str) -> list:
    """The station files of Radio/ or IPTV/ (and the folders below, a few levels) that have no picture beside them:
    [{"path": "Norway/NRK P3.m3u", "name": "NRK P3", "url": first stream address, "logo": the tvg-logo address in the file or ''}]."""
    from . import radio
    top, _ = _kind_folder(lib, user, kind)
    found = []
    if top is None:
        return found
    stack = [(top, "", 0)]
    while stack and len(found) < MAX_WALK:
        folder, rel, depth = stack.pop()
        try:
            with os.scandir(longpath(folder)) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False) and depth < MAX_DEPTH and not lib.is_hidden_name(e.name):
                    stack.append((os.path.join(folder, e.name), rel + e.name + "/", depth + 1))
                elif e.is_file(follow_symlinks=False) and radio.is_radio_file(e.name) and not lib.is_hidden_name(e.name):
                    path = os.path.join(folder, e.name)
                    if radio.find_logo(path):
                        continue
                    stations = radio.read_file(path)
                    if not stations:
                        continue
                    try:
                        with open(longpath(path), "rb") as f:
                            head = f.read(65536).decode("utf-8", "replace")
                    except OSError:
                        head = ""
                    m = _LOGO_ATTRIBUTE.search(head)
                    found.append({"path": rel + e.name, "name": os.path.splitext(e.name)[0], "url": stations[0][1],
                                  "logo": _logo_address(m.group(1)) if m else ""})
            except OSError:
                continue
    return found[:MAX_WALK]


def _with_logo_attribute(text: str, logo: str, stem: str) -> str:
    """The file's text with a tvg-logo attribute on its first #EXTINF line (a plain list of addresses gets one); unchanged if it has one."""
    if _LOGO_ATTRIBUTE.search(text):
        return text
    if _EXTINF.search(text):
        return _EXTINF.sub(lambda m: '%s tvg-logo="%s"' % (m.group(1), logo), text, count=1)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _ADDRESS.fullmatch(line.strip()):
            lines.insert(i, '#EXTINF:-1 tvg-logo="%s",%s' % (logo, _text(stem)))
            return "\n".join(lines) + "\n"
    return text


def apply_logos(lib, quota_mb: int, user, kind: str, items: list) -> dict:
    """Fetch the logo of each item ({"path": "Norway/NRK P3.m3u", "logo": address}) and save it beside that station file, and name it in the
    file when the file does not. Only existing station files inside Radio/ or IPTV/ of this user are touched; a picture that is already
    there is kept. Returns {"fetched": n, "failed": n, "skipped": n}."""
    from . import radio
    top, parts = _kind_folder(lib, user, kind)
    if top is None or not isinstance(items, list):
        raise ValueError("format")
    wanted = []
    for item in items[:MAX_BATCH]:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            wanted.append(item)
    pictures = _fetch_logos(wanted)
    quota_left = None
    if quota_mb:
        quota_left = max(0, quota_mb * 1024 * 1024 - uploads.tree_size(lib.upload_base(user)))
    fetched = failed = skipped = 0
    for number, item in enumerate(wanted):
        names = [p for p in item["path"].split("/") if p]
        if not names or len(names) > MAX_DEPTH + 1 or any(p in (".", "..") or "\\" in p or p != uploads.sanitize_name(p, lib.is_hidden_name) for p in names):
            skipped += 1
            continue
        try:
            folder = lib.upload_dir(user, parts + tuple(names[:-1]))
        except Exception:                                  # noqa: BLE001  not a folder in the user's upload area
            skipped += 1
            continue
        path = os.path.join(folder, names[-1])
        if not (radio.is_radio_file(names[-1]) and os.path.isfile(longpath(path)) and not os.path.islink(longpath(path))) or radio.find_logo(path):
            skipped += 1
            continue
        picture = pictures.get(number)
        if not picture:
            failed += 1
            continue
        data_bytes, ext = picture
        if quota_left is not None and len(data_bytes) > quota_left:
            raise uploads.UploadError("quota")
        stem = os.path.splitext(names[-1])[0]
        _write(folder, stem + "." + ext, data_bytes)
        if quota_left is not None:
            quota_left -= len(data_bytes)
        try:                                               # name the logo in the file too (a .m3u; the file stays otherwise as it was)
            if not names[-1].lower().endswith((".m3u", ".m3u8")) or not _logo_address(item.get("logo")):
                raise OSError
            with open(longpath(path), "rb") as f:
                original = f.read(262144 + 1)
            if len(original) <= 262144:
                text = original.decode("utf-8", "replace")
                updated = _with_logo_attribute(text, _logo_address(item.get("logo")), stem)
                if updated != text:
                    _write(folder, names[-1], updated.encode("utf-8"))
        except OSError:
            pass
        fetched += 1
    return {"fetched": fetched, "failed": failed, "skipped": skipped}

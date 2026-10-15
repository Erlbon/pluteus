"""A read-only subset of the Subsonic / OpenSubsonic API, so that existing music apps (and Android Auto through them) can
browse and play the audio files of the library.

Turned on with `subsonic = true` in [server]. The API lives under /rest/NAME(.view). What is supported: ping, folders,
folder browsing (getIndexes / getMusicDirectory), the tag-style browsing most apps use (getArtists / getArtist / getAlbum,
read as Artist folder / Album folder / songs), album lists, a simple search, cover art, stream and download. Everything else
that an app may ask for at start-up (playlists, genres, ...) is answered with an empty result. Stars are the user's
Favourites shelf (star, unstar, getStarred2, and `starred` on the items), and lyrics come as OpenSubsonic songLyrics. A saved play queue
(savePlayQueue / getPlayQueue, one per user) lets another device resume, and `recent` albums come from the saved positions.

Login: every user may have ONE app key (made on the admin page). It works as an OpenSubsonic API key (`apiKey`), as the
password in the salted-token login (`u`, `t`, `s`) and as the legacy `p` password. The real password is also accepted as `p`.
The key opens nothing but this read-only API. Failed logins count against the same throttle as the web login.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from urllib.parse import urlencode
import random
import re
import threading
import time
from types import SimpleNamespace
from xml.sax.saxutils import escape, quoteattr

from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, PlainTextResponse, Response

from . import __version__, aboutfiles, kinds, mediainfo, notes, radio, textview
from .auth import verify_password
from .bodies import read_limited
from .config import UPLOADS_ROOT
from .fs import NotFound, PathError, clamp_time
from .state import passing_by

API_VERSION = "1.16.1"
SEP = "\x1f"
MAX_ALBUMS = 3000
RADIO_FOLDER = "radio"          # a top-level folder of station files (.m3u): not an artist, left out of the music lists
_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
_ALBUM_CACHE_SECONDS = 120
UNKNOWN_LISTS = {"starred", "highest", "frequent", "byYear", "byGenre"}


class ApiError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


# ---- ids: a file or folder is named by its root and path, never by a number we would have to keep ---------------------------
def make_id(root: str, parts=()) -> str:
    raw = SEP.join((root, *parts)).encode("utf-8")
    return "p" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")      # the letter: never mistaken for a music folder number


def read_id(value: str):
    if not value.startswith("p"):
        raise ApiError(70, "The requested data was not found.")
    value = value[1:]
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise ApiError(70, "The requested data was not found.")
    root, *parts = raw.split(SEP)
    return root, tuple(parts)


# ---- answers --------------------------------------------------------------------------------------------------------------
KEEP_EMPTY = {"ignoredArticles"}                 # required by the API even when it is empty


def prune(value):
    """Leave out empty values (None and empty strings) so the answer holds only what is known."""
    if isinstance(value, dict):
        return {k: prune(v) for k, v in value.items() if v is not None and (v != "" or k in KEEP_EMPTY)}
    if isinstance(value, list):
        return [prune(v) for v in value]
    return value


def _text(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return _XML_BAD.sub("", str(value))


def _node(name: str, value: dict) -> str:
    attrs, kids = [], []
    for key, item in value.items():
        if isinstance(item, dict):
            kids.append(_node(key, item))
        elif isinstance(item, list):
            for one in item:
                kids.append(_node(key, one) if isinstance(one, dict) else "<%s>%s</%s>" % (key, escape(_text(one)), key))
        elif item is not None:
            attrs.append(" %s=%s" % (key, quoteattr(_text(item))))
    return "<%s%s>%s</%s>" % (name, "".join(attrs), "".join(kids), name)


def envelope(payload=None, error=None) -> dict:
    body = {"status": "failed" if error else "ok", "version": API_VERSION, "type": "pluteus", "serverVersion": __version__,
            "openSubsonic": True}
    if error:
        body["error"] = {"code": error.code, "message": error.message}
    elif payload:
        body.update(prune(payload))
    return body


def render(body: dict, fmt: str) -> Response:
    if fmt == "json":
        return Response(json.dumps({"subsonic-response": body}, ensure_ascii=False), media_type="application/json",
                        headers={"Cache-Control": "no-store"})
    xml = '<?xml version="1.0" encoding="UTF-8"?>' + _node("subsonic-response", {"xmlns": "http://subsonic.org/restapi", **body})
    return Response(xml, media_type="text/xml", headers={"Cache-Control": "no-store"})


_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_LEAD = re.compile(r"^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)")
_MD_MARKS = re.compile(r"(\*\*|__|\*|_|`|~~)")


def plain_text(markdown_text: str, limit: int = 4000) -> str:
    """A Markdown note as plain paragraphs for an app that shows a biography: no headings marks, links or emphasis, at most `limit` characters."""
    out = []
    for line in markdown_text.replace("\r\n", "\n").split("\n"):
        line = _MD_LEAD.sub("", _MD_LINK.sub(r"\1", _MD_IMAGE.sub("", line)))
        out.append(_MD_MARKS.sub("", line).strip())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return text[:limit].rstrip()


def _now_iso() -> str:
    """A timestamp for `starred`: the Favourites shelf keeps no star time a client could use, and apps only look for the field."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _letter(name: str) -> str:
    first = name[:1].upper()
    return first if first.isalpha() else "#"


class Subsonic:
    def __init__(self, cfg, lib, state, auth, meta, throttle):
        """throttle: object with wait(request, name) -> seconds, fail(request, name), ok(request, name)."""
        self.cfg, self.lib, self.state, self.auth, self.meta, self.throttle = cfg, lib, state, auth, meta, throttle
        self._pw_ok: dict = {}
        self._albums: dict = {}
        self._albums_lock = threading.Lock()
        self._radio: dict = {}
        self.relay_station = None             # set by the app when the relay is on: async (user, root, parts, number) -> Response
        self.handlers = {
            "ping": self.ping, "getlicense": self.license, "getopensubsonicextensions": self.extensions,
            "getmusicfolders": self.music_folders, "getindexes": self.indexes, "getmusicdirectory": self.music_directory,
            "getartists": self.artists, "getartist": self.artist, "getalbum": self.album, "getsong": self.song,
            "getalbumlist": self.album_list, "getalbumlist2": self.album_list2, "getrandomsongs": self.random_songs,
            "search2": self.search2, "search3": self.search3, "getcoverart": self.cover_art, "stream": self.stream,
            "download": self.download, "getuser": self.user_info, "getscanstatus": self.scan_status,
            "startscan": self.scan_status, "star": self.star, "unstar": self.unstar, "getstarred": self.starred,
            "getstarred2": self.starred2, "getlyricsbysongid": self.lyrics_by_id, "getinternetradiostations": self.radio_stations,
            "getartistinfo2": self.artist_info2, "getartistinfo": self.artist_info,
            "getplayqueue": self.get_play_queue, "saveplayqueue": self.save_play_queue,
        }
        self.empty = {
            "getplaylists": "playlists", "getlyrics": "lyrics", "getgenres": "genres",
            "getnowplaying": "nowPlaying",
            "getalbuminfo": "albumInfo", "getalbuminfo2": "albumInfo", "gettopsongs": "topSongs",
            "getsimilarsongs": "similarSongs", "getsimilarsongs2": "similarSongs2", "getpodcasts": "podcasts",
            "getbookmarks": "bookmarks", "getshares": "shares",
            "getsongsbygenre": "songsByGenre", "getvideos": "videos",
        }
        self.nothing = {"scrobble", "setrating", "updatenowplaying", "savebookmark", "deletebookmark"}

    # ---- entry point ---------------------------------------------------------------------------------------------------
    async def handle(self, request) -> Response:
        params = dict(request.query_params)
        ids = [v for k, v in request.query_params.multi_items() if k in ("id", "albumId", "artistId")]       # star sends several
        if request.method == "POST":
            raw = await read_limited(request, 16384)
            if raw is None:
                return render(envelope(error=ApiError(0, "The request is too large.")), "json" if params.get("f") == "json" else "xml")
            from urllib.parse import parse_qsl
            pairs = parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True)
            params.update(dict(pairs))
            ids += [v for k, v in pairs if k in ("id", "albumId", "artistId")]
        params["_ids"] = ids
        proxied = self.cfg.trust_proxy
        scheme = (request.headers.get("x-forwarded-proto", "") if proxied else "").split(",")[0].strip() or request.url.scheme
        host = (request.headers.get("x-forwarded-host", "") if proxied else "").split(",")[0].strip() or request.headers.get("host", "")
        params["_origin"] = ("%s://%s%s" % (scheme, host, self.cfg.base_path)) if host else ""
        fmt = "json" if params.get("f") == "json" else "xml"
        name = request.path_params["name"].lower()
        name = name[:-5] if name.endswith(".view") else name
        try:
            user = await self.authenticate(request, params)
            if name == "stream" and self.relay_station is not None and (target := self._station_target(params.get("id", ""))):
                return await self.relay_station(user, *target)
            if name in self.handlers:
                result = await run_in_threadpool(self.handlers[name], user, params)
                if isinstance(result, Response):
                    return result
                return render(envelope(result), fmt)
            if name in self.empty:
                return render(envelope({self.empty[name]: {}}), fmt)
            if name in self.nothing:
                return render(envelope(), fmt)
            raise ApiError(0, "This server does not support %s." % name)
        except ApiError as e:
            return render(envelope(error=e), fmt)
        except (PathError, NotFound):
            return render(envelope(error=ApiError(70, "The requested data was not found.")), fmt)
        except OSError:
            return render(envelope(error=ApiError(0, "The library storage is not available right now.")), fmt)

    # ---- login ---------------------------------------------------------------------------------------------------------
    async def authenticate(self, request, params):
        """Three kinds of login. A KEY (apiKey, or the salted token made from it) is a long random secret, so a wrong one counts
        against the address only and never locks a user name. A `p` password may be the key or the real password: the key is
        tried first (cheap), and only a wrong real-password attempt counts against the user name, like the web login."""
        key, name = params.get("apiKey", ""), params.get("u", "")
        token, salt, password = params.get("t", ""), params.get("s", ""), params.get("p", "")
        if key and (name or token or password):
            raise ApiError(43, "Multiple conflicting authentication mechanisms provided.")
        if not (key or (token and salt) or password):
            raise ApiError(10, "Required parameter is missing.")
        if key or (token and salt):
            wait = self.throttle.wait_key(request)
            if wait:
                raise ApiError(40, "Too many failed attempts. Try again in %d seconds." % wait)
            if key:
                user = await run_in_threadpool(self._user_for_key, key)
            else:
                user = await run_in_threadpool(self._check_token, name, token, salt)
            if user is None:
                self.throttle.fail_key(request)
                raise ApiError(44 if key else 40, "Invalid API key." if key else "Wrong username or password.")
            self.throttle.ok(request, user.name)
            return user
        user = await run_in_threadpool(self._check_key_password, name, password)
        if user is not None:
            self.throttle.ok(request, user.name)
            return user
        wait = self.throttle.wait(request, name)
        if wait:
            raise ApiError(40, "Too many failed attempts. Try again in %d seconds." % wait)
        user = await run_in_threadpool(self._check_real_password, name, password)
        if user is None:
            self.throttle.fail(request, name)
            raise ApiError(40, "Wrong username or password.")
        self.throttle.ok(request, user.name)
        return user

    @staticmethod
    def _same(a: str, b: str) -> bool:
        return hmac.compare_digest(a.encode("utf-8", "replace"), b.encode("utf-8", "replace"))

    def _user_for_key(self, key: str):
        found = None
        for username, secret in self.state.appkeys().items():
            if self._same(secret, key):
                found = self.auth.users.get(username)
        return found

    def _check_token(self, name: str, token: str, salt: str):
        """The salted-token login: t = md5(key + salt). An unknown name or a user without a key costs as much as a wrong token."""
        user = self.auth.users.get(name)
        secret = self.state.get_appkey(name) if user else None
        if user is None or secret is None:
            verify_password("x", self.auth._dummy)
            return None
        expected = hashlib.md5((secret + salt).encode("utf-8"), usedforsecurity=False).hexdigest()
        return user if self._same(expected, token.lower()) else None

    @staticmethod
    def _plain(password: str):
        """The legacy `p` value: plain, or `enc:` + hex."""
        if password.startswith("enc:"):
            try:
                return bytes.fromhex(password[4:]).decode("utf-8")
            except ValueError:
                return None
        return password

    def _check_key_password(self, name: str, password: str):
        """The user when the legacy `p` value is the user's key (no password hashing)."""
        user, password = self.auth.users.get(name), self._plain(password)
        secret = self.state.get_appkey(name) if (user and password is not None) else None
        return user if (secret is not None and self._same(secret, password)) else None

    def _check_real_password(self, name: str, password: str):
        user, password = self.auth.users.get(name), self._plain(password)
        if user is None or password is None:
            verify_password("x", self.auth._dummy)       # an unknown name costs as much as a wrong password
            return None
        cache_key = hashlib.sha256((name + SEP + password).encode("utf-8")).hexdigest()
        now = time.monotonic()
        if self._pw_ok.get(cache_key, 0) > now:
            return user
        if verify_password(password, user.password_hash):
            if len(self._pw_ok) > 500:
                self._pw_ok = {k: v for k, v in self._pw_ok.items() if v > now}
            self._pw_ok[cache_key] = now + self.cfg.auth_cache_seconds
            return user
        return None

    # ---- building blocks -------------------------------------------------------------------------------------------------
    def _roots(self, user, wanted: str = ""):
        roots = self.lib.roots_for(user)
        return [r for r in roots if not wanted or r.name == wanted]

    def _list(self, user, root: str, parts):
        entries = self.lib.list_dir(user, root, parts, True, True)
        dirs = [e for e in entries if e.is_dir and not (not parts and e.name.lower() == RADIO_FOLDER)]       # no station files among the artists
        songs = [e for e in entries if not e.is_dir and kinds.kind_of(e.name) == "audio"]
        return dirs, songs

    def _name(self, root: str, parts) -> str:
        return self.lib.display_name(parts[-1]) if parts else root

    def _song(self, root: str, entry, tags: dict) -> dict:
        parts = entry.parts
        folder = self._name(root, parts[:-1])
        ext = os.path.splitext(entry.name)[1].lower()
        duration = int(round(tags["duration"])) if tags.get("duration") else None
        return {
            "id": make_id(root, parts), "parent": make_id(root, parts[:-1]), "isDir": False,
            "title": tags.get("title") or os.path.splitext(entry.name)[0], "album": tags.get("album") or folder,
            "artist": tags.get("artist") or tags.get("albumartist") or (self.lib.display_name(parts[0]) if len(parts) > 2 else ""),
            "track": tags.get("track"), "discNumber": tags.get("disc"), "year": int(tags["year"]) if str(tags.get("year", "")).isdigit() else None,
            "genre": tags.get("genre"), "coverArt": make_id(root, parts[:-1]), "size": entry.size,
            "contentType": kinds.AUDIO.get(ext, "audio/mpeg"), "suffix": ext[1:], "duration": duration,
            "bitRate": tags.get("bitrate"), "path": "/".join(parts), "type": "music", "isVideo": False,
            "albumId": make_id(root, parts[:-1]), "artistId": make_id(root, parts[:1]) if len(parts) > 2 else None,
        }

    def _songs(self, root: str, entries, user=None) -> list:
        tags = self.meta.song_tags_many(entries)
        out = [self._song(root, e, t) for e, t in zip(entries, tags)]
        out.sort(key=lambda s: (s.get("discNumber") or 0, s.get("track") or 9999))
        if user is not None:
            self._mark_stars(user, root, out)
        return out

    def _mark_stars(self, user, root: str, items: list) -> None:
        """Items that are on the user's Favourites shelf carry `starred`, so the heart shows in the app."""
        paths = [i["path"] for i in items if i.get("path")]
        flags = self.state.shelf_flags(user.name, root, paths) if paths else {}
        for item in items:
            if "favourite" in flags.get(item.get("path"), ()):
                item["starred"] = _now_iso()

    def _dir_item(self, root: str, entry) -> dict:
        ident = make_id(root, entry.parts)
        return {"id": ident, "parent": make_id(root, entry.parts[:-1]), "isDir": True,
                "title": self._name(root, entry.parts), "coverArt": ident}

    def _album_item(self, root: str, entry, songs: int | None = None) -> dict:
        parts = entry.parts
        ident = make_id(root, parts)
        created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(clamp_time(getattr(entry, "mtime", 0) or 0)))
        return {"id": ident, "name": self._name(root, parts), "title": self._name(root, parts), "isDir": True,
                "artist": self.lib.display_name(parts[0]) if len(parts) > 1 else "", "artistId": make_id(root, parts[:1]) if len(parts) > 1 else None,
                "parent": make_id(root, parts[:-1]), "coverArt": ident, "songCount": songs or 0, "duration": 0, "created": created}

    def _grouped(self, items: list) -> list:
        """[{"name": letter, "artist": [...]}] for the index lists, in alphabetical order."""
        groups: dict = {}
        for item in items:
            groups.setdefault(_letter(item["name"]), []).append(item)
        return [{"name": k, "artist": sorted(v, key=lambda a: a["name"].lower())} for k, v in sorted(groups.items())]

    def _top_folders(self, user, wanted: str = "") -> list:
        counts: dict = {}
        for root, entry in self._all_albums(user):                    # how many albums each artist folder holds
            counts[(root, entry.parts[0])] = counts.get((root, entry.parts[0]), 0) + 1
        out = []
        for r in self._roots(user, wanted):
            try:
                dirs, _ = self._list(user, r.name, ())
            except (NotFound, OSError):
                continue
            out += [{"id": make_id(r.name, d.parts), "name": self.lib.display_name(d.name), "coverArt": make_id(r.name, d.parts),
                     "albumCount": counts.get((r.name, d.parts[0]), 0)} for d in dirs]
        return out

    def _all_albums(self, user) -> list:
        """[(root, entry)] of every album folder (the folders inside the top folders; a top folder with songs and no folders
        inside counts as an album itself). Kept for two minutes."""
        hit = self._albums.get(user.name)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        with self._albums_lock:                                      # several apps ask at once: one scan, the others wait for it
            hit = self._albums.get(user.name)
            now = time.monotonic()
            if hit and hit[0] > now:
                return hit[1]
            found, full = [], False
            for r in self._roots(user):
                if full:
                    break
                try:
                    top, _ = self._list(user, r.name, ())
                except (NotFound, OSError):
                    continue
                for artist in top:
                    try:
                        sub = self.lib.list_dir(user, r.name, artist.parts, True, True)      # with dates: read once, kept with the list
                    except (NotFound, OSError):
                        continue
                    albums = [e for e in sub if e.is_dir]
                    if albums:
                        found += [(r.name, e) for e in albums]
                    elif any(kinds.kind_of(e.name) == "audio" for e in sub if not e.is_dir):
                        found.append((r.name, artist))
                    if len(found) >= MAX_ALBUMS:
                        found, full = found[:MAX_ALBUMS], True
                        break
            fresh = {k: v for k, v in self._albums.items() if v[0] > now}         # a new dict: readers never see one half changed
            fresh[user.name] = (time.monotonic() + _ALBUM_CACHE_SECONDS, found)
            self._albums = fresh
            return found

    # ---- endpoints -------------------------------------------------------------------------------------------------------
    def ping(self, user, p):
        return {}

    def license(self, user, p):
        return {"license": {"valid": True, "email": "", "licenseExpires": "2099-01-01T00:00:00.000Z"}}

    def extensions(self, user, p):
        return {"openSubsonicExtensions": [{"name": "apiKeyAuthentication", "versions": [1]}, {"name": "songLyrics", "versions": [1]}]}

    # ---- internet radio: the station files (.m3u, .pls, .url) of a top-level Radio folder, in it and one folder down ---------
    def _stations(self, user) -> list:
        hit = self._radio.get(user.name)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        found = []
        for r in self._roots(user):
            try:
                top = self.lib.list_dir(user, r.name, (), True, True)
            except (NotFound, OSError):
                continue
            for folder in (e for e in top if e.is_dir and e.name.lower() == RADIO_FOLDER):
                try:
                    inside = self.lib.list_dir(user, r.name, folder.parts, True, True)
                    files = [e for e in inside if not e.is_dir]
                    for sub in (e for e in inside if e.is_dir):
                        files += [e for e in self.lib.list_dir(user, r.name, sub.parts, True, True) if not e.is_dir]
                except (NotFound, OSError):
                    continue
                for entry in sorted(files, key=lambda e: e.parts)[:300]:
                    if not radio.is_radio_file(entry.name) or len(found) >= radio.MAX_STATIONS:
                        continue
                    try:
                        path = self.lib.file_info(user, r.name, entry.parts, True).path
                    except (NotFound, PathError, OSError):
                        continue
                    stem = os.path.splitext(entry.name)[0]
                    stations = radio.read_file_streams(path)
                    for n, (name, url, stored) in enumerate(stations):
                        label = name or (stem if len(stations) == 1 else "%s %d" % (stem, n + 1))
                        found.append({"id": make_id(r.name, entry.parts) + ("" if len(stations) == 1 else ".%d" % n),
                                      "name": label, "streamUrl": url,
                                      "_relay": bool(stored) or url.lower().startswith("http:")})      # these play through the server when it offers that
        found = found[:radio.MAX_STATIONS]
        self._radio = {**{k: v for k, v in self._radio.items() if v[0] > time.monotonic()}, user.name: (time.monotonic() + _ALBUM_CACHE_SECONDS, found)}
        return found

    def radio_stations(self, user, p):
        origin = p.get("_origin", "") if self.relay_station is not None else ""
        login = {k: p[k] for k in ("apiKey",) if p.get(k)} or {k: p[k] for k in ("u", "t", "s") if p.get(k)}
        usable = bool(origin) and (("apiKey" in login) or all(k in login for k in ("u", "t", "s")))     # a plain `p` password is never copied into an address
        out = []
        for station in self._stations(user):
            item = {k: v for k, v in station.items() if k != "_relay"}
            if usable and station.get("_relay"):                # through the server: the same app login, no address of the station's own
                item["streamUrl"] = "%s/rest/stream?%s" % (origin, urlencode({"id": station["id"], **login, "c": "pluteus", "v": "1.16.1"}))
            out.append(item)
        return {"internetRadioStations": {"internetRadioStation": out}}

    def _station_target(self, value: str):
        """(root, parts, number) when `value` is the id of a station (a station file, with .N for the Nth station of a file), else None."""
        base, _, tail = value.rpartition(".")
        number = 0
        if base and tail.isdigit():
            value, number = base, int(tail)
        try:
            root, parts = read_id(value)
        except ApiError:
            return None
        return (root, parts, number) if parts and radio.is_radio_file(parts[-1]) else None

    # ---- artist information: the note about the artist folder (artist.md / artist.txt / about.md), no online look-up ----------
    _BIOGRAPHY_FILES = ("artist.md", "artist.txt", "about.md")

    def _biography(self, user, p) -> str:
        root, parts = read_id(p.get("id", ""))
        if not parts:
            return ""
        if root != UPLOADS_ROOT and self.lib.uploads_enabled(user):          # a note the user wrote themselves comes first
            for wanted in self._BIOGRAPHY_FILES:
                mine = notes.read(self.lib, user, root, parts, wanted)
                if mine and mine[0].strip():
                    return plain_text(mine[0])
        files = {e.name.lower(): e for e in self.lib.list_dir(user, root, parts, True, False) if not e.is_dir}
        for wanted in self._BIOGRAPHY_FILES:
            entry = files.get(wanted)
            if entry is None:
                continue
            try:
                info = self.lib.file_info(user, root, parts + (entry.name,), True)
                with open(info.path, "rb") as f:
                    raw = f.read(65537)
            except (NotFound, PathError, OSError):
                continue
            if len(raw) > 65536:
                continue
            return plain_text(textview.decode(raw, name=entry.name)[0])
        entry = files.get("artist.nfo")                                       # what Kodi, Plex and Lidarr write
        if entry is not None:
            try:
                with open(self.lib.file_info(user, root, parts + (entry.name,), True).path, "rb") as f:
                    return aboutfiles.summary("artist.nfo", f.read(aboutfiles.MAX_BYTES + 1)).replace("\n\n", "\n")
            except (NotFound, PathError, OSError):
                pass
        return ""

    def artist_info2(self, user, p):
        text = self._biography(user, p)
        return {"artistInfo2": {"biography": text} if text else {}}

    def artist_info(self, user, p):
        text = self._biography(user, p)
        return {"artistInfo": {"biography": text} if text else {}}

    # ---- stars: the user's Favourites shelf, the same list as the heart in the web pages ------------------------------------
    def _is_dir(self, user, root: str, parts) -> bool:
        try:
            self.lib.file_info(user, root, parts, True)
            return False
        except NotFound:
            self.lib.folder_path(user, root, parts)                   # raises NotFound when it is neither
            return True

    def _star(self, user, p, on: bool):
        if getattr(user, "read_only", False):                        # a read-only user changes nothing, not even this list
            return {}
        for ident in p.get("_ids", [])[:200]:
            try:
                root, parts = read_id(ident)
            except ApiError:
                continue                                              # a music folder number or junk
            if not parts or not self.lib.has_root(user, root):
                continue
            path = "/".join(parts)
            if not on:
                self.state.shelf_remove(user.name, "favourite", root, path)
                continue
            try:
                self.state.shelf_add(user.name, "favourite", root, path, self._is_dir(user, root, parts))
            except (NotFound, PathError, OSError):
                continue
        return {}

    def star(self, user, p):
        return self._star(user, p, True)

    def unstar(self, user, p):
        return self._star(user, p, False)

    def _starred(self, user) -> dict:
        songs, albums = [], []
        for row in self.state.shelf_list(user.name, "favourite")[:100]:          # newest first
            root, parts = row["root"], tuple(row["path"].split("/"))
            if not self.lib.has_root(user, root):
                continue
            try:
                if row["is_dir"]:
                    self.lib.folder_path(user, root, parts)
                    item = self._album_item(root, SimpleNamespace(parts=parts, mtime=0))
                    item["starred"] = _now_iso()
                    albums.append(item)
                else:
                    info = self.lib.file_info(user, root, parts, True)
                    if kinds.kind_of(info.name) != "audio":
                        continue
                    entry = SimpleNamespace(parts=parts, name=info.name, path=info.path, size=info.size, mtime=info.mtime)
                    item = self._song(root, entry, self.meta.song_tags(entry))
                    item["starred"] = _now_iso()
                    songs.append(item)
            except (NotFound, PathError, OSError):
                continue
        return {"artist": [], "album": albums, "song": songs}

    def starred(self, user, p):
        return {"starred": self._starred(user)}

    def starred2(self, user, p):
        return {"starred2": self._starred(user)}

    # ---- lyrics (OpenSubsonic songLyrics): timed lines from a .lrc file or the tag, else the plain text ---------------------
    def lyrics_by_id(self, user, p):
        root, parts = read_id(p.get("id", ""))
        info = self.lib.file_info(user, root, parts, True)
        if kinds.kind_of(info.name) != "audio":
            raise ApiError(70, "The requested data was not found.")
        tags = mediainfo.read_info(info.path)
        timed = mediainfo.find_lrc(info.path)
        if not timed and len(mediainfo.parse_lrc(tags.get("lyrics", ""))) >= 3:
            timed = mediainfo.parse_lrc(tags["lyrics"])
        if timed:
            lines = [{"start": int(round(seconds * 1000)), "value": words} for seconds, words in timed]
        elif tags.get("lyrics"):
            lines = [{"value": line} for line in tags["lyrics"].split("\n")]
        else:
            return {"lyricsList": {}}
        entry = {"displayArtist": tags.get("artist") or tags.get("albumartist") or "", "displayTitle": tags.get("title") or os.path.splitext(info.name)[0],
                 "lang": "xxx", "synced": bool(timed), "line": lines}
        return {"lyricsList": {"structuredLyrics": [entry]}}

    def scan_status(self, user, p):
        return {"scanStatus": {"scanning": False, "count": 0}}

    def music_folders(self, user, p):
        return {"musicFolders": {"musicFolder": [{"id": i, "name": r.name} for i, r in enumerate(self._roots(user), 1)]}}

    def _folder_root(self, user, value: str) -> str:
        """The library a music folder id (1, 2, ... in the order of getMusicFolders) or a folder id belongs to."""
        if value.isdigit():
            roots = self._roots(user)
            if 1 <= int(value) <= len(roots):
                return roots[int(value) - 1].name
            raise ApiError(70, "The requested data was not found.")
        return read_id(value)[0]

    def indexes(self, user, p):
        wanted = self._folder_root(user, p["musicFolderId"]) if p.get("musicFolderId") else ""
        artists = self._top_folders(user, wanted)
        files = []
        for r in self._roots(user, wanted):
            try:
                _, songs = self._list(user, r.name, ())
            except (NotFound, OSError):
                continue
            files += self._songs(r.name, songs)
        return {"indexes": {"ignoredArticles": "", "lastModified": int(time.time() * 1000),
                            "index": self._grouped(artists), "child": files}}

    def music_directory(self, user, p):
        if not p.get("id"):
            raise ApiError(10, "Required parameter is missing.")
        root, parts = (self._folder_root(user, p["id"]), ()) if p["id"].isdigit() else read_id(p["id"])
        if not self.lib.has_root(user, root):
            raise ApiError(70, "The requested data was not found.")
        dirs, songs = self._list(user, root, parts)
        children = [self._dir_item(root, d) for d in dirs] + self._songs(root, songs, user)
        return {"directory": {"id": p["id"], "parent": make_id(root, parts[:-1]) if parts else None,
                              "name": self._name(root, parts), "child": children}}

    def artists(self, user, p):
        return {"artists": {"ignoredArticles": "", "index": self._grouped(self._top_folders(user))}}

    def artist(self, user, p):
        root, parts = read_id(p.get("id", ""))
        dirs, songs = self._list(user, root, parts)
        albums = []
        for d in dirs:
            try:
                _, inside = self._list(user, root, d.parts)
            except (NotFound, OSError):
                inside = []
            albums.append(self._album_item(root, d, len(inside)))
        if not dirs and songs:
            albums.append(self._album_item(root, SimpleNamespace(parts=parts), len(songs)))
        name = self._name(root, parts)
        return {"artist": {"id": p["id"], "name": name, "coverArt": p["id"], "albumCount": len(albums), "album": albums}}

    def album(self, user, p):
        root, parts = read_id(p.get("id", ""))
        _, songs = self._list(user, root, parts)
        listed = self._songs(root, songs, user)
        starred = {"starred": _now_iso()} if parts and "favourite" in self.state.shelf_flags(user.name, root, ["/".join(parts)]).get("/".join(parts), ()) else {}
        return {"album": {**starred, "id": p["id"], "name": self._name(root, parts), "title": self._name(root, parts),
                          "artist": self.lib.display_name(parts[0]) if len(parts) > 1 else "",
                          "artistId": make_id(root, parts[:1]) if len(parts) > 1 else None, "coverArt": p["id"],
                          "songCount": len(listed), "duration": sum(s.get("duration") or 0 for s in listed),
                          "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(clamp_time(max((e.mtime for e in songs), default=0)))),
                          "year": next((s["year"] for s in listed if s.get("year")), None),
                          "genre": next((s["genre"] for s in listed if s.get("genre")), None), "song": listed}}

    def song(self, user, p):
        root, parts = read_id(p.get("id", ""))
        info = self.lib.file_info(user, root, parts, True)
        if kinds.kind_of(info.name) != "audio":
            raise ApiError(70, "The requested data was not found.")
        entry = SimpleNamespace(parts=parts, name=info.name, path=info.path, size=info.size, mtime=info.mtime)
        item = self._song(root, entry, self.meta.song_tags(entry))
        self._mark_stars(user, root, [item])
        return {"song": item}

    def _recent_albums(self, user) -> list:
        """[(root, folder entry)]: the folders of the audio files this user has a saved position in (the web player keeps one), newest first.
        Nothing is logged for this: it is the same list as "continue" on the home page."""
        out, seen = [], set()
        for row in self.state.recent_progress(user.name, 300):
            parts = tuple(row["path"].split("/"))
            if len(parts) < 2 or kinds.kind_of(parts[-1]) != "audio" or not self.lib.has_root(user, row["root"]):
                continue
            key = (row["root"], parts[:-1])
            if key in seen:
                continue
            seen.add(key)
            out.append((row["root"], SimpleNamespace(parts=parts[:-1], name=parts[-2], mtime=row["updated"])))
        return out

    # ---- the play queue (resume on another device): one saved queue per user, replaced by every savePlayQueue ------------------------
    def save_play_queue(self, user, p):
        ids = []
        for ident in p.get("_ids", [])[:500]:
            try:
                root, parts = read_id(ident)
            except ApiError:
                continue
            if parts and self.lib.has_root(user, root) and kinds.kind_of(parts[-1]) == "audio":
                ids.append(ident)
        current = p.get("current", "")
        try:
            position = max(0, min(int(p.get("position", "0")), 10 ** 10))
        except ValueError:
            position = 0
        if not ids:
            self.state.playqueue_delete(user.name)                    # an empty save clears it
            return {}
        self.state.playqueue_put(user.name, {"ids": ids, "current": current if current in ids else ids[0], "position": position,
                                             "by": re.sub(r"[^\w .-]", "", p.get("c", ""))[:40]})
        return {}

    def _resume_from_progress(self, user, newer_than: float):
        """A queue made from the web player's saved position: the unfinished audio file opened most recently (if later than `newer_than`),
        and the rest of its folder after it. None when there is none. Nothing is stored for this: the position is already kept for the web."""
        for row in self.state.recent_progress(user.name, 40):
            parts = tuple(row["path"].split("/"))
            if row["updated"] <= newer_than:
                return None                                           # the saved queue is newer than anything the web player did
            if len(parts) < 2 or kinds.kind_of(parts[-1]) != "audio" or row["percent"] >= 0.98 or passing_by(row["path"], row["locator"]) or not self.lib.has_root(user, row["root"]):
                continue
            match = re.fullmatch(r"t:(\d+(?:[.]\d+)?)", row["locator"] or "")
            try:
                _, songs = self._list(user, row["root"], parts[:-1])
            except (NotFound, PathError, OSError):
                continue
            items = self._songs(row["root"], songs, user)
            ident = make_id(row["root"], parts)
            at = next((i for i, item in enumerate(items) if item["id"] == ident), None)
            if at is None:
                continue
            return {"ids": [item["id"] for item in items[at:at + 500]], "current": ident,
                    "position": int(float(match.group(1)) * 1000) if match else 0, "by": "pluteus web player", "updated": row["updated"]}
        return None

    def get_play_queue(self, user, p):
        saved = self.state.playqueue_get(user.name)
        resumed = self._resume_from_progress(user, saved["updated"] if saved else 0.0)
        if resumed is not None:
            saved = resumed
        if not saved:
            return {"playQueue": {}}
        entries = []
        for ident in saved["ids"]:
            try:
                root, parts = read_id(ident)
                info = self.lib.file_info(user, root, parts, True)
            except (ApiError, NotFound, PathError, OSError):
                continue                                              # a file that is gone or no longer visible drops out
            entry = SimpleNamespace(parts=parts, name=info.name, path=info.path, size=info.size, mtime=info.mtime)
            item = self._song(root, entry, self.meta.song_tags(entry))
            self._mark_stars(user, root, [item])
            entries.append(item)
        if not entries:
            return {"playQueue": {}}
        current = saved["current"] if any(e["id"] == saved["current"] for e in entries) else entries[0]["id"]
        return {"playQueue": {"entry": entries, "current": current, "position": saved["position"], "username": user.name,
                              "changed": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(clamp_time(saved["updated"]))),
                              "changedBy": saved.get("by", "")}}

    def _sorted_albums(self, user, kind: str) -> list:
        if kind in UNKNOWN_LISTS:                                    # no play counts, stars, years or genres are kept: an honest empty list
            return []
        if kind == "recent":
            return self._recent_albums(user)
        albums = list(self._all_albums(user))
        if kind == "random":
            random.shuffle(albums)
        elif kind == "newest":
            albums.sort(key=lambda item: item[1].mtime, reverse=True)
        elif kind == "alphabeticalByArtist":
            albums.sort(key=lambda a: (a[1].parts[0].lower() if len(a[1].parts) > 1 else "", a[1].name.lower()))
        else:
            albums.sort(key=lambda a: self._name(a[0], a[1].parts).lower())
        return albums

    def _window(self, p) -> tuple:
        try:
            size = max(1, min(500, int(p.get("size", "10"))))
            offset = max(0, int(p.get("offset", "0")))
        except ValueError:
            size, offset = 10, 0
        return size, offset

    def album_list2(self, user, p):
        size, offset = self._window(p)
        albums = self._sorted_albums(user, p.get("type", "alphabeticalByName"))[offset:offset + size]
        return {"albumList2": {"album": [self._album_item(r, e) for r, e in albums]}}

    def album_list(self, user, p):
        size, offset = self._window(p)
        albums = self._sorted_albums(user, p.get("type", "alphabeticalByName"))[offset:offset + size]
        return {"albumList": {"album": [self._album_item(r, e) for r, e in albums]}}

    def random_songs(self, user, p):
        size, _ = self._window({"size": p.get("size", "10")})
        songs = []
        for r, e in self._sorted_albums(user, "random")[:12]:
            try:
                _, inside = self._list(user, r, e.parts)
            except (NotFound, OSError):
                continue
            songs += self._songs(r, inside)
            if len(songs) >= size * 3:
                break
        random.shuffle(songs)
        return {"randomSongs": {"song": songs[:size]}}

    @staticmethod
    def _paged(p, name: str, items: list) -> list:
        """items[offset:offset+count] for artistCount/artistOffset, albumCount/albumOffset, songCount/songOffset (default 20)."""
        try:
            count = max(0, min(500, int(p.get(name + "Count", "20"))))
            offset = max(0, int(p.get(name + "Offset", "0")))
        except ValueError:
            count, offset = 20, 0
        return items[offset:offset + count]

    def _search(self, user, p):
        """(artists, albums, songs) matching the query. An EMPTY query means everything (artists and albums; songs come with
        their albums): this is how Subsonic apps load the whole library."""
        query = re.sub(r"[\"*]", "", p.get("query", "")).strip().lower()
        artists = [a for a in self._top_folders(user) if query in a["name"].lower()]
        albums = [(r, e) for r, e in self._all_albums(user) if query in self._name(r, e.parts).lower()]
        albums.sort(key=lambda item: self._name(item[0], item[1].parts).lower())
        artists.sort(key=lambda a: a["name"].lower())
        if not query:
            return artists, albums, []
        songs = []
        for r, e in albums[:6]:
            try:
                _, inside = self._list(user, r, e.parts)
            except (NotFound, OSError):
                continue
            songs += self._songs(r, inside)
        return artists, albums, songs

    def search3(self, user, p):
        artists, albums, songs = self._search(user, p)
        return {"searchResult3": {"artist": self._paged(p, "artist", artists),
                                  "album": [self._album_item(r, e) for r, e in self._paged(p, "album", albums)],
                                  "song": self._paged(p, "song", songs)}}

    def search2(self, user, p):
        artists, albums, songs = self._search(user, p)
        return {"searchResult2": {"artist": self._paged(p, "artist", artists),
                                  "album": [self._album_item(r, e) for r, e in self._paged(p, "album", albums)],
                                  "song": self._paged(p, "song", songs)}}

    def user_info(self, user, p):
        roles = {r: False for r in ("adminRole", "settingsRole", "uploadRole", "playlistRole", "coverArtRole", "commentRole",
                                    "podcastRole", "jukeboxRole", "shareRole", "videoConversionRole")}
        return {"user": {"username": user.name, "scrobblingEnabled": False, **roles, "downloadRole": True, "streamRole": True,
                         "folder": list(range(1, len(self._roots(user)) + 1))}}

    def _file_response(self, user, p, attachment: bool):
        root, parts = read_id(p.get("id", ""))
        info = self.lib.file_info(user, root, parts, True)
        mime = kinds.INLINE_MIME.get(kinds.extension(info.name))
        if kinds.kind_of(info.name) not in ("audio", "video") or mime is None:
            raise ApiError(70, "The requested data was not found.")
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"}
        return FileResponse(info.path, media_type=mime, headers=headers, filename=info.name if attachment else None,
                            content_disposition_type="attachment" if attachment else "inline")

    def stream(self, user, p):
        return self._file_response(user, p, False)

    def download(self, user, p):
        return self._file_response(user, p, True)

    def cover_art(self, user, p):
        root, parts = read_id(p.get("id", ""))
        found = None
        if parts and kinds.kind_of(parts[-1]) == "audio":
            found = mediainfo.read_cover(self.lib.file_info(user, root, parts, True).path)
        else:
            found = self._folder_art(user, root, parts, 1)
        if found is None:
            return PlainTextResponse("No cover", 404)
        data, mime = found
        return Response(data, media_type=mime, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"})

    def _folder_art(self, user, root: str, parts, depth: int):
        dirs, songs = self._list(user, root, parts)
        if songs:
            found = mediainfo.read_folder_cover(os.path.dirname(songs[0].path))
            if found:
                return found
            for song in songs[:3]:
                found = mediainfo.read_cover(song.path)
                if found:
                    return found
        if depth > 0:
            for d in dirs[:5]:
                try:
                    found = self._folder_art(user, root, d.parts, depth - 1)
                except (NotFound, OSError):
                    continue
                if found:
                    return found
        return None

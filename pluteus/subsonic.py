"""A read-only subset of the Subsonic / OpenSubsonic API, so that existing music apps (and Android Auto through them) can
browse and play the audio files of the library.

Turned on with `subsonic = true` in [server]. The API lives under /rest/NAME(.view). What is supported: ping, folders,
folder browsing (getIndexes / getMusicDirectory), the tag-style browsing most apps use (getArtists / getArtist / getAlbum,
read as Artist folder / Album folder / songs), album lists, a simple search, cover art, stream and download. Everything else
that an app may ask for at start-up (playlists, starred, genres, ...) is answered with an empty result.

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
import random
import re
import time
from types import SimpleNamespace
from xml.sax.saxutils import escape, quoteattr

from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, PlainTextResponse, Response

from . import __version__, kinds, mediainfo
from .auth import verify_password
from .fs import NotFound, PathError

API_VERSION = "1.16.1"
SEP = "\x1f"
MAX_ALBUMS = 3000
_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
_ALBUM_CACHE_SECONDS = 120


class ApiError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


# ---- ids: a file or folder is named by its root and path, never by a number we would have to keep ---------------------------
def make_id(root: str, parts=()) -> str:
    raw = SEP.join((root, *parts)).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def read_id(value: str):
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise ApiError(70, "The requested data was not found.")
    root, *parts = raw.split(SEP)
    return root, tuple(parts)


# ---- answers --------------------------------------------------------------------------------------------------------------
def prune(value):
    """Leave out empty values (None and empty strings) so the answer holds only what is known."""
    if isinstance(value, dict):
        return {k: prune(v) for k, v in value.items() if v is not None and v != ""}
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


def _letter(name: str) -> str:
    first = name[:1].upper()
    return first if first.isalpha() else "#"


class Subsonic:
    def __init__(self, cfg, lib, state, auth, meta, throttle):
        """throttle: object with wait(request, name) -> seconds, fail(request, name), ok(request, name)."""
        self.cfg, self.lib, self.state, self.auth, self.meta, self.throttle = cfg, lib, state, auth, meta, throttle
        self._pw_ok: dict = {}
        self._albums: dict = {}
        self.handlers = {
            "ping": self.ping, "getlicense": self.license, "getopensubsonicextensions": self.extensions,
            "getmusicfolders": self.music_folders, "getindexes": self.indexes, "getmusicdirectory": self.music_directory,
            "getartists": self.artists, "getartist": self.artist, "getalbum": self.album, "getsong": self.song,
            "getalbumlist": self.album_list, "getalbumlist2": self.album_list2, "getrandomsongs": self.random_songs,
            "search2": self.search2, "search3": self.search3, "getcoverart": self.cover_art, "stream": self.stream,
            "download": self.download, "getuser": self.user_info, "getscanstatus": self.scan_status,
            "startscan": self.scan_status,
        }
        self.empty = {
            "getplaylists": "playlists", "getstarred": "starred", "getstarred2": "starred2", "getgenres": "genres",
            "getnowplaying": "nowPlaying", "getartistinfo": "artistInfo", "getartistinfo2": "artistInfo2",
            "getalbuminfo": "albumInfo", "getalbuminfo2": "albumInfo", "gettopsongs": "topSongs",
            "getsimilarsongs": "similarSongs", "getsimilarsongs2": "similarSongs2", "getpodcasts": "podcasts",
            "getinternetradiostations": "internetRadioStations", "getbookmarks": "bookmarks", "getshares": "shares",
            "getplayqueue": "playQueue", "getsongsbygenre": "songsByGenre", "getvideos": "videos",
        }
        self.nothing = {"scrobble", "star", "unstar", "setrating", "updatenowplaying", "savebookmark", "deletebookmark",
                        "saveplayqueue"}

    # ---- entry point ---------------------------------------------------------------------------------------------------
    async def handle(self, request) -> Response:
        params = dict(request.query_params)
        if request.method == "POST":
            try:
                body = (await request.body())[:16384].decode("utf-8", "replace")
            except Exception:                           # noqa: BLE001
                body = ""
            from urllib.parse import parse_qsl
            params.update(dict(parse_qsl(body, keep_blank_values=True)))
        fmt = "json" if params.get("f") == "json" else "xml"
        name = request.path_params["name"].lower()
        name = name[:-5] if name.endswith(".view") else name
        try:
            user = await self.authenticate(request, params)
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
        key, name = params.get("apiKey", ""), params.get("u", "")
        token, salt, password = params.get("t", ""), params.get("s", ""), params.get("p", "")
        if key and (name or token or password):
            raise ApiError(43, "Multiple conflicting authentication mechanisms provided.")
        who = name or ("(api key)" if key else "")
        if not (key or (token and salt) or password):
            raise ApiError(10, "Required parameter is missing.")
        wait = self.throttle.wait(request, who)
        if wait:
            raise ApiError(40, "Too many failed attempts. Try again in %d seconds." % wait)
        user = None
        if key:
            user = await run_in_threadpool(self._user_for_key, key)
            if user is None:
                self.throttle.fail(request, who)
                raise ApiError(44, "Invalid API key.")
        else:
            user = await run_in_threadpool(self._check_login, name, token, salt, password)
            if user is None:
                self.throttle.fail(request, who)
                raise ApiError(40, "Wrong username or password.")
        self.throttle.ok(request, user.name)
        return user

    def _user_for_key(self, key: str):
        found = None
        for username, secret in self.state.appkeys().items():
            if hmac.compare_digest(secret.encode("utf-8"), key.encode("utf-8")):
                found = self.auth.users.get(username)
        return found

    def _check_login(self, name: str, token: str, salt: str, password: str):
        user = self.auth.users.get(name)
        secret = self.state.get_appkey(name) if user else None
        if user is None:
            verify_password(password or "x", self.auth._dummy)       # an unknown name costs as much as a wrong password
            return None
        if token and salt:
            if secret is None:
                return None
            expected = hashlib.md5((secret + salt).encode("utf-8"), usedforsecurity=False).hexdigest()
            return user if hmac.compare_digest(expected, token.lower()) else None
        if password.startswith("enc:"):
            try:
                password = bytes.fromhex(password[4:]).decode("utf-8")
            except ValueError:
                return None
        if secret is not None and hmac.compare_digest(secret.encode("utf-8"), password.encode("utf-8")):
            return user
        cache_key = hashlib.sha256((name + SEP + password).encode("utf-8")).hexdigest()
        now = time.monotonic()
        if self._pw_ok.get(cache_key, 0) > now:
            return user
        if verify_password(password, user.password_hash):               # the real password, legacy login
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
        dirs = [e for e in entries if e.is_dir]
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

    def _songs(self, root: str, entries) -> list:
        tags = self.meta.song_tags_many(entries)
        out = [self._song(root, e, t) for e, t in zip(entries, tags)]
        out.sort(key=lambda s: (s.get("discNumber") or 0, s.get("track") or 9999))
        return out

    def _dir_item(self, root: str, entry) -> dict:
        ident = make_id(root, entry.parts)
        return {"id": ident, "parent": make_id(root, entry.parts[:-1]), "isDir": True,
                "title": self._name(root, entry.parts), "coverArt": ident}

    def _album_item(self, root: str, entry, songs: int | None = None) -> dict:
        parts = entry.parts
        ident = make_id(root, parts)
        return {"id": ident, "name": self._name(root, parts), "title": self._name(root, parts), "isDir": True,
                "artist": self.lib.display_name(parts[0]) if len(parts) > 1 else "", "artistId": make_id(root, parts[:1]) if len(parts) > 1 else None,
                "parent": make_id(root, parts[:-1]), "coverArt": ident, "songCount": songs}

    def _grouped(self, items: list) -> list:
        """[{"name": letter, "artist": [...]}] for the index lists, in alphabetical order."""
        groups: dict = {}
        for item in items:
            groups.setdefault(_letter(item["name"]), []).append(item)
        return [{"name": k, "artist": sorted(v, key=lambda a: a["name"].lower())} for k, v in sorted(groups.items())]

    def _top_folders(self, user, wanted: str = "") -> list:
        out = []
        for r in self._roots(user, wanted):
            try:
                dirs, _ = self._list(user, r.name, ())
            except (NotFound, OSError):
                continue
            out += [{"id": make_id(r.name, d.parts), "name": self.lib.display_name(d.name), "coverArt": make_id(r.name, d.parts)} for d in dirs]
        return out

    def _all_albums(self, user) -> list:
        """[(root, entry)] of every album folder (the folders inside the top folders; a top folder with songs and no folders
        inside counts as an album itself). Kept for two minutes."""
        hit = self._albums.get(user.name)
        now = time.monotonic()
        if hit and hit[0] > now:
            return hit[1]
        found = []
        for r in self._roots(user):
            try:
                top, _ = self._list(user, r.name, ())
            except (NotFound, OSError):
                continue
            for artist in top:
                try:
                    sub = self.lib.list_dir(user, r.name, artist.parts, True, False)
                except (NotFound, OSError):
                    continue
                albums = [e for e in sub if e.is_dir]
                if albums:
                    found += [(r.name, e) for e in albums]
                elif any(kinds.kind_of(e.name) == "audio" for e in sub if not e.is_dir):
                    found.append((r.name, artist))
                if len(found) >= MAX_ALBUMS:
                    break
        self._albums = {k: v for k, v in self._albums.items() if v[0] > now}
        self._albums[user.name] = (now + _ALBUM_CACHE_SECONDS, found)
        return found

    # ---- endpoints -------------------------------------------------------------------------------------------------------
    def ping(self, user, p):
        return {}

    def license(self, user, p):
        return {"license": {"valid": True, "email": "", "licenseExpires": "2099-01-01T00:00:00.000Z"}}

    def extensions(self, user, p):
        return {"openSubsonicExtensions": [{"name": "apiKeyAuthentication", "versions": [1]}]}

    def scan_status(self, user, p):
        return {"scanStatus": {"scanning": False, "count": 0}}

    def music_folders(self, user, p):
        return {"musicFolders": {"musicFolder": [{"id": make_id(r.name), "name": r.name} for r in self._roots(user)]}}

    def indexes(self, user, p):
        wanted = read_id(p["musicFolderId"])[0] if p.get("musicFolderId") else ""
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
        root, parts = read_id(p["id"])
        if not self.lib.has_root(user, root):
            raise ApiError(70, "The requested data was not found.")
        dirs, songs = self._list(user, root, parts)
        children = [self._dir_item(root, d) for d in dirs] + self._songs(root, songs)
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
        listed = self._songs(root, songs)
        return {"album": {"id": p["id"], "name": self._name(root, parts), "title": self._name(root, parts),
                          "artist": self.lib.display_name(parts[0]) if len(parts) > 1 else "",
                          "artistId": make_id(root, parts[:1]) if len(parts) > 1 else None, "coverArt": p["id"],
                          "songCount": len(listed), "duration": sum(s.get("duration") or 0 for s in listed),
                          "year": next((s["year"] for s in listed if s.get("year")), None),
                          "genre": next((s["genre"] for s in listed if s.get("genre")), None), "song": listed}}

    def song(self, user, p):
        root, parts = read_id(p.get("id", ""))
        info = self.lib.file_info(user, root, parts, True)
        if kinds.kind_of(info.name) != "audio":
            raise ApiError(70, "The requested data was not found.")
        entry = SimpleNamespace(parts=parts, name=info.name, path=info.path, size=info.size, mtime=info.mtime)
        return {"song": self._song(root, entry, self.meta.song_tags(entry))}

    def _sorted_albums(self, user, kind: str) -> list:
        albums = list(self._all_albums(user))
        if kind == "random":
            random.shuffle(albums)
        elif kind in ("newest", "recent"):
            def when(item):
                try:
                    return os.path.getmtime(item[1].src or "")
                except OSError:
                    return 0.0
            albums.sort(key=when, reverse=True)
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

    def _search(self, user, p):
        query = re.sub(r"[\"*]", "", p.get("query", "")).strip().lower()
        if not query:
            return [], [], []
        artists = [a for a in self._top_folders(user) if query in a["name"].lower()]
        albums = [(r, e) for r, e in self._all_albums(user) if query in self._name(r, e.parts).lower()]
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
        return {"searchResult3": {"artist": artists[:20], "album": [self._album_item(r, e) for r, e in albums[:20]],
                                  "song": songs[:50]}}

    def search2(self, user, p):
        artists, albums, songs = self._search(user, p)
        return {"searchResult2": {"artist": artists[:20], "album": [self._album_item(r, e) for r, e in albums[:20]],
                                  "song": songs[:50]}}

    def user_info(self, user, p):
        roles = {r: False for r in ("adminRole", "settingsRole", "uploadRole", "playlistRole", "coverArtRole", "commentRole",
                                    "podcastRole", "jukeboxRole", "shareRole", "videoConversionRole")}
        return {"user": {"username": user.name, "scrobblingEnabled": False, **roles, "downloadRole": True, "streamRole": True,
                         "folder": [make_id(r.name) for r in self._roots(user)]}}

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

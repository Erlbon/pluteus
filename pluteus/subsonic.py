"""A read-only subset of the Subsonic / OpenSubsonic API, so that existing music apps (and Android Auto through them) can
browse and play the audio files of the library.

Turned on with `subsonic = true` in [server]. Music apps see only the libraries ticked for them on the admin page
(`subsonic = false` on a [[roots]] entry and `subsonic_uploads = false` leave one out), and only where the user may open them. The API lives under /rest/NAME(.view). What is supported: ping, folders,
folder browsing (getIndexes / getMusicDirectory), the tag-style browsing most apps use (getArtists / getArtist / getAlbum,
read from the folders: the artists are the folders at the library's artist_level, their albums the folders inside them that hold
songs), album lists, a simple search, cover art, stream and download. Everything else
that an app may ask for at start-up (playlists, genres, ...) is answered with an empty result. Stars are the user's
Favourites shelf (star, unstar, getStarred2, the `starred` album list, and `starred` with the time of the star on songs, albums and
artists; a starred top folder is an artist), and lyrics come as OpenSubsonic songLyrics. `musicFolderId` limits every list that takes it,
and a cover asked for with `size` is scaled down (Pillow, cached with the /thumb pictures). A saved play queue
(savePlayQueue / getPlayQueue, one per user) lets another device resume, and `recent` albums come from the saved positions.

Login: every user may have ONE app key (made on the admin page). It works as an OpenSubsonic API key (`apiKey`), as the
password in the salted-token login (`u`, `t`, `s`) and as the legacy `p` password. The real password is also accepted as `p`.
The key opens nothing but this read-only API. Failed logins count against the same throttle as the web login.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import itertools
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

from . import __version__, aboutfiles, kinds, markdown, mediainfo, notes, radio, textview, thumbs
from .auth import verify_password
from .bodies import read_limited
from .config import MAX_ARTIST_LEVEL, UPLOADS_ROOT
from .fs import NotFound, PathError, check_segment, clamp_time, longpath, natural_key
from .safetext import clean
from .state import passing_by

API_VERSION = "1.16.1"
SEP = "\x1f"
LAYOUT_NOTE = "about.md"        # its front matter may say how the folders below are arranged: `layout: letter/artist/album`
ALBUM_NOTES = ("album.md", "album.nfo")      # a folder with one of these is an album, also when its songs sit two folders down (a box set)
_LAYOUT_SECONDS = 60
RADIO_FOLDER = "radio"          # a top-level folder of station files (.m3u): not an artist, left out of the music lists
_ALBUM_CACHE_SECONDS = 120
MAX_RUNS = 64                   # paging places kept at once (one per user, library, app and list); the oldest goes first
MAX_ID = 8192                   # characters of an id: far more than the longest path gives
UNKNOWN_LISTS = {"highest", "frequent", "byYear", "byGenre", "newest"}      # each would need every folder opened
_DUMMY_SECRET = "no app key: this text only makes a wrong name cost as much as a wrong token"


def _layout_names(windows: bool) -> tuple:
    """The spellings of about.md that are tried. Windows file systems ignore letter case, so one open finds any of them there."""
    return (LAYOUT_NOTE,) if windows else (LAYOUT_NOTE, LAYOUT_NOTE.capitalize(), LAYOUT_NOTE.upper())


_LAYOUT_NAMES = _layout_names(os.name == "nt")


def is_number(value: str) -> bool:
    """A plain number as an app sends it (a music folder, a size, a station of a file). str.isdigit() alone also says yes to characters
    such as a superscript two, which int() refuses, and int() refuses thousands of digits."""
    return value.isascii() and value.isdigit() and len(value) <= 9


def _storable(text: str) -> bool:
    """Whether the text can be written as UTF-8 (the state database and the tag cache refuse a name that is not valid Unicode)."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


class ApiError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


# ---- ids: a file or folder is named by its root and path, never by a number we would have to keep ---------------------------
def make_id(root: str, parts=()) -> str:
    # surrogatepass: a file name that is not valid Unicode (Python hands its odd bytes over as lone surrogates) still gets an id that
    # leads back to exactly that file. The id itself is plain ASCII.
    raw = SEP.join((root, *parts)).encode("utf-8", "surrogatepass")
    return "p" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")      # the letter: never mistaken for a music folder number


def read_id(value: str):
    if not value.startswith("p") or len(value) > MAX_ID:
        raise ApiError(70, "The requested data was not found.")
    value = value[1:]
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8", "surrogatepass")
    except ValueError:                                # also what a text that is not UTF-8 raises
        raise ApiError(70, "The requested data was not found.")
    root, *parts = raw.split(SEP)
    return root, tuple(parts)


# ---- answers --------------------------------------------------------------------------------------------------------------
KEEP_EMPTY = {"ignoredArticles"}                 # required by the API even when it is empty


def prune(value):
    """Leave out empty values (None and empty strings) so the answer holds only what is known. Every text is made safe to send on the
    way (safetext.clean): a file name or a tag with a control character, or one that is not valid Unicode, must not break a whole list."""
    if isinstance(value, dict):
        return {k: prune(v) for k, v in value.items() if v is not None and (v != "" or k in KEEP_EMPTY)}
    if isinstance(value, list):
        return [prune(v) for v in value]
    if isinstance(value, str):
        return clean(value)
    return value


def _text(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return clean(str(value))


XML_ELEMENTS = {"biography", "notes"}            # long texts the XML schema wants as child elements, not attributes
XML_TEXT = {"line": "value"}                     # a lyric line's words are the text of its element


def _node(name: str, value: dict) -> str:
    attrs, kids, text = [], [], ""
    for key, item in value.items():
        if isinstance(item, dict):
            kids.append(_node(key, item))
        elif isinstance(item, list):
            for one in item:
                kids.append(_node(key, one) if isinstance(one, dict) else "<%s>%s</%s>" % (key, escape(_text(one)), key))
        elif item is None:
            continue
        elif XML_TEXT.get(name) == key:
            text = escape(_text(item))
        elif key in XML_ELEMENTS:
            kids.append("<%s>%s</%s>" % (key, escape(_text(item)), key))
        else:
            attrs.append(" %s=%s" % (key, quoteattr(_text(item))))
    return "<%s%s>%s%s</%s>" % (name, "".join(attrs), text, "".join(kids), name)


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


def _iso(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(clamp_time(seconds or 0)))


def layout_depth(value) -> int | None:
    """How many folders down the artists are, from a `layout:` value such as `letter/artist/album` (2), `country/artist/album` (2),
    `artist/album` (1) or `album` (1: each album folder is its own artist, for compilations). None when it names neither."""
    if not isinstance(value, str):
        return None
    levels = [w.strip().lower().rstrip("s") for w in value.split("/") if w.strip()]
    for word in ("artist", "album"):
        if word in levels:
            return min(levels.index(word) + 1, MAX_ARTIST_LEVEL)      # no deeper than artist_level may be set in the config
    return None


def _letter(name: str) -> str:
    first = name[:1].upper()
    return first if first.isalpha() else "#"


class Subsonic:
    def __init__(self, cfg, lib, state, auth, meta, throttle):
        """throttle: object with wait(request, name) -> seconds, fail(request, name), ok(request, name)."""
        self.cfg, self.lib, self.state, self.auth, self.meta, self.throttle = cfg, lib, state, auth, meta, throttle
        self._pw_ok: dict = {}
        self._layouts: dict = {}              # (library, folder) -> (artist depth from its about.md or None, until): a minute at most
        self._runs: dict = {}                 # (user, library, app, list) -> where a paging app stopped: see _album_run
        self._runs_lock = threading.Lock()
        self._radio: dict = {}
        self.relay_station = None             # set by the app when the relay is on: async (user, root, parts, number) -> Response
        self.thumb_cache = thumbs.ThumbCache()                 # the app hands over its own cache and gate, shared with /thumb
        self.thumb_gate = threading.BoundedSemaphore(3)
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
            "getplayqueue": self.get_play_queue, "saveplayqueue": self.save_play_queue, "tokeninfo": self.token_info,
        }
        self.empty = {
            "getplaylists": "playlists", "getlyrics": "lyrics", "getgenres": "genres",
            "getnowplaying": "nowPlaying",
            "getalbuminfo": "albumInfo", "getalbuminfo2": "albumInfo", "gettopsongs": "topSongs",
            "getsimilarsongs": "similarSongs", "getsimilarsongs2": "similarSongs2", "getpodcasts": "podcasts",
            "getbookmarks": "bookmarks", "getshares": "shares",
            "getsongsbygenre": "songsByGenre", "getvideos": "videos",
        }
        self.nothing = {"scrobble", "setrating", "updatenowplaying", "createbookmark", "deletebookmark"}

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
        if name == "getopensubsonicextensions":                       # public by the OpenSubsonic rules: an app asks before it logs in
            return render(envelope(self.extensions(None, params)), fmt)
        try:
            user = await self.authenticate(request, params)
            await run_in_threadpool(self._check_ids, user, name, params)
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
        except (PathError, NotFound, ValueError):                     # ValueError: a number or a name in the request that nothing can use
            return render(envelope(error=ApiError(70, "The requested data was not found.")), fmt)
        except OSError:
            return render(envelope(error=ApiError(0, "The library storage is not available right now.")), fmt)

    # ---- login ---------------------------------------------------------------------------------------------------------
    async def authenticate(self, request, params):
        """Three kinds of login. A KEY (apiKey, or the salted token made from it) is a long random secret, so a wrong one counts
        against the address only and never locks a user name. A `p` password may be the key or the real password: the key is
        tried first (cheap), and only a wrong real-password attempt counts against the user name, like the web login. Only a
        verified real password clears the failures counted against a name: a key login never does, or a playing music app
        would keep unlocking its user for password guesses."""
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
            return user
        user = await run_in_threadpool(self._check_key_password, name, password)
        if user is not None:
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
        """The salted-token login: t = md5(key + salt). An unknown name and a user without a key do the same work as a wrong token
        (one look-up, one md5 of a fixed dummy secret, one comparison), so the answer time does not tell which users have a key, and
        no password hashing can be set off from outside."""
        user = self.auth.users.get(name)
        secret = self.state.get_appkey(name)
        real = user is not None and secret is not None
        expected = hashlib.md5(((secret if real else _DUMMY_SECRET) + salt).encode("utf-8", "replace"), usedforsecurity=False).hexdigest()
        same = self._same(expected, token.lower())
        return user if (same and real) else None

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
        cache_key = hashlib.sha256((name + SEP + password).encode("utf-8", "replace")).hexdigest()
        now = time.monotonic()
        until, proved = self._pw_ok.get(cache_key, (0, ""))
        if until > now and hmac.compare_digest(proved, user.password_hash):      # proved against the hash the user has NOW: a changed
            return user                                                          # password stops working at once
        if verify_password(password, user.password_hash):
            if len(self._pw_ok) > 500:
                self._pw_ok = {k: v for k, v in self._pw_ok.items() if v[0] > now}
            self._pw_ok[cache_key] = (now + self.cfg.auth_cache_seconds, user.password_hash)
            return user
        return None

    # ---- building blocks -------------------------------------------------------------------------------------------------
    def _shown(self, root) -> bool:
        """Whether music apps see this library (the Subsonic ticks on the admin page's Libraries tab)."""
        return self.lib.cfg.subsonic_uploads if root.name == UPLOADS_ROOT else root.subsonic

    def _roots(self, user, wanted: str = ""):
        """The libraries this user may open AND music apps may see; only `wanted` when one is given."""
        return [r for r in self.lib.roots_for(user) if self._shown(r) and (not wanted or r.name == wanted)]

    def _has_root(self, user, root: str) -> bool:
        return any(r.name == root for r in self._roots(user))

    MANY_IDS = {"star", "unstar", "saveplayqueue"}                    # these drop an id they may not use and keep the rest

    def _check_ids(self, user, name: str, params) -> None:
        """An id that points into a library music apps may not see is "not found" (junk ids are left to the call)."""
        if name in self.MANY_IDS:
            return
        for value in params.get("_ids", ()):
            target = self._station_target(value)
            try:
                root = target[0] if target else read_id(value)[0]
            except ApiError:
                continue
            if not self._has_root(user, root):
                raise ApiError(70, "The requested data was not found.")

    def _folder(self, user, root: str, parts, stat: bool):
        """(folders, songs, names of the other files in lower case) of one folder. `stat`: the songs come with their size and date (one
        more system call per song: say False wherever only names or a count are used). The folders never do: where an album's date is
        shown, _dated reads it for the few folders that are."""
        entries = self.lib.list_dir(user, root, parts, True, False)
        dirs = [e for e in entries if e.is_dir and not (not parts and e.name.lower() == RADIO_FOLDER)]       # no station files among the artists
        songs = [e for e in entries if not e.is_dir and kinds.kind_of(e.name) == "audio"]
        if stat and songs:
            songs = self.lib.with_stats(songs)
        return dirs, songs, {e.name.lower() for e in entries if not e.is_dir}

    def _list(self, user, root: str, parts, stat: bool):
        return self._folder(user, root, parts, stat)[:2]

    def _dated(self, entries: list) -> list:
        """The same folder entries with their dates read (an album's `created`); one that vanished meanwhile drops out."""
        return self.lib.with_stats(entries) if entries else []

    def _name(self, root: str, parts) -> str:
        return self.lib.display_name(parts[-1]) if parts else root

    def _song(self, root: str, entry, tags: dict) -> dict:
        parts = entry.parts
        folder = self._name(root, parts[:-1])
        artist = self._artist_of(root, parts[:-1])
        ext = os.path.splitext(entry.name)[1].lower()
        duration = int(round(tags["duration"])) if tags.get("duration") else None
        return {
            "id": make_id(root, parts), "parent": make_id(root, parts[:-1]), "isDir": False,
            "title": tags.get("title") or os.path.splitext(entry.name)[0], "album": tags.get("album") or folder,
            "artist": tags.get("artist") or tags.get("albumartist") or (self._name(root, artist) if artist else ""),
            "track": tags.get("track"), "discNumber": tags.get("disc"), "year": int(tags["year"]) if is_number(str(tags.get("year", ""))) else None,
            "genre": tags.get("genre"), "coverArt": make_id(root, parts), "size": entry.size,
            "contentType": kinds.AUDIO.get(ext, "audio/mpeg"), "suffix": ext[1:], "duration": duration,
            "bitRate": tags.get("bitrate"), "path": "/".join(parts), "type": "music", "isVideo": False,
            "albumId": make_id(root, parts[:-1]), "artistId": make_id(root, artist) if artist else None,
        }

    def _tags(self, entry) -> dict:
        """The tags of one song. A path that is not valid Unicode cannot be a key of the tag cache, so such a file is read each time."""
        return self.meta.song_tags(entry) if _storable(entry.path) else mediainfo.song_info(entry.path)

    def _tags_many(self, entries) -> list:
        entries = list(entries)
        if all(_storable(e.path) for e in entries):
            return self.meta.song_tags_many(entries)
        return [self._tags(e) for e in entries]

    @staticmethod
    def _play_order(pair) -> tuple:
        """Disc, then the folder the song is in (CD1 before CD2 before CD10), then the track: a box set whose songs carry track numbers but
        no disc numbers plays disc by disc, not every first track and then every second."""
        entry, song = pair
        return (song.get("discNumber") or 0, [natural_key(part) for part in entry.parts[:-1]], song.get("track") or 9999)

    def _songs(self, root: str, entries, user=None) -> list:
        entries = list(entries)
        tags = self._tags_many(entries)
        pairs = [(e, self._song(root, e, t)) for e, t in zip(entries, tags)]
        pairs.sort(key=self._play_order)
        out = [song for _entry, song in pairs]
        if user is not None:
            self._mark_stars(user, out)
        return out

    def _mark_stars(self, user, items: list) -> list:
        """Items (songs, albums, artists) that are on the user's Favourites shelf carry `starred`, the time they were put there,
        so the heart shows in the app. The library and path are read from each item's id."""
        by_root: dict = {}
        for item in items:
            try:
                root, parts = read_id(item.get("id", ""))
            except ApiError:
                continue
            if parts and _storable("/".join(parts)):                  # a name the shelf cannot hold was never starred
                by_root.setdefault(root, []).append(("/".join(parts), item))
        for root, pairs in by_root.items():
            times = self.state.shelf_times(user.name, "favourite", root, [path for path, _ in pairs])
            for path, item in pairs:
                if path in times:
                    item["starred"] = _iso(times[path])
        return items

    def _dir_item(self, root: str, entry) -> dict:
        ident = make_id(root, entry.parts)
        return {"id": ident, "parent": make_id(root, entry.parts[:-1]), "isDir": True,
                "title": self._name(root, entry.parts), "coverArt": ident}

    def _album_item(self, root: str, entry, songs: int | None = None) -> dict:
        parts = entry.parts
        ident = make_id(root, parts)
        artist = self._artist_of(root, parts)
        created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(clamp_time(getattr(entry, "mtime", 0) or 0)))
        return {"id": ident, "name": self._name(root, parts), "title": self._name(root, parts), "isDir": True,
                "artist": self._name(root, artist) if artist else "", "artistId": make_id(root, artist) if artist else None,
                "parent": make_id(root, parts[:-1]), "coverArt": ident, "songCount": songs or 0, "duration": 0, "created": created}

    def _grouped(self, items: list) -> list:
        """[{"name": letter, "artist": [...]}] for the index lists, in alphabetical order."""
        groups: dict = {}
        for item in items:
            groups.setdefault(_letter(item["name"]), []).append(item)
        return [{"name": k, "artist": sorted(v, key=lambda a: a["name"].lower())} for k, v in sorted(groups.items())]

    # ---- artists and albums: read from the folders and their notes, when an app asks; nothing is walked, built or kept ---------------
    # The folders say how they are arranged. A folder's about.md may carry `layout: letter/artist/album` in its front matter: the artists are
    # then that many folders below it, for everything inside (a nearer note wins). Without a note the library's artist_level counts from
    # the top (1 = the top folders). An artist's albums are the folders inside the artist folder that hold songs, directly or in the folders
    # right inside them (CD1, CD2); a folder with album.md is an album also when its songs sit two folders down (a box set). Songs loose in
    # an artist folder are an album of that artist under the artist's name.
    def _default_level(self, root: str) -> int:
        r = self.lib.cfg.roots.get(root)
        return r.artist_level if r is not None else 1          # the upload folders: the top folders are the artists

    def _layout(self, root: str, folder) -> int | None:
        """The artist depth from the `layout:` line of a folder's about.md, None without one. Read at most once a minute per folder.
        This opens a file by itself, without the library's checks, so every part is checked here (PathError) and every caller hands
        over only folders the library has listed or checked for this user (folder_path, file_info, list_dir)."""
        for part in folder:
            check_segment(part)
        r = self.lib.cfg.roots.get(root)
        if r is None:
            return None                                         # the upload folders carry no layout notes
        key, now = (root, tuple(folder)), time.monotonic()
        hit = self._layouts.get(key)
        if hit and hit[1] > now:
            return hit[0]
        depth = None
        base = os.path.join(longpath(r.path), *folder)
        for name in _LAYOUT_NAMES:
            try:
                with open(os.path.join(base, name), "rb") as f:
                    raw = f.read(16384)
            except OSError:
                continue
            depth = layout_depth(markdown.front_matter(raw.decode("utf-8", "replace"))[0].get("layout"))
            break
        if len(self._layouts) > 5000:
            self._layouts = {}
        self._layouts[key] = (depth, now + _LAYOUT_SECONDS)
        return depth

    def _target(self, root: str, parts) -> int:
        """How many folders down from the top the artists are on the way to `parts`: the nearest layout note above them, else the default."""
        target, decided = self._default_level(root), False
        for i in range(len(parts) + 1):
            if i >= target and not (i == 1 and not decided):     # a top folder's own note counts even where the default puts the artists
                break
            depth = self._layout(root, parts[:i])
            if depth is not None:
                target, decided = i + depth, True
        return target

    def _artist_of(self, root: str, parts) -> tuple:
        """The artist folder on the way to an album or song folder; () above the artist level."""
        target = self._target(root, parts)
        return tuple(parts[:target]) if len(parts) >= target else ()

    def _artist_folders(self, user, root: str) -> list:
        """The artist folders, in folder order: each folder above the artist level is listed (the 27 letter folders of Letter/Artist), and
        its about.md read when it has one. A top folder's about.md is also read where the default makes the top folders the artists (one
        small file each), so a genre folder can say how it is arranged. The artist folders themselves are not opened."""
        found = []

        def down(parts, target, decided):
            try:
                dirs, _songs, files = self._folder(user, root, parts, False)
            except (NotFound, OSError):
                return
            if LAYOUT_NOTE in files:
                depth = self._layout(root, parts)
                if depth is not None:
                    target, decided = len(parts) + depth, True
            for d in dirs:
                if len(d.parts) < target:
                    down(d.parts, target, decided)
                    continue
                depth = self._layout(root, d.parts) if (len(d.parts) == 1 and not decided) else None
                if depth is not None:
                    down(d.parts, 1 + depth, True)
                else:
                    found.append(d)

        down((), self._default_level(root), False)
        return found

    def _album_files(self, user, root: str, parts, stat: bool, limit: int = 20) -> list:
        """The song entries of an album folder: its own, else those in the folders right inside it (CD1, CD2), and with album.md also one
        folder further down (a box set: Box/Disc 1/Album/songs). At most `limit` folders per level are opened. `stat`: see _folder."""
        try:
            dirs, songs, files = self._folder(user, root, parts, stat)
        except (NotFound, OSError):
            return []
        if songs:
            return list(songs)
        found = []
        layer = dirs[:limit]
        for depth in range(2 if files & set(ALBUM_NOTES) else 1):
            deeper = []
            for d in layer:
                try:
                    sub, inside, _files = self._folder(user, root, d.parts, stat)
                except (NotFound, OSError):
                    continue
                found += inside
                deeper += sub[:limit]
            layer = deeper
        return found

    def _songs_in(self, user, root: str, parts) -> int:
        """How many songs an album folder holds (see _album_files). 0 = not an album. A count: no sizes or dates are read."""
        return len(self._album_files(user, root, parts, False))

    def _albums_of(self, user, root: str, artist) -> list:
        """[(entry, songs)] of one artist folder: its loose songs first (as an album named after the artist), then each folder inside it
        that holds songs. The songs are only counted; the date is read of each album folder, and of the loose songs."""
        try:
            dirs, loose = self._list(user, root, artist, True)
        except (NotFound, OSError):
            return []
        out = []
        if loose:
            out.append((SimpleNamespace(parts=tuple(artist), name=artist[-1], mtime=max(e.mtime for e in loose)), len(loose)))
        counted = [(d, self._songs_in(user, root, d.parts)) for d in dirs]
        counts = {d.name: songs for d, songs in counted if songs}
        return out + [(d, counts[d.name]) for d in self._dated([d for d, songs in counted if songs])]

    def _album_stream(self, user, wanted: str = ""):
        """(root, entry, songs) of every album, artist by artist in folder order, read only as far as it is used."""
        for r in self._roots(user, wanted):
            for artist in self._artist_folders(user, r.name):
                for entry, songs in self._albums_of(user, r.name, artist.parts):
                    yield r.name, entry, songs

    def _album_run(self, user, wanted: str, offset: int, size: int, client: str = "", what: str = "list") -> list:
        """albums[offset:offset+size] in artist order. An app that pages through (offset 0, 500, 1000 ...) continues where its last page
        stopped, so paging costs one pass in all. Offset 0 starts a run; any other offset is answered only when it continues the run kept
        for this user, library, app (`c`) and list, or asks for that run's last page again (a retry). Every other offset answers EMPTY:
        skipping to it would mean listing every album before it, and nothing walks the library for one request. The place is kept a few
        minutes, in memory: (next offset, album stream, until, offset of the last page, the last page)."""
        key = (user.name, wanted, client, what)
        with self._runs_lock:
            now = time.monotonic()
            self._runs = {k: v for k, v in self._runs.items() if v[2] > now}
            kept = self._runs.get(key)
            if offset and kept and kept[3] == offset and len(kept[4]) == size:
                return list(kept[4])                                  # the same page again
            if offset and not (kept and kept[0] == offset):
                return []                                             # continues nothing; a kept run stays as it is
            self._runs.pop(key, None)                                 # taken out while it is read: two requests never share a stream
        stream = kept[1] if offset else self._album_stream(user, wanted)
        page = list(itertools.islice(stream, size))
        if len(page) == size:
            with self._runs_lock:
                while len(self._runs) >= MAX_RUNS:
                    del self._runs[min(self._runs, key=lambda k: self._runs[k][2])]
                self._runs[key] = (offset + size, stream, time.monotonic() + _ALBUM_CACHE_SECONDS, offset, page)
        return page

    def _random_albums(self, user, wanted: str, count: int) -> list:
        """[(root, entry, songs)] picked at random, folder by folder (a random letter, a random artist, a random album): a few listings
        each, never the whole library."""
        roots = self._roots(user, wanted)
        out, seen, misses = [], set(), 0
        for _ in range(count * 4):
            if len(out) >= count or not roots or misses >= 8:        # a small library runs out of new albums quickly
                break
            misses += 1
            r = random.choice(roots)
            parts = ()
            while len(parts) < self._target(r.name, parts):
                try:
                    dirs = self._list(user, r.name, parts, False)[0]
                except (NotFound, OSError):
                    dirs = []
                if not dirs:
                    break
                parts = random.choice(dirs).parts
            else:
                try:
                    dirs, loose = self._list(user, r.name, parts, False)
                except (NotFound, OSError):
                    continue
                choices = [(d.parts, d) for d in dirs] + ([(parts, SimpleNamespace(parts=parts, name=parts[-1], mtime=0))] if loose else [])
                if not choices:
                    continue
                album, entry = random.choice(choices)
                songs = len(loose) if album == parts else self._songs_in(user, r.name, album)
                if songs and (r.name, album) not in seen:
                    seen.add((r.name, album))
                    if album != parts:
                        entry = (self._dated([entry]) or [entry])[0]      # the date of the one album that is shown
                    out.append((r.name, entry, songs))
                    misses = 0
        return out

    def _artists(self, user, wanted: str = "") -> list:
        """The artist list: the folders at each library's artist level. The number of albums is not counted (that would mean opening
        every artist folder); it is 0 until the artist is opened."""
        out = []
        for r in self._roots(user, wanted):
            out += [{"id": make_id(r.name, d.parts), "name": self.lib.display_name(d.name), "coverArt": make_id(r.name, d.parts),
                     "albumCount": 0} for d in self._artist_folders(user, r.name)]
        return out

    def _album_songs(self, user, root: str, parts) -> list:
        """The song entries of an album folder (see _album_files); for the artist folder itself, only its loose songs."""
        if tuple(parts) == self._artist_of(root, parts):
            return list(self._list(user, root, parts, True)[1])
        return self._album_files(user, root, parts, True)

    # ---- endpoints -------------------------------------------------------------------------------------------------------
    def ping(self, user, p):
        return {}

    def license(self, user, p):
        return {"license": {"valid": True, "email": "", "licenseExpires": "2099-01-01T00:00:00.000Z"}}

    def extensions(self, user, p):
        """Answered without a login (see handle): `user` is None here."""
        return {"openSubsonicExtensions": [{"name": "apiKeyAuthentication", "versions": [1]}, {"name": "songLyrics", "versions": [1]}]}

    def token_info(self, user, p):
        """Part of apiKeyAuthentication: who an API key belongs to."""
        return {"tokenInfo": {"username": user.name}}

    # ---- internet radio: the station files (.m3u, .pls, .url) of a top-level Radio folder, in it and one folder down ---------
    def _stations(self, user) -> list:
        hit = self._radio.get(user.name)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        found = []
        for r in self._roots(user):
            try:
                top = self.lib.list_dir(user, r.name, (), True, False)
            except (NotFound, OSError):
                continue
            for folder in (e for e in top if e.is_dir and e.name.lower() == RADIO_FOLDER):
                try:
                    inside = self.lib.list_dir(user, r.name, folder.parts, True, False)
                    files = [e for e in inside if not e.is_dir]
                    for sub in (e for e in inside if e.is_dir):
                        files += [e for e in self.lib.list_dir(user, r.name, sub.parts, True, False) if not e.is_dir]
                except (NotFound, OSError):
                    continue
                files = [e for e in files if radio.is_radio_file(e.name)]         # first the station files, then the cut: logos do not count
                for entry in sorted(files, key=lambda e: e.parts)[:300]:
                    if len(found) >= radio.MAX_STATIONS:
                        break
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
        if base and is_number(tail):
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
        self.lib.folder_path(user, root, parts)                              # the library's checks before anything is read for this id
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
            if not parts or not self._has_root(user, root) or not _storable("/".join(parts)):      # the shelf cannot hold such a name
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

    def _favourites(self, user, wanted: str = "") -> list:
        """[(root, parts, is_dir, added)] of the Favourites shelf that this user can still open, newest first, at most 100;
        only library `wanted` when one is given."""
        out = []
        for row in self.state.shelf_list(user.name, "favourite"):
            if len(out) >= 100:
                break
            root, parts = row["root"], tuple(row["path"].split("/"))
            if (wanted and root != wanted) or not self._has_root(user, root):
                continue
            try:
                if row["is_dir"]:
                    self.lib.folder_path(user, root, parts)
                elif kinds.kind_of(self.lib.file_info(user, root, parts, True).name) != "audio":
                    continue
            except (NotFound, PathError, OSError):
                continue
            out.append((root, parts, row["is_dir"], row["added"]))
        return out

    def _starred(self, user, p) -> dict:
        """A starred folder at the library's artist level is an artist, any other an album."""
        artists, albums, songs = [], [], []
        for root, parts, is_dir, added in self._favourites(user, self._wanted(user, p)):
            ident = make_id(root, parts)
            if is_dir and len(parts) == self._target(root, parts):
                artists.append({"id": ident, "name": self._name(root, parts), "coverArt": ident, "starred": _iso(added)})
            elif is_dir:
                albums.append({**self._album_item(root, SimpleNamespace(parts=parts, mtime=added)), "starred": _iso(added)})
            else:
                try:
                    info = self.lib.file_info(user, root, parts, True)
                except (NotFound, PathError, OSError):
                    continue
                entry = SimpleNamespace(parts=parts, name=info.name, path=info.path, size=info.size, mtime=info.mtime)
                songs.append({**self._song(root, entry, self._tags(entry)), "starred": _iso(added)})
        return {"artist": artists, "album": albums, "song": songs}

    def starred(self, user, p):
        return {"starred": self._starred(user, p)}

    def starred2(self, user, p):
        return {"starred2": self._starred(user, p)}

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
        """Nothing is ever scanned: the folders are read when an app asks."""
        return {"scanStatus": {"scanning": False, "count": 0}}

    def music_folders(self, user, p):
        return {"musicFolders": {"musicFolder": [{"id": i, "name": r.name} for i, r in enumerate(self._roots(user), 1)]}}

    def _folder_root(self, user, value: str) -> str:
        """The library a music folder id (1, 2, ... in the order of getMusicFolders) or a folder id belongs to."""
        if is_number(value):
            roots = self._roots(user)
            if 1 <= int(value) <= len(roots):
                return roots[int(value) - 1].name
            raise ApiError(70, "The requested data was not found.")
        return read_id(value)[0]

    def _wanted(self, user, p) -> str:
        """The library named by `musicFolderId`, or "" for all of them."""
        return self._folder_root(user, p["musicFolderId"]) if p.get("musicFolderId") else ""

    def indexes(self, user, p):
        wanted = self._wanted(user, p)
        tops, files = [], []
        for r in self._roots(user, wanted):                          # folder browsing: the top folders as they are, each library listed once
            try:
                dirs, songs = self._list(user, r.name, (), True)
            except (NotFound, OSError):
                continue
            tops += [{"id": make_id(r.name, d.parts), "name": self.lib.display_name(d.name), "coverArt": make_id(r.name, d.parts)} for d in dirs]
            files += self._songs(r.name, songs)
        artists = self._mark_stars(user, tops)
        return {"indexes": {"ignoredArticles": "", "lastModified": int(time.time() * 1000),
                            "index": self._grouped(artists), "child": files}}

    def music_directory(self, user, p):
        if not p.get("id"):
            raise ApiError(10, "Required parameter is missing.")
        root, parts = (self._folder_root(user, p["id"]), ()) if is_number(p["id"]) else read_id(p["id"])
        if not self._has_root(user, root):
            raise ApiError(70, "The requested data was not found.")
        dirs, songs = self._list(user, root, parts, True)
        children = self._mark_stars(user, [self._dir_item(root, d) for d in dirs]) + self._songs(root, songs, user)
        return {"directory": {"id": p["id"], "parent": make_id(root, parts[:-1]) if parts else None,
                              "name": self._name(root, parts), "child": children}}

    def artists(self, user, p):
        artists = self._mark_stars(user, self._artists(user, self._wanted(user, p)))
        return {"artists": {"ignoredArticles": "", "index": self._grouped(artists)}}

    def artist(self, user, p):
        root, parts = read_id(p.get("id", ""))
        self.lib.folder_path(user, root, parts)                       # NotFound when it is gone or not for this user
        albums = [self._album_item(root, entry, songs) for entry, songs in self._albums_of(user, root, parts)]
        name = self._name(root, parts)
        found = self._mark_stars(user, [{"id": p["id"], "name": name, "coverArt": p["id"], "albumCount": len(albums)}])[0]
        return {"artist": {**found, "album": self._mark_stars(user, albums)}}

    def album(self, user, p):
        root, parts = read_id(p.get("id", ""))
        self.lib.folder_path(user, root, parts)                       # before anything else: the layout notes are opened at paths made from the id
        songs = self._album_songs(user, root, parts)
        listed = self._songs(root, songs, user)
        artist = self._artist_of(root, parts)
        starred = {k: v for k, v in self._mark_stars(user, [{"id": p["id"]}])[0].items() if k == "starred"}
        return {"album": {**starred, "id": p["id"], "name": self._name(root, parts), "title": self._name(root, parts),
                          "artist": self._name(root, artist) if artist else "",
                          "artistId": make_id(root, artist) if artist else None, "coverArt": p["id"],
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
        item = self._song(root, entry, self._tags(entry))
        self._mark_stars(user, [item])
        return {"song": item}

    def _recent_albums(self, user, wanted: str, offset: int, size: int) -> list:
        """[(root, folder entry)]: one page of the folders of the audio files this user has a saved position in (the web player keeps
        one), newest first. Nothing is logged for this: it is the same list as "continue" on the home page. A saved path is only a text
        from the database: each folder is checked by the library (still there, still open to this user) before it is shown, and the
        checking stops when the page is full."""
        out, seen = [], set()
        for row in self.state.recent_progress(user.name, 300) if offset < 300 else ():
            if len(out) >= offset + size:
                break
            parts = tuple(row["path"].split("/"))
            if len(parts) < 2 or kinds.kind_of(parts[-1]) != "audio" or (wanted and row["root"] != wanted) or not self._has_root(user, row["root"]):
                continue
            key = (row["root"], parts[:-1])
            if key in seen:
                continue
            seen.add(key)
            try:
                self.lib.folder_path(user, row["root"], parts[:-1])
            except (NotFound, PathError, OSError):
                continue
            out.append((row["root"], SimpleNamespace(parts=parts[:-1], name=parts[-2], mtime=row["updated"])))
        return out[offset:offset + size]

    # ---- the play queue (resume on another device): one saved queue per user, replaced by every savePlayQueue ------------------------
    def save_play_queue(self, user, p):
        ids = []
        for ident in p.get("_ids", [])[:500]:
            try:
                root, parts = read_id(ident)
            except ApiError:
                continue
            if parts and self._has_root(user, root) and kinds.kind_of(parts[-1]) == "audio":
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
                                             "by": self._client(p)})
        return {}

    def _resume_from_progress(self, user, newer_than: float):
        """A queue made from the web player's saved position: the unfinished audio file opened most recently (if later than `newer_than`),
        and the rest of its folder after it. None when there is none. Nothing is stored for this: the position is already kept for the web."""
        for row in self.state.recent_progress(user.name, 40):
            parts = tuple(row["path"].split("/"))
            if row["updated"] <= newer_than:
                return None                                           # the saved queue is newer than anything the web player did
            if len(parts) < 2 or kinds.kind_of(parts[-1]) != "audio" or row["percent"] >= 0.98 or passing_by(row["path"], row["locator"]) or not self._has_root(user, row["root"]):
                continue
            match = re.fullmatch(r"t:(\d+(?:[.]\d+)?)", row["locator"] or "")
            try:
                _, songs = self._list(user, row["root"], parts[:-1], True)
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
            item = self._song(root, entry, self._tags(entry))
            self._mark_stars(user, [item])
            entries.append(item)
        if not entries:
            return {"playQueue": {}}
        current = saved["current"] if any(e["id"] == saved["current"] for e in entries) else entries[0]["id"]
        return {"playQueue": {"entry": entries, "current": current, "position": saved["position"], "username": user.name,
                              "changed": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(clamp_time(saved["updated"]))),
                              "changedBy": saved.get("by", "")}}

    @staticmethod
    def _client(p) -> str:
        """The app's own name (`c`), as part of the key its paging place is kept under."""
        return re.sub(r"[^\w .-]", "", p.get("c", ""))[:40]

    def _list_albums(self, user, kind: str, wanted: str, offset: int, size: int, client: str = "") -> list:
        """[(root, entry, songs or None)] of one page of an album list type. Recent and starred come from what is saved for the user, random
        from a few random folders, the alphabetical lists artist by artist in folder order (alphabeticalByName too: sorting every album by
        name would mean opening every folder). Lists that need every folder opened (newest, frequent ...) are honestly empty."""
        if kind in UNKNOWN_LISTS:
            return []
        if kind == "starred":                                        # the album folders on the Favourites shelf, newest first
            found = [(root, SimpleNamespace(parts=parts, name=parts[-1], mtime=added), None)
                     for root, parts, is_dir, added in self._favourites(user, wanted) if is_dir and len(parts) != self._target(root, parts)]
            return found[offset:offset + size]
        if kind == "recent":
            return [(r, e, None) for r, e in self._recent_albums(user, wanted, offset, size)]
        if kind == "random":
            return self._random_albums(user, wanted, min(size, 100))
        return self._album_run(user, wanted, offset, size, client, "list")

    def _window(self, p) -> tuple:
        try:
            size = max(1, min(500, int(p.get("size", "10"))))
            offset = max(0, int(p.get("offset", "0")))
        except ValueError:
            size, offset = 10, 0
        return size, offset

    def _album_page(self, user, p) -> list:
        size, offset = self._window(p)
        albums = self._list_albums(user, p.get("type", "alphabeticalByName"), self._wanted(user, p), offset, size, self._client(p))
        return self._mark_stars(user, [self._album_item(r, e, songs) for r, e, songs in albums])

    def album_list2(self, user, p):
        return {"albumList2": {"album": self._album_page(user, p)}}

    def album_list(self, user, p):
        return {"albumList": {"album": self._album_page(user, p)}}

    def random_songs(self, user, p):
        size, _ = self._window({"size": p.get("size", "10")})
        songs = []
        for r, e, _songs in self._random_albums(user, self._wanted(user, p), 12):
            try:
                inside = self._album_songs(user, r, e.parts)
            except (NotFound, OSError):
                continue
            songs += self._songs(r, inside, user)
            if len(songs) >= size * 3:
                break
        random.shuffle(songs)
        return {"randomSongs": {"song": songs[:size]}}

    @staticmethod
    def _paged_window(p, name: str) -> tuple:
        """(count, offset) for artistCount/artistOffset, albumCount/albumOffset, songCount/songOffset (default 20)."""
        try:
            return max(0, min(500, int(p.get(name + "Count", "20")))), max(0, int(p.get(name + "Offset", "0")))
        except ValueError:
            return 20, 0

    @classmethod
    def _paged(cls, p, name: str, items: list) -> list:
        count, offset = cls._paged_window(p, name)
        return items[offset:offset + count]

    SEARCH_ARTISTS = 10                  # the albums of at most this many matching artists are opened for a search

    def _search_result(self, user, p) -> dict:
        """A search reads the artist folders' names (the folders above the artist level) and opens only the artists that match: their
        albums, and the songs of the first few. Album and song names elsewhere are not searched (that would mean opening every folder).
        An EMPTY query lists the library, artists and albums, paged, as apps that load everything ask for it."""
        query = re.sub(r"[\"*]", "", p.get("query", "")).strip().lower()
        wanted = self._wanted(user, p)
        asked = query or self._paged_window(p, "artist")[0]           # an app that pages through the albums asks for no artists: none are listed
        artists = sorted((a for a in (self._artists(user, wanted) if asked else []) if query in a["name"].lower()), key=lambda a: a["name"].lower())
        if not query:
            count, offset = self._paged_window(p, "album")
            albums = self._album_run(user, wanted, offset, count, self._client(p), "search") if count else []
            return {"artist": self._mark_stars(user, self._paged(p, "artist", artists)),
                    "album": self._mark_stars(user, [self._album_item(r, e, songs) for r, e, songs in albums]), "song": []}
        albums = []
        for a in artists[:self.SEARCH_ARTISTS]:
            root, parts = read_id(a["id"])
            albums += [(root, entry, songs) for entry, songs in self._albums_of(user, root, parts)]
        songs = []
        for r, e, _count in albums[:6]:
            try:
                songs += self._songs(r, self._album_songs(user, r, e.parts), user)
            except (NotFound, OSError):
                continue
        return {"artist": self._mark_stars(user, self._paged(p, "artist", artists)),
                "album": self._mark_stars(user, [self._album_item(r, e, n) for r, e, n in self._paged(p, "album", albums)]),
                "song": self._paged(p, "song", songs)}

    def search3(self, user, p):
        return {"searchResult3": self._search_result(user, p)}

    def search2(self, user, p):
        return {"searchResult2": self._search_result(user, p)}

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
        return FileResponse(info.path, media_type=mime, headers=headers, filename=clean(info.name) if attachment else None,
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
        data, mime = self._smaller(*found, p.get("size", ""))
        return Response(data, media_type=mime, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"})

    def _smaller(self, data: bytes, mime: str, size: str):
        """(bytes, mime) of the picture scaled down to `size` pixels wide (rounded up to one of the thumbnail widths), when the app
        asks for a size up to the largest of those; cached on disk. The original when it asks for none or more, when Pillow is not
        installed, or when the picture cannot be scaled."""
        if not is_number(size) or int(size) > thumbs.MAX_WIDTH:
            return data, mime
        width = thumbs.clamp_width(size)
        key = "subsonic|%s|%d" % (hashlib.sha1(data, usedforsecurity=False).hexdigest(), width)
        hit = self.thumb_cache.get(key)
        if hit is not None:
            return hit, "image/jpeg"
        with self.thumb_gate:                                         # a few at a time: each one decodes a whole picture in memory
            small = thumbs.scaled(data, width)
        if not small:
            return data, mime
        self.thumb_cache.put(key, small[0])
        return small

    def _folder_art(self, user, root: str, parts, depth: int):
        dirs, songs = self._list(user, root, parts, False)
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

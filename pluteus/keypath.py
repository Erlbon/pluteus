"""A read-only app key inside the address, for reader apps that cannot log in.

Some reader apps never send the login they are given (the PocketBook Android app sends none to this server). For them an address carries the
key: <site>/k/<app key>/opds. The middleware takes `/k/<key>` off the path, finds the user the key belongs to, and gives the request the same
Basic login the app would have sent (the key is the password), so everything downstream is unchanged: the same read-only user, the same
throttling of wrong keys, the same access rules. Every link in an OPDS feed that is answered this way carries the key too, so the app can follow
folders, covers and downloads without ever sending a login.

Only the paths a feed links to are reachable this way (opds, downloads, covers, thumbnails, comic pages); the web pages, uploads, the admin page and
everything that writes are not. Revoking the app key (admin page, Users) ends every address that holds it. The key is a secret in an address, so
it is masked in the access and debug logs; a proxy in front of pluteus may still log it.
"""
from __future__ import annotations

import base64
import re

ALLOWED = ("/opds", "/opds-shelves/", "/dl/", "/thumb/", "/cover/", "/cbz/")
FEED_TYPES = ("application/atom+xml", "application/opds+json")
_KEY = re.compile(r"[A-Za-z0-9_-]{8,80}")
MASK = re.compile(r"/k/[^/?#]+")


def masked(path: str) -> str:
    """The path with a key removed, for logs."""
    return MASK.sub("/k/***", path, count=1) if "/k/" in path else path


class KeyPath:
    def __init__(self, app, auth, base: str = ""):
        self.app, self.auth, self.base = app, auth, base

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/k/"):
            return await self.app(scope, receive, send)
        key, _, tail = scope["path"][3:].partition("/")
        rest = "/" + tail
        if not _KEY.fullmatch(key) or not (rest == "/opds" or any(rest.startswith(p) for p in ALLOWED)):
            return await self._plain(send, 404, b"Not found")
        name = self.auth.user_for_key(key)
        # an unknown key is sent on as a wrong login, so it is refused and counted like any other wrong password
        login = "%s:%s" % (name or "key-in-address", key)
        headers = [(k, v) for k, v in scope.get("headers", ()) if k.lower() not in (b"authorization", b"cookie")]
        headers.append((b"authorization", b"Basic " + base64.b64encode(login.encode("utf-8"))))
        inner = dict(scope, path=rest, headers=headers)
        raw = scope.get("raw_path")
        if raw:
            marker = ("/k/%s" % key).encode("ascii")
            at = raw.find(marker)
            inner["raw_path"] = (raw[:at] + raw[at + len(marker):]) if at >= 0 else rest.encode("ascii", "ignore")
        if not name:
            return await self.app(inner, receive, send)
        await self.app(inner, receive, self._rewriting(send, key))

    @staticmethod
    async def _plain(send, status: int, body: bytes) -> None:
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    def _rewriting(self, send, key: str):
        """`send` that puts the key into the links of a feed: every href that starts with this site's own prefix gets /k/<key> after it."""
        base = re.escape(self.base)
        atom = re.compile(r"""(href\s*=\s*["'])(%s)(/)""" % base)
        jsn = re.compile(r"""("href"\s*:\s*")(%s)(/)""" % base)
        held = {"start": None, "body": bytearray(), "feed": False}
        insert = "/k/" + key

        async def wrapped(message):
            if message["type"] == "http.response.start":
                kind = ""
                for k, v in message.get("headers", ()):
                    if k.lower() == b"content-type":
                        kind = v.decode("latin-1").split(";", 1)[0].strip().lower()
                held["feed"] = kind in FEED_TYPES and message.get("status", 200) == 200
                if held["feed"]:
                    held["start"] = message                      # answered together with the body, whose length changes
                    return
            elif message["type"] == "http.response.body" and held["feed"]:
                held["body"] += message.get("body", b"")
                if message.get("more_body"):
                    return
                text = held["body"].decode("utf-8", "replace")
                text = jsn.sub(lambda m: m.group(1) + m.group(2) + insert + m.group(3), atom.sub(lambda m: m.group(1) + m.group(2) + insert + m.group(3), text))
                data = text.encode("utf-8")
                start = held["start"]
                headers = [(k, v) for k, v in start.get("headers", ()) if k.lower() != b"content-length"]
                headers.append((b"content-length", str(len(data)).encode()))
                await send(dict(start, headers=headers))
                held["feed"] = False
                return await send({"type": "http.response.body", "body": data})
            await send(message)
        return wrapped

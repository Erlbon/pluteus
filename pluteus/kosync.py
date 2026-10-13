"""KOReader progress sync (the "kosync" protocol): KOReader devices (also on PocketBook, Kobo, Kindle, Android) keep their reading
position here and pick it up on another device.

Set `kosync = true`, make a key for the user on the admin page, and in KOReader (Tools > Progress sync > Custom sync server) give
the server address ending in /kosync, the user name, and the key as the password. KOReader identifies a book by a hash of its file,
so this stores one position per user and book hash: it syncs KOReader with KOReader and is not connected to the web readers.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

DOCUMENT = re.compile(r"[A-Za-z0-9._-]{1,128}")


def _answer(body: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status, media_type="application/json", headers={"Cache-Control": "no-store"})


class KoSync:
    def __init__(self, auth, state, throttle):
        self.auth, self.state, self.throttle = auth, state, throttle

    def login(self, request):
        """The user for the x-auth-user / x-auth-key headers (the key is the MD5 of the app key, which is what KOReader sends
        for a password), else None. Wrong tries count against the normal login lock."""
        name = request.headers.get("x-auth-user", "")
        sent = request.headers.get("x-auth-key", "").lower()
        if not name or not sent:
            return None
        if self.throttle.wait(request, name):
            return None
        user = self.auth.users.get(name)
        secret = self.state.get_appkey(name) if user else None
        expected = hashlib.md5(secret.encode("utf-8"), usedforsecurity=False).hexdigest() if secret else "0" * 32
        if user is None or secret is None or not hmac.compare_digest(expected, sent):
            self.throttle.fail(request, name)
            return None
        self.throttle.ok(request, name)
        return user

    async def handle(self, request):
        user = await run_in_threadpool(self.login, request)
        route = request.path_params["route"].strip("/")
        if route == "users/create":
            return _answer({"message": "Accounts are made on the pluteus admin page (Users, Make a key)."}, 403)
        if user is None:
            return _answer({"message": "Unauthorized"}, 401)
        if route == "users/auth" and request.method == "GET":
            return _answer({"authorized": "OK"})
        if route == "syncs/progress" and request.method == "PUT":
            return await self.put(request, user)
        if route.startswith("syncs/progress/") and request.method == "GET":
            return await run_in_threadpool(self.get, user, route[len("syncs/progress/"):])
        return _answer({"message": "Not found"}, 404)

    async def put(self, request, user):
        if int(request.headers.get("content-length", "0") or 0) > 4096:
            return _answer({"message": "Too large"}, 413)
        try:
            body = json.loads((await request.body())[:4096])
            document, progress = str(body["document"]), str(body["progress"])
            percentage = float(body["percentage"])
        except (ValueError, KeyError, TypeError):
            return _answer({"message": "Invalid request"}, 400)
        if not DOCUMENT.fullmatch(document) or len(progress) > 500 or not 0 <= percentage <= 1:
            return _answer({"message": "Invalid request"}, 400)
        now = time.time()
        await run_in_threadpool(self.state.put_kosync, user.name, document, progress, percentage,
                                str(body.get("device", ""))[:100], str(body.get("device_id", ""))[:100], now)
        return _answer({"document": document, "timestamp": int(now)})

    def get(self, user, document: str):
        if not DOCUMENT.fullmatch(document):
            return _answer({"message": "Invalid request"}, 400)
        row = self.state.get_kosync(user.name, document)
        if row is None:
            return _answer({"document": document})                # nothing stored yet: no percentage, KOReader says so
        return _answer({"document": document, **row})

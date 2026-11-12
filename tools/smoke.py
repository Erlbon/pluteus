"""Smoke test for a built pluteus (the exe, the Linux program, the Docker image): standard library only.

It makes a tiny library and a config with the admin page switched on, the program under test adds its own first user and
serves it, and then the real thing is asked for over HTTP: the health check, a folder page, a cover thumbnail (Pillow),
an audio page (tinytag) and the admin page with and without a login. Used by .github/workflows; works locally the same way:

    python tools/smoke.py prepare WORK --port 8600 --admin-port 8601
    PROGRAM user add smoke --config WORK/config/config.toml --admin --all-roots --password-stdin < WORK/password.txt
    PROGRAM serve --config WORK/config/config.toml &
    python tools/smoke.py check WORK --base http://127.0.0.1:8600 --admin http://127.0.0.1:8601

For a program inside a container, give `prepare` the paths as the container sees them (--library, --data), listen on
every interface there (--host 0.0.0.0 --admin-host 0.0.0.0) and mount WORK/config and WORK/library at those paths.
The password is random, made by `prepare` and kept in WORK/password.txt; it is a throwaway for this one run.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import struct
import sys
import time
import urllib.error
import urllib.request
import wave
import zipfile
import zlib

USER = "smoke"
COMIC = "Smoke 01.cbz"
SOUND = "tone.wav"


def png(width: int, height: int, rgb: tuple) -> bytes:
    """A plain-coloured PNG, written by hand so this file needs nothing but Python."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    row = bytes([0]) + bytes(rgb) * width
    return (bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10]) + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b""))


def toml_text(value: str) -> str:
    return json.dumps(value)                    # a JSON string is a valid TOML basic string


def prepare(a) -> int:
    work = os.path.abspath(a.work)
    folder = os.path.join(work, "library", "Smoke")
    for path in (folder, os.path.join(work, "config"), os.path.join(work, "data")):
        os.makedirs(path, exist_ok=True)
    with zipfile.ZipFile(os.path.join(folder, COMIC), "w", zipfile.ZIP_STORED) as z:
        z.writestr("page-01.png", png(120, 180, (200, 60, 40)))
        z.writestr("page-02.png", png(120, 180, (40, 60, 200)))
    with wave.open(os.path.join(folder, SOUND), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(bytes(16000))             # one second of silence
    library = a.library or os.path.join(work, "library")
    data = a.data or os.path.join(work, "data")
    lines = ["[server]", "host = %s" % toml_text(a.host), "port = %d" % a.port,
             "admin_host = %s" % toml_text(a.admin_host), "admin_port = %d" % a.admin_port,
             "data_dir = %s" % toml_text(data), "thumbnails = true", "",
             "[[roots]]", 'name = "library"', "path = %s" % toml_text(library), ""]
    with open(os.path.join(work, "config", "config.toml"), "w", encoding="utf-8", newline="") as f:
        f.write("".join(line + chr(10) for line in lines))
    with open(os.path.join(work, "password.txt"), "w", encoding="ascii", newline="") as f:
        f.write(secrets.token_urlsafe(18) + chr(10))
    if a.open_to_all:                           # a container's own user (not this one) must read the library and write the config
        for base, dirs, files in os.walk(work):
            os.chmod(base, 0o777)
            for name in files:
                os.chmod(os.path.join(base, name), 0o666)
    print("prepared %s" % work)
    return 0


def fetch(url: str, login: str = "", timeout: float = 20.0) -> tuple:
    """(status, content type, body) without following redirects; an HTTP error status is an answer, not an exception."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    request = urllib.request.Request(url, headers={"Accept": "text/html"})
    if login:
        request.add_header("Authorization", "Basic " + base64.b64encode(login.encode()).decode())
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def check(a) -> int:
    with open(os.path.join(os.path.abspath(a.work), "password.txt"), encoding="ascii") as f:
        login = "%s:%s" % (USER, f.read().strip())
    base, admin = a.base.rstrip("/"), a.admin.rstrip("/")
    deadline = time.monotonic() + a.wait
    while True:                                 # the server is starting: ask until it answers, not for a fixed time
        try:
            if fetch(base + "/healthz", timeout=3)[2] == b"ok":
                break
        except OSError:
            pass
        if time.monotonic() > deadline:
            print("FAILED: %s/healthz did not answer 'ok' within %d seconds" % (base, a.wait))
            return 1
        time.sleep(0.5)
    comic = urllib.request.quote(COMIC)
    failures = []

    def expect(what: str, url: str, who: str, test) -> None:
        try:
            status, kind, body = fetch(url, who)
            ok = test(status, kind, body)
            shown = "%d %s, %d bytes" % (status, kind or "-", len(body))
        except OSError as e:
            ok, shown = False, "no answer (%s)" % e
        print("%-6s %-34s %s" % ("ok" if ok else "FAILED", what, shown))
        if not ok:
            failures.append(what)

    expect("health check", base + "/healthz", "", lambda s, k, b: s == 200 and b == b"ok")
    expect("stylesheet", base + "/static/base.css", "", lambda s, k, b: s == 200 and len(b) > 1000)
    expect("ebook reader script", base + "/static/vendor/epub.min.js", "", lambda s, k, b: s == 200 and len(b) > 1000)
    expect("front page asks for a login", base + "/", "", lambda s, k, b: s in (302, 303, 401))
    expect("folder page", base + "/browse/library/Smoke", login, lambda s, k, b: s == 200 and b"Smoke 01" in b)
    expect("cover thumbnail (Pillow)", base + "/thumb/library/Smoke/%s?w=160" % comic, login,
           lambda s, k, b: s == 200 and k.startswith("image/jpeg") and b[:2] == bytes([0xFF, 0xD8]))
    expect("audio page (tinytag)", base + "/view/library/Smoke/" + SOUND, login, lambda s, k, b: s == 200 and b"tone" in b)
    expect("admin page refuses without login", admin + "/", "", lambda s, k, b: s == 401)
    expect("admin page", admin + "/", login, lambda s, k, b: s == 200 and b"<h1>Status</h1>" in b)
    expect("admin users tab", admin + "/users", login, lambda s, k, b: s == 200 and USER.encode() in b)
    if failures:
        print("FAILED: %s" % ", ".join(failures))
        return 1
    print("all smoke checks passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="make the library, the config and the password")
    p.add_argument("work")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--admin-port", type=int, required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--admin-host", default="127.0.0.1")
    p.add_argument("--library", default="", help="the library folder as the program sees it (default: WORK/library)")
    p.add_argument("--data", default="", help="the data folder as the program sees it (default: WORK/data)")
    p.add_argument("--open-to-all", action="store_true", help="make WORK readable and writable for every account (containers)")
    c = sub.add_parser("check", help="ask the running program for its pages")
    c.add_argument("work")
    c.add_argument("--base", required=True, help="the site, e.g. http://127.0.0.1:8600")
    c.add_argument("--admin", required=True, help="the admin page, e.g. http://127.0.0.1:8601")
    c.add_argument("--wait", type=int, default=60, help="seconds to wait for the server to start")
    a = parser.parse_args()
    return prepare(a) if a.cmd == "prepare" else check(a)


if __name__ == "__main__":
    sys.exit(main())

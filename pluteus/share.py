"""Quick share: serve folders on the local network straight away, with no config file, and show the address as a QR code.

    python -m pluteus share [FOLDER ...] [--private] [--upload [DIR]] [--port N]

The default is OPEN and read-only: anyone on the network who has the address can browse and download (a warning says so).
--private makes the QR code carry a one-time access link (/join/<token>), so only people who scan it (or are sent the link) get in.
--upload lets those guests upload into one separate folder, and always means --private. Nothing is written to the shared folders,
the session data lives in a temporary folder that is removed on exit, and the share stops when you press Ctrl+C.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import socket
import sys
import tempfile

from .auth import hash_password
from .config import Config, ConfigError, parse_config

SHARE_USER = "guest"


def lan_ip() -> str:
    """This machine's address on the local network (the one a phone on the same Wi-Fi can reach), else 127.0.0.1."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))                 # a documentation address: nothing is sent, it only picks the route
            ip = s.getsockname()[0]
        return ip if not ip.startswith("127.") else "127.0.0.1"
    except OSError:
        return "127.0.0.1"


def other_addresses(primary: str) -> list:
    """The machine's other usable IPv4 addresses (Wi-Fi, Ethernet, VPN, virtual adapters), to suggest if the first one does not
    work from the phone. Loopback and link-local addresses are left out."""
    found = []
    try:
        found = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    return sorted({a for a in found if a != primary and not a.startswith(("127.", "169.254."))})


def free_port(start: int = 8080, host: str = "0.0.0.0", tries: int = 50) -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    raise ConfigError("no free port between %d and %d" % (start, start + tries - 1))


def root_names(folders: list) -> list:
    """One URL-safe, unique name per folder, from its own name."""
    names = []
    for folder in folders:
        base = re.sub(r"[^A-Za-z0-9_-]+", "-", os.path.basename(os.path.abspath(folder)) or "files").strip("-") or "files"
        name, n = base, 2
        while name in names:
            name, n = "%s-%d" % (base, n), n + 1
        names.append(name)
    return names


def _inside(path: str, folder: str) -> bool:
    path, folder = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(folder))
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def build_config(folders: list, *, port: int, upload_dir: str = "", private: bool = False, token: str = "",
                 data_dir: str = "") -> Config:
    """The configuration of one share, built in memory. Open mode: the guest user is the anonymous user (read-only, no login).
    Private mode: the guest user has an unguessable password nobody is told, and the one-time token signs people in."""
    folders = [os.path.abspath(f) for f in folders]
    for f in folders:
        if not os.path.isdir(f):
            raise ConfigError("%s is not a folder" % f)
    if upload_dir:
        for f in folders:
            if _inside(upload_dir, f) or _inside(f, upload_dir):
                raise ConfigError("the upload folder %s and the shared folder %s must be separate folders" % (upload_dir, f))
    names = root_names(folders)
    data = {
        "server": {"host": "0.0.0.0", "port": port, "data_dir": data_dir, "cookie_secure": "never", "update_check": False,
                   **({"uploads_dir": upload_dir} if upload_dir else {}),
                   **({"join_token": token, "join_user": SHARE_USER} if private else {"anonymous_user": SHARE_USER})},
        "roots": [{"name": n, "path": f} for n, f in zip(names, folders)],
        "users": [{"name": SHARE_USER, "password_hash": hash_password(secrets.token_hex(24)), "roots": ["*"],
                   "upload": bool(upload_dir)}],
    }
    return parse_config(data)


# ---- the QR code in the terminal ---------------------------------------------------------------------------------------
def _encodable(text: str, encoding: str) -> bool:
    try:
        text.encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def qr_text(url: str, *, blocks: bool = True, colours: bool = True) -> str:
    """The URL as a QR code in text. With `blocks` it uses half-block characters (two modules per line); `colours` paints it black
    on white whatever the terminal theme, otherwise it is drawn as is (right on a light terminal). Without block characters it uses
    '##' and spaces."""
    from qrcodegen import QrCode
    qr = QrCode.encode_text(url, QrCode.Ecc.MEDIUM)
    n, border = qr.get_size(), 2

    def dark(x: int, y: int) -> bool:
        return 0 <= x < n and 0 <= y < n and qr.get_module(x, y)

    lines = []
    if blocks:
        glyph = {(False, False): " ", (True, True): "█", (True, False): "▀", (False, True): "▄"}
        for y in range(-border, n + border, 2):
            lines.append("".join(glyph[(dark(x, y), dark(x, y + 1))] for x in range(-border, n + border)))
    else:
        for y in range(-border, n + border):
            lines.append("".join("##" if dark(x, y) else "  " for x in range(-border, n + border)))
    if colours and blocks:
        lines = ["\x1b[30;47m" + line + "\x1b[0m" for line in lines]
    return "\n".join(lines)


def _enable_ansi() -> bool:
    if os.name == "nt":
        os.system("")                                   # switches the console to understand ANSI colour codes (Windows 10 and later)
    return sys.stdout.isatty() and os.environ.get("TERM") != "dumb" and "NO_COLOR" not in os.environ


def banner(cfg: Config, url: str, *, private: bool, upload_dir: str, show_qr: bool, others: tuple = ()) -> str:
    from . import __version__
    out = ["", "Pluteus %s quick share" % __version__, ""]
    for r in cfg.roots.values():
        out.append("  Sharing  %s%s" % (r.path, "  (read-only)"))
    if upload_dir:
        out.append("  Uploads  %s" % upload_dir)
    out += ["", "  Open this on a phone or computer on the same network:", "      " + url, ""]
    if show_qr:
        try:
            colours = _enable_ansi()
            blocks = _encodable("█▀▄", getattr(sys.stdout, "encoding", None) or "ascii")
            out += ["    " + line for line in qr_text(url, blocks=blocks, colours=colours).split("\n")]
            out.append("")
        except ImportError:
            out.append("  (the qrcodegen package is not installed: no QR code)")
    if others:
        out.append("  If a phone cannot reach it, this computer has other addresses too: %s (use --address)." % ", ".join(others))
    if private:
        out.append("  PRIVATE: only people with this link or QR code can get in. The link stops working when you stop sharing.")
    else:
        out.append("  OPEN: anyone on this network who knows the address can browse and download. Use --private to limit it.")
    out += ["  Press Ctrl+C to stop.", ""]
    return "\n".join(out)


def run(folders: list, *, private: bool, upload: str | None, port: int, host: str, show_qr: bool, address: str = "") -> int:
    private = private or upload is not None
    upload_dir = ""
    if upload is not None:
        upload_dir = os.path.abspath(upload or os.path.join(os.path.expanduser("~"), "Pluteus uploads"))
    data_dir = tempfile.mkdtemp(prefix="pluteus-share-")
    try:
        if upload_dir:
            os.makedirs(upload_dir, exist_ok=True)
        token = secrets.token_urlsafe(16) if private else ""
        port = free_port(port or 8080, host)
        cfg = build_config(folders or ["."], port=port, upload_dir=upload_dir, private=private, token=token, data_dir=data_dir)
        address = address or lan_ip()
        url = "http://%s:%d/%s" % (address, port, ("join/" + token) if private else "")
        print(banner(cfg, url, private=private, upload_dir=upload_dir, show_qr=show_qr, others=tuple(other_addresses(address))), flush=True)
        import uvicorn

        from .app import create_app
        app = create_app(cfg)
        uvicorn.run(app, host=host, port=port, log_level="warning")
        return 0
    except ConfigError as err:
        print("Error:", err, file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)

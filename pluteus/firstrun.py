"""Everyday start of the downloaded programs: `pluteus` with no arguments (or `pluteus start`).

The first time there is no configuration, so it asks three questions in the console (an administrator name, a password the person types
themselves, and optionally a first library folder), writes config.toml in the user's own settings folder, starts the server and opens
the admin page in the browser, where libraries are added with the folder picker. From then on `pluteus` simply starts the server with
that configuration. Windows: %APPDATA%\\Pluteus; Linux and macOS: ~/.config/pluteus (or $XDG_CONFIG_HOME/pluteus); PLUTEUS_HOME overrides.
"""
from __future__ import annotations

import getpass
import os
import re
import sys

from .auth import MIN_PASSWORD, hash_password
from .config import upload_name_ok
from .libedit import _toml_string
from .useredit import EditError, _q, validate

NAME_OK = re.compile(r"^[^:\s][^:]*$")


class SetupError(ValueError):
    """The first configuration would not load; nothing was written. The message says why."""


def name_ok(name: str) -> bool:
    """The administrator gets an upload folder named after them (uploads_dir is set in the first configuration), so the
    name must already follow the upload-folder rule here: otherwise the configuration written would not load."""
    return bool(NAME_OK.match(name)) and upload_name_ok(name)


def config_dir() -> str:
    override = os.environ.get("PLUTEUS_HOME")
    if override:
        return os.path.abspath(override)
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Pluteus")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"), "pluteus")


def default_config_path() -> str:
    return os.path.join(config_dir(), "config.toml")


def build_config_text(*, admin: str, password_hash: str, library_folder: str, home: str, port: int, admin_port: int) -> str:
    return "\n".join([
        "# Pluteus configuration, created on the first run. Add libraries on the admin page (Libraries tab) or edit this file.",
        "[server]",
        'host = "0.0.0.0"                   # reachable from other devices on this network; the site asks for a login',
        "port = %d" % port,
        "data_dir = %s" % _toml_string(os.path.join(home, "data")),
        "uploads_dir = %s" % _toml_string(os.path.join(home, "uploads")),
        "admin_port = %d                  # the admin page" % admin_port,
        'admin_host = "127.0.0.1"           # ... for this computer only',
        "",
        "[[roots]]",
        'name = "library"',
        "path = %s" % _toml_string(library_folder),
        "",
        "[[users]]",
        "name = %s" % _q(admin),
        "password_hash = %s" % _q(password_hash),
        'roots = ["*"]                      # every library, also ones added later',
        "adult = true",
        "admin = true",
        "",
    ])


def ask_setup(read=input, read_password=getpass.getpass, say=print) -> tuple:
    """The three first-run questions. The password is typed by the person (hidden), never chosen for them."""
    say("")
    say("Welcome to Pluteus. This is the first run, so let's set up an administrator account.")
    say("")
    name = ""
    while not name:
        name = (read("Name of the administrator [admin]: ").strip() or "admin")
        if not name_ok(name):
            say("  Use letters (a to z), digits, space, - _ . only, not starting or ending with a space or a dot "
                "(the name also becomes the name of your upload folder).")
            name = ""
    while True:
        password = read_password("Password (at least %d characters; you type it, it is not shown): " % MIN_PASSWORD)
        if len(password) < MIN_PASSWORD:
            say("  Too short: at least %d characters." % MIN_PASSWORD)
            continue
        if read_password("Password again: ") != password:
            say("  The two passwords differ. Try again.")
            continue
        break
    say("")
    folder = read("Folder with your books or comics (press Enter to add libraries later in your browser): ").strip().strip('"')
    return name, password, folder


def create_config(path: str, name: str, password: str, folder: str, *, port: int = 8080, admin_port: int = 8101) -> str:
    """Write the first configuration to `path`; returns the library folder used. The placeholder library (when no folder was given)
    is an empty folder next to the configuration, so Pluteus always has one library to start with.

    The text is checked exactly as the next start will check it (useredit.validate with the path: parse_config plus the
    overlap rules, such as a library folder that holds this settings folder) BEFORE anything is written; ValueError with
    the reason when it would not load, so the caller can ask again instead of leaving a configuration that never starts."""
    home = os.path.dirname(os.path.abspath(path))
    placeholder = os.path.join(home, "library")
    folder = os.path.abspath(os.path.expanduser(folder)) if folder else placeholder
    if folder != placeholder and not os.path.isdir(folder):
        raise ValueError("%s is not a folder" % folder)
    text = build_config_text(admin=name, password_hash=hash_password(password), library_folder=folder, home=home,
                             port=port, admin_port=admin_port)
    try:
        validate(text, os.path.abspath(path))
    except EditError as e:
        raise SetupError(str(e)) from None
    os.makedirs(home, exist_ok=True)
    if folder == placeholder:
        os.makedirs(folder, exist_ok=True)
    # Owner-only from the first byte (it holds the password hash); the mode is ignored where the system has no such thing.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    return folder


def start(config_path: str | None, run_server, read=input, read_password=getpass.getpass, say=print, open_browser=True) -> int:
    """Start with the default (or the given) configuration, creating it on the first run. `run_server(cfg, path)` runs the server."""
    from .config import ConfigError, load_config
    from .share import free_port

    path = config_path or default_config_path()
    first = not os.path.exists(path)
    if first:
        if not sys.stdin or not sys.stdin.isatty():
            print("Error: no configuration yet (%s) and this is not a terminal to ask in. Run pluteus in a terminal once." % path,
                  file=sys.stderr)
            return 2
        name, password, folder = ask_setup(read, read_password, say)
        while True:
            try:
                create_config(path, name, password, folder, port=free_port(8080), admin_port=free_port(8101, "127.0.0.1"))
                break
            except ValueError as err:
                say("  %s" % err)
                folder = read("Folder with your books or comics (Enter = later): ").strip().strip('"')
        say("")
        say("Settings saved in %s" % path)
    try:
        cfg = load_config(path, check_roots=False)
    except ConfigError as err:
        print("Error: %s (%s)" % (err, path), file=sys.stderr)
        return 2
    from .share import lan_ip
    say("")
    say("Pluteus is starting. Open it from a phone or computer on this network at  http://%s:%d/" % (lan_ip(), cfg.port))
    say("Press Ctrl+C to stop. Settings: %s" % path)
    say("")
    if first and open_browser and cfg.admin_port:
        def admin_is_up() -> None:
            """Called by the server once the admin page really listens; never when it could not start (a taken port), so
            the browser is not opened on a dead address."""
            import threading
            import webbrowser
            say("Your browser opens the admin page now. Sign in with the name and password you just chose, then add your")
            say("libraries on the Libraries tab.")
            # In a thread of its own: a console browser would otherwise hold up the start of the site itself.
            threading.Thread(target=webbrowser.open, args=("http://127.0.0.1:%d/libraries" % cfg.admin_port,),
                             name="pluteus-open-browser", daemon=True).start()
        return run_server(cfg, path, on_admin=admin_is_up)
    return run_server(cfg, path)

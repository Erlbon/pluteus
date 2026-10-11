"""Command line: serve, check-config, hash-password, user list/add/set/remove, admin (window)."""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from . import __version__, useredit
from .auth import MIN_PASSWORD, hash_password
from .config import ConfigError, load_config


ATTEMPTS = 3


def _read_password(from_stdin: bool, who: str) -> str:
    if from_stdin:
        pw = sys.stdin.readline().rstrip("\r\n")
        if len(pw) < MIN_PASSWORD:
            raise useredit.EditError("password must be at least %d characters" % MIN_PASSWORD)
        return pw
    # Interactive: say the rule BEFORE the user types, refuse a too-short password at once (before asking for it a
    # second time) and let them try again instead of starting the whole command over.
    print("The password must be at least %d characters." % MIN_PASSWORD)
    for attempt in range(ATTEMPTS):
        last = attempt == ATTEMPTS - 1
        pw = getpass.getpass("Password for %s (at least %d characters): " % (who, MIN_PASSWORD))
        if len(pw) < MIN_PASSWORD:
            if last:
                raise useredit.EditError("password must be at least %d characters" % MIN_PASSWORD)
            print("Too short: %d characters, at least %d are needed. Try again." % (len(pw), MIN_PASSWORD))
            continue
        if pw != getpass.getpass("Again: "):
            if last:
                raise useredit.EditError("the two entries differ")
            print("The two entries differ. Try again.")
            continue
        return pw
    raise useredit.EditError("no valid password entered")


def _split_roots(value: str) -> list:
    return [r.strip() for r in value.split(",") if r.strip()]


def _user_command(a) -> int:
    if a.user_cmd == "list":
        cfg = load_config(a.config, check_roots=False)
        print("%-20s %-8s %-8s %-8s %s" % ("user", "adult", "upload", "admin", "roots"))
        for u in cfg.users.values():
            print("%-20s %-8s %-8s %-8s %s" % (u.name, "yes" if u.adult else "no", "yes" if u.upload else "no",
                                               "yes" if u.admin else "no", ", ".join(u.roots) or "(all)"))
        return 0
    if a.user_cmd == "add":
        pw = _read_password(a.password_stdin, a.name)
        useredit.add_user(a.config, a.name, hash_password(pw), _split_roots(a.roots or ""), a.adult, not a.no_upload, a.admin)
        print("Added user %s." % a.name)
    elif a.user_cmd == "set":
        if not (a.password or a.password_stdin or a.roots is not None or a.all_roots
                or a.adult or a.no_adult or a.upload or a.no_upload or a.admin or a.no_admin):
            raise useredit.EditError("nothing to change: give --password, --roots, --all-roots, --adult, --no-adult, "
                                     "--upload, --no-upload, --admin or --no-admin")
        pw_hash = hash_password(_read_password(a.password_stdin, a.name)) if (a.password or a.password_stdin) else None
        roots = useredit.UNSET
        if a.all_roots:
            roots = ()
        elif a.roots is not None:
            roots = _split_roots(a.roots)
        useredit.set_user(a.config, a.name, password_hash=pw_hash, roots=roots,
                          adult=True if a.adult else (False if a.no_adult else None),
                          upload=True if a.upload else (False if a.no_upload else None),
                          admin=True if a.admin else (False if a.no_admin else None))
        print("Updated user %s." % a.name)
    elif a.user_cmd == "remove":
        if not a.yes and input("Remove user %s? [y/N] " % a.name).strip().lower() != "y":
            print("Cancelled.")
            return 1
        useredit.remove_user(a.config, a.name)
        print("Removed user %s." % a.name)
    print("Saved (previous file kept as %s.bak). A running server picks up user changes within a few seconds." % a.config)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pluteus", description="Filesystem-first book and comic server.")
    ap.add_argument("--version", action="version", version="pluteus " + __version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the server")
    serve.add_argument("--config", required=True, help="path to config.toml")
    check = sub.add_parser("check-config", help="validate a config file and exit")
    check.add_argument("--config", required=True)
    sub.add_parser("hash-password", help="print a password hash (for editing config.toml by hand)")
    sup = sub.add_parser("supervise", help="run the server and start it again whenever it exits (for a scheduled task)")
    sup.add_argument("--config", required=True)
    sup.add_argument("--log", required=True, help="file the server's output is appended to")
    sup.add_argument("--delay", type=float, default=10.0, help="seconds to wait before starting it again")
    admin = sub.add_parser("admin", help="open the admin window (users and config file)")
    admin.add_argument("--config", help="path to config.toml (default: the one used last time)")

    user = sub.add_parser("user", help="manage users in config.toml")
    usub = user.add_subparsers(dest="user_cmd", required=True)
    u_list = usub.add_parser("list", help="show the users")
    u_add = usub.add_parser("add", help="add a user")
    u_set = usub.add_parser("set", help="change a user")
    u_rm = usub.add_parser("remove", help="remove a user")
    for p in (u_list, u_add, u_set, u_rm):
        p.add_argument("--config", required=True, help="path to config.toml")
    for p in (u_add, u_set, u_rm):
        p.add_argument("name")
    for p in (u_add, u_set):
        p.add_argument("--password-stdin", action="store_true", help="read the password from stdin (scripts)")
        p.add_argument("--roots", help="comma-separated roots this user may use (default: all)")
    u_add.add_argument("--adult", action="store_true", help="may see folders carrying the adult marker")
    u_add.add_argument("--no-upload", action="store_true", help="no personal upload folder for this user")
    u_add.add_argument("--admin", action="store_true", help="may sign in to the admin page (admin_port)")
    u_set.add_argument("--password", action="store_true", help="prompt for a new password")
    u_set.add_argument("--all-roots", action="store_true", help="allow every root again")
    u_set.add_argument("--adult", action="store_true")
    u_set.add_argument("--no-adult", action="store_true")
    u_set.add_argument("--upload", action="store_true", help="give the user a personal upload folder (the default)")
    u_set.add_argument("--no-upload", action="store_true", help="no personal upload folder for this user")
    u_set.add_argument("--admin", action="store_true", help="may sign in to the admin page (admin_port)")
    u_set.add_argument("--no-admin", action="store_true", help="may not sign in to the admin page")
    u_rm.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    a = ap.parse_args(argv)

    if a.cmd == "supervise":
        from .supervise import run as supervise
        return supervise(a.config, a.log, a.delay)
    if a.cmd == "admin":
        from .admin_gui import main as admin_main
        return admin_main(["--config", a.config] if a.config else [])
    if a.cmd == "hash-password":
        try:
            print(hash_password(_read_password(False, "the new hash")))
        except useredit.EditError as e:
            print("Error:", e, file=sys.stderr)
            return 1
        return 0
    try:
        if a.cmd == "user":
            return _user_command(a)
        # check-config insists every root exists; serve only warns, so a share that is offline at boot (the usual
        # case after a power cut) does not turn into a restart loop: its folders answer 503 until it is back.
        cfg = load_config(a.config, check_roots=a.cmd == "check-config")
    except (ConfigError, useredit.EditError) as e:
        print("Error:", e, file=sys.stderr)
        return 2
    if a.cmd == "check-config":
        print("OK: %d root(s), %d user(s)" % (len(cfg.roots), len(cfg.users)))
        return 0
    from .fs import longpath
    for root in cfg.roots.values():
        if not os.path.isdir(longpath(root.path)):
            print("Warning: root %r (%s) is not reachable right now" % (root.name, root.path), file=sys.stderr)
    import uvicorn

    from .app import create_app
    app = create_app(cfg, config_path=a.config)
    admin = None
    if cfg.admin_port:                                  # the admin page: its own port, for the local network only
        from .adminapp import start_admin_server
        admin = start_admin_server(app)
        print("Admin page on http://%s:%d/ (local network only: never route or forward this port)"
              % (cfg.admin_host, cfg.admin_port), file=sys.stderr)
    try:
        uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")
    finally:
        if admin:
            admin.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

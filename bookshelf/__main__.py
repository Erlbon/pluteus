"""Command line: serve, hash-password, check-config."""
from __future__ import annotations

import argparse
import getpass
import sys

from . import __version__
from .auth import hash_password
from .config import ConfigError, load_config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bookshelf", description="Filesystem-first book and comic server.")
    ap.add_argument("--version", action="version", version="bookshelf " + __version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the server")
    serve.add_argument("--config", required=True, help="path to config.toml")
    check = sub.add_parser("check-config", help="validate a config file and exit")
    check.add_argument("--config", required=True)
    sub.add_parser("hash-password", help="print a password hash for config.toml")
    a = ap.parse_args(argv)

    if a.cmd == "hash-password":
        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Again: "):
            print("The two entries differ.", file=sys.stderr)
            return 1
        print(hash_password(pw))
        return 0
    try:
        cfg = load_config(a.config)
    except ConfigError as e:
        print("Config error:", e, file=sys.stderr)
        return 2
    if a.cmd == "check-config":
        print("OK: %d root(s), %d user(s)" % (len(cfg.roots), len(cfg.users)))
        return 0
    import uvicorn

    from .app import create_app
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())

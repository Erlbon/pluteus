"""Entry point of pluteus-tray.exe, the windowed program of the installed folder (PyInstaller, console=False).

With no arguments or only tray options (--autostart, --config, --quit) it runs the tray (pluteus/tray.py). Given a command
(`serve`, `user` ...) it is the ordinary command line, so it can stand in for pluteus.exe when that one is missing.
"""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    args = sys.argv[1:]
    if args and not args[0].startswith("-"):
        from pluteus.__main__ import main
        sys.exit(main(args))
    from pluteus.tray import main as tray_main
    sys.exit(tray_main(args))

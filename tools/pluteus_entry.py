"""Entry point of the packaged program (PyInstaller): the same command line as `python -m pluteus`."""
import multiprocessing
import os
import sys

from pluteus.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    code = main()
    # Started by double-click on Windows: keep the console open when something went wrong, so the message can be read.
    if code and len(sys.argv) == 1 and os.name == "nt":
        try:
            input("Press Enter to close...")
        except (EOFError, OSError):
            pass
    sys.exit(code)

"""Entry point of the packaged program (PyInstaller): the same command line as `python -m pluteus`."""
import multiprocessing
import sys

from pluteus.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())

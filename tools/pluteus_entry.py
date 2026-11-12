"""Entry point of the packaged program (PyInstaller): the same command line as `python -m pluteus`."""
import multiprocessing
import os
import sys

from pluteus.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    # What is inside this build (the release smoke tests print it): the bundled Python and OpenSSL. Asked for with an
    # environment variable so that the command line stays exactly that of `python -m pluteus`.
    if os.environ.get("PLUTEUS_BUILD_INFO"):
        import platform
        import ssl
        print("Python %s; %s; built for %s" % (platform.python_version(), ssl.OPENSSL_VERSION, platform.platform()))
        sys.exit(0)
    code = main()
    # Started by double-click on Windows: keep the console open when something went wrong, so the message can be read.
    if code and len(sys.argv) == 1 and os.name == "nt":
        try:
            input("Press Enter to close...")
        except (EOFError, OSError):
            pass
    sys.exit(code)

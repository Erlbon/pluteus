# Builds the Linux program (dist/pluteus) with PyInstaller. Debian 11 (bullseye) on purpose: its glibc 2.31 is old, so the result runs on
# most current distributions (a binary built on a newer system will not run on an older one).
FROM python:3.12-bullseye
RUN pip install --no-cache-dir pyinstaller
WORKDIR /src

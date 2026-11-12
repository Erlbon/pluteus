# The base image is pinned by digest (python:3.12-slim as it was on 2026-11-12), so a build always starts from the same
# files. It does NOT follow the tag any more: to take newer Python and Debian security fixes, look up the current digest
#   docker buildx imagetools inspect python:3.12-slim        (the "Digest:" line; it covers amd64 and arm64)
# and replace it here. The Python version must stay the one the lock files were made for (tools/make_lock.py).
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# bsdtar (libarchive) reads RAR and 7z comics (CBR, CB7). About 2 MB; without it only CBZ and CBT open in the reader.
RUN apt-get update && apt-get install -y --no-install-recommends libarchive-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# The dependencies come from the hashed lock file and from nowhere else: pip refuses any file whose SHA-256 is not listed.
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
# pluteus itself is not built or installed (that would fetch an unpinned build tool): the package is copied and put on
# Python's path, so `python -m pluteus` works from any folder in the container.
COPY pluteus ./pluteus
ENV PYTHONPATH=/app

# A fixed user id keeps bind-mount permissions predictable on Linux hosts.
RUN useradd --no-create-home --uid 10001 pluteus && mkdir /data /config && chown pluteus /data /config
USER pluteus

# state.sqlite3 (sessions, reading progress, backups) and the metadata cache live here: mount a volume on it.
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').read()==b'ok' else 1)"
CMD ["python", "-m", "pluteus", "serve", "--config", "/config/config.toml"]

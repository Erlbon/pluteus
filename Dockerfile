FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# bsdtar (libarchive) reads RAR and 7z comics (CBR, CB7). About 2 MB; without it only CBZ and CBT open in the reader.
RUN apt-get update && apt-get install -y --no-install-recommends libarchive-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY pluteus ./pluteus
RUN pip install --no-cache-dir .

# A fixed user id keeps bind-mount permissions predictable on Linux hosts.
RUN useradd --no-create-home --uid 10001 pluteus && mkdir /data /config && chown pluteus /data /config
USER pluteus

# state.sqlite3 (sessions, reading progress, backups) and the metadata cache live here: mount a volume on it.
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').read()==b'ok' else 1)"
CMD ["python", "-m", "pluteus", "serve", "--config", "/config/config.toml"]

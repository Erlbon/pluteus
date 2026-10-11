FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY pyproject.toml ./
COPY pluteus ./pluteus
RUN pip install --no-cache-dir .

# A fixed user id keeps bind-mount permissions predictable on Linux hosts.
RUN useradd --no-create-home --uid 10001 pluteus && mkdir /data /config && chown pluteus /data
USER pluteus

# state.sqlite3 (sessions, reading progress, backups) and the metadata cache live here: mount a volume on it.
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').read()==b'ok' else 1)"
CMD ["python", "-m", "pluteus", "serve", "--config", "/config/config.toml"]

# UNTESTED: written alongside the Windows-native prototype, not yet built.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY bookshelf ./bookshelf
RUN pip install --no-cache-dir .

RUN useradd --system --no-create-home bookshelf
USER bookshelf

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').read()==b'ok' else 1)"
CMD ["python", "-m", "bookshelf", "serve", "--config", "/config/config.toml"]

#!/bin/bash
# Usage: tools/linux-build/build.sh [OUTPUT_DIR]    (needs Docker)
# Builds the image, then builds dist/pluteus inside it from this checkout (mounted read-only) and copies it to OUTPUT_DIR (default ./dist-linux).
set -euo pipefail
here="$(cd "$(dirname "$0")/../.." && pwd)"
out="${1:-$here/dist-linux}"
case "$out" in /*) ;; *) out="$PWD/$out" ;; esac        # Docker treats a relative path as a volume NAME, so make it absolute
mkdir -p "$out"
docker build -q -t pluteus-linux-build "$here/tools/linux-build" > /dev/null
MSYS_NO_PATHCONV=1 docker run --rm -v "$here:/src:ro" -v "$out:/out" pluteus-linux-build bash -c '
  set -e
  mkdir /work && cp -r /src/pluteus /src/tools /src/pyproject.toml /src/pluteus.spec /src/README.md /src/LICENSE /work/ && cd /work
  pip install --no-cache-dir . > /dev/null
  pyinstaller pluteus.spec --noconfirm --distpath /out --workpath /tmp/build > /tmp/pyinstaller.log 2>&1 || { tail -30 /tmp/pyinstaller.log; exit 1; }
  chmod +x /out/pluteus
'
ls -la "$out/pluteus"

# Vendored third-party scripts

Copied unchanged from the npm registry so the server works offline on a LAN (no CDN). Do not edit these
files; to upgrade, fetch a newer package with `npm pack`, check its integrity hash, and replace them.

| File | Package | Version | Licence | npm integrity (sha512) |
|---|---|---|---|---|
| epub.min.js | epubjs (dist/epub.min.js) | 0.3.93 | BSD-2-Clause (LICENSE-epubjs.txt) | c06pNSdBxcXv3dZSbXAVLE1/pmleRhOT6mXNZo6INKmvuKpYB65MwU/lO7830czCtjIiK9i+KR+3S+p0wtljrw== |
| jszip.min.js | jszip (dist/jszip.min.js) | 3.10.1 | MIT or GPL-3.0-or-later, used under MIT (LICENSE-jszip.txt) | xXDvecyTpGLrqFrvkrUSoxxfJI5AH7U8zxxtVclpsUtMCq4JQ290LY8AW5c7Ggnr/Y/oK+bQMbqK2qmtk3pN4g== |

epub.js needs JSZip as a global, so jszip.min.js is loaded first.

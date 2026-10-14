# Vendored third-party scripts

Copied unchanged from the npm registry so the server works offline on a LAN (no CDN). Do not edit these
files; to upgrade, fetch a newer package with `npm pack`, check its integrity hash, and replace them.

| File | Package | Version | Licence | npm integrity (sha512) |
|---|---|---|---|---|
| epub.min.js | epubjs (dist/epub.min.js) | 0.3.93 | BSD-2-Clause (LICENSE-epubjs.txt) | c06pNSdBxcXv3dZSbXAVLE1/pmleRhOT6mXNZo6INKmvuKpYB65MwU/lO7830czCtjIiK9i+KR+3S+p0wtljrw== |
| jszip.min.js | jszip (dist/jszip.min.js) | 3.10.1 | MIT or GPL-3.0-or-later, used under MIT (LICENSE-jszip.txt) | xXDvecyTpGLrqFrvkrUSoxxfJI5AH7U8zxxtVclpsUtMCq4JQ290LY8AW5c7Ggnr/Y/oK+bQMbqK2qmtk3pN4g== |

epub.js needs JSZip as a global, so jszip.min.js is loaded first.

## pdf.js (PDF viewer)

Copied unchanged from the npm package, **legacy build** (broader browser support), into `pdfjs/`:

| File | Package | Version | Licence | npm integrity (sha512) |
|---|---|---|---|---|
| pdfjs/pdf.min.mjs | pdfjs-dist (legacy/build/pdf.min.mjs) | 6.4.299 | Apache-2.0 (LICENSE-pdfjs.txt) | AVl138zALtfaAPvADulE0PZThbYzCBS79nL4pOSL/6Sm/4AH5A21BD9VHt97OlCuzJuCpmeZtAtkinisF4Vb1g== |
| pdfjs/pdf.worker.min.mjs | pdfjs-dist (legacy/build/pdf.worker.min.mjs) | 6.4.299 | Apache-2.0 | (same package) |
| pdfjs/standard_fonts/ | pdfjs-dist (standard_fonts/) | 6.4.299 | Foxit and Liberation licences (LICENSE_FOXIT, LICENSE_LIBERATION inside) | (same package) |

Also copied unchanged from the same package: `pdfjs/wasm/` (openjpeg = JPEG 2000, jbig2, qcms colour profiles, their
no-wasm fallbacks and licences; scanned PDFs use JPEG 2000 and JBIG2 heavily). The CSP carries `'wasm-unsafe-eval'` for
them, which allows WebAssembly only, not `eval`. `quickjs-eval.*` (PDF form scripting) is left out on purpose.

Left out on purpose: `cmaps/` (1.5 MB, only needed for some CJK/legacy-encoded PDFs), the source maps and the full viewer
UI. The minified files contain no `eval`; the worker has one `new Function` in a polyfill fallback that a current browser
never reaches.

## hls.js (live channels)

Copied unchanged from the npm package (`dist/hls.min.js`), loaded only on the channel page (a station file with a video or HLS stream):

| File | Package | Version | Licence | npm integrity (sha512) |
|---|---|---|---|---|
| hls.min.js | hls.js (dist/hls.min.js) | 1.7.3 | Apache-2.0 (LICENSE-hlsjs.txt) | MsPlx6yVW4Qv4C7mEVou4/gk/5cN2dVxOTnrNjPSmyH2f0Ln+/UkG10Ax1PE6OvrmTVPKgQYozTtBaPOuCzq7A== |

The page runs it with `enableWorker: false` (no blob worker), so the content policy only needs `connect-src`, `media-src` and `blob:` for the media source on that one page.

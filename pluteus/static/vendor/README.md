# Vendored third-party scripts

Copied from the npm registry so the server works offline on a LAN (no CDN). Do not edit these
files; to upgrade, fetch a newer package with `npm pack`, check its integrity hash, and replace them.
All are unchanged except `epub.min.js`, which carries one small patch (see "The patch to epub.min.js" below): it no
longer matches the hash in the table, and the patch must be made again after an upgrade.

| File | Package | Version | Licence | npm integrity (sha512) |
|---|---|---|---|---|
| epub.min.js | epubjs (dist/epub.min.js) | 0.3.93 | BSD-2-Clause (LICENSE-epubjs.txt) | c06pNSdBxcXv3dZSbXAVLE1/pmleRhOT6mXNZo6INKmvuKpYB65MwU/lO7830czCtjIiK9i+KR+3S+p0wtljrw== |
| jszip.min.js | jszip (dist/jszip.min.js) | 3.10.1 | MIT or GPL-3.0-or-later, used under MIT (LICENSE-jszip.txt) | xXDvecyTpGLrqFrvkrUSoxxfJI5AH7U8zxxtVclpsUtMCq4JQ290LY8AW5c7Ggnr/Y/oK+bQMbqK2qmtk3pN4g== |

epub.js needs JSZip as a global, so jszip.min.js is loaded first.

### The patch to epub.min.js (2026-11-12, code review card 37)

The integrity hash above is that of the published `dist/epub.min.js`. The vendored file differs from it in three
expressions, all in the default view manager's page turning (`next()` and `prev()`), so **the file no longer matches
the published hash**.

**Why.** To decide whether the chapter has another page, epub.js compares the scroll offset of the page container
with its width, exactly. On a screen whose device pixel ratio is not a whole number (most Android phones: 2.625, 2.75;
a laptop at 125 % or 150 %) the browser keeps scroll offsets on whole device pixels, so an offset set to 411 reads
back as 411.0476, and the error grows with every page. Three faults followed, each reproduced in headless Chrome
started with `--force-device-scale-factor` (`tests/test_review2_web_epub_last_page_browser.py`; it fails on the
published file):

1. Forward, left-to-right book: on the second-to-last page the sum overshot `scrollWidth` by a fraction of a pixel, and
   the reader went to the next chapter **without showing the chapter's last page** (ratios 1.25, 1.5, 2.625, 2.75;
   not at 1 or 3).
2. Back, left-to-right book: on the chapter's first page the offset could be a pixel or so above 0, and one press
   moved that pixel instead of opening the previous chapter (seen at ratio 1.5).
3. Forward, right-to-left book: on the chapter's last page the test still said "another page", and the reader stayed
   there for good (seen at ratio 2.625).

**What.** Each test now allows half a page (`this.layout.delta/2`) of slack. A one-pixel tolerance would not do:
the error is per page and adds up. Exact text, before and after:

| Where | Before | After |
|---|---|---|
| `next()`, horizontal, left to right | `this.container.scrollLeft+this.container.offsetWidth+this.layout.delta<=this.container.scrollWidth?` | `this.container.scrollLeft+this.container.offsetWidth+this.layout.delta<=this.container.scrollWidth+this.layout.delta/2?` |
| `prev()`, horizontal, left to right | `this.container.scrollLeft>0?this.scrollBy(-this.layout.delta,0,!0)` | `this.container.scrollLeft>this.layout.delta/2?this.scrollBy(-this.layout.delta,0,!0)` |
| `next()`, horizontal, right to left, negative scroll offsets (every current browser) | `this.container.scrollLeft+-1*this.layout.delta>-1*this.container.scrollWidth?` | `this.container.scrollLeft+-1*this.layout.delta>-1*this.container.scrollWidth+this.layout.delta/2?` |

Left as published, because no fault was reproduced there: `prev()` in a right-to-left book
(`this.container.scrollLeft<0?`), the two right-to-left branches for browsers with the old "default" scroll type
(`"default"===this.settings.rtlScrollType`), and the vertical branch. They have the same exact comparisons; should a
right-to-left book ever need a press too many going back, give them the same slack.

## mpegts.js (MPEG-TS channels)

Copied unchanged from the npm package, plays the raw MPEG-TS streams that an IPTV panel such as Dispatcharr serves (hls.js plays only HLS).

| File | Package | Version | Licence | npm integrity (sha512, of the whole package) |
|---|---|---|---|---|
| mpegts.min.js | mpegts.js (dist/mpegts.js, already minified) | 1.8.2 | Apache-2.0 (LICENSE-mpegtsjs.txt) | cZYMa5muASH55wrS6JK5IGkUqnCA96c0f+WdnW91jIK9s/N6NzjmTQDJNYg9A5u/G2Q5nj2b3w1Zwzze/9guxA== |

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

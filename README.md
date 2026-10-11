# pluteus

A small, filesystem-first book and comic server: browse your real folder tree, read it from an
OPDS app, download files, with per-user logins. Python, no Java, low memory, no scanning step.

**Status: Phase 1 prototype (0.1.0).** Named after the *pluteus*, the sloping shelf or bookcase of a
medieval library. Pronounced "PLOO-tee-us".

## Why filesystem-first

Library servers that index everything into a database flatten the folder tree and need long
rescans on a multi-terabyte library. pluteus does the opposite:

- the **folder tree is the catalogue**: every folder is one OPDS feed and one web page, read live
  with `os.scandir()` when you open it, so it works the moment it starts and never needs a rescan;
- numbering such as `3.23 - Vertigo` sorts naturally (3.9 before 3.10) and is hidden in display names;
- nothing is cached yet. Later phases add a disposable SQLite cache (search, metadata), never a source of truth.

## Run it

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
copy config.example.toml config.toml
.\.venv\Scripts\python -m pluteus hash-password          # paste the output into config.toml
.\.venv\Scripts\python -m pluteus check-config --config config.toml
.\.venv\Scripts\python -m pluteus serve --config config.toml
```

Then open `http://localhost:8080/` in a browser, or add `http://<host>:8080/opds` as a catalogue in
an OPDS reader (KOReader, Panels, Moon+ Reader, Librera...). Credentials use HTTP Basic.
Put it behind HTTPS (a reverse proxy such as Caddy) before exposing it beyond your LAN.

## Users (command line)

No admin web page is needed for a household: manage users with the CLI, which edits `config.toml` in
place (your comments are kept, the old file is saved as `config.toml.bak`, and an edit that would make
the config invalid is refused before anything is written).

```powershell
python -m pluteus user list   --config config.toml
python -m pluteus user add    erlend --config config.toml --adult              # asks for a password (min 8 chars)
python -m pluteus user add    guest  --config config.toml --roots books
python -m pluteus user set    guest  --config config.toml --roots books,comics   # or --all-roots
python -m pluteus user set    erlend --config config.toml --password / --adult / --no-adult
python -m pluteus user remove guest  --config config.toml                      # asks to confirm (or --yes)
```

A running server picks up **user** changes within about two seconds, with no restart, and a removed
or changed user stops working immediately. Other settings (roots, port, ...) still need a restart.
`--password-stdin` reads the password from stdin for scripts.

**TOML scoping:** a key belongs to the `[table]` header above it. Keep all settings in `[server]`; a setting
typed under `[[roots]]` or `[[users]]` is refused with an error instead of being silently ignored.

## Endpoints

| URL | What |
|---|---|
| `/` | web: list of your roots |
| `/browse/{root}/{path}` | web: folder listing, paged |
| `/opds`, `/opds/{root}/{path}` | OPDS 1.2 navigation / acquisition feeds, paged (`?page=N`) |
| `/dl/{root}/{path}` | download, with `Range` support |
| `/healthz` | unauthenticated, for Docker health checks |

## Reader apps (tested)

| App | Result |
|---|---|
| **PocketBook Reader, Android** (2026-10-08/10) | Browses nested folder feeds, downloads EPUBs, logs in. **Login must be entered in the app's own username/password fields.** Credentials embedded in the URL (`http://user:pw@host/opds`) are NOT sent by the app, so the server answers 401. Anonymous access (`anonymous_user`) also worked. CBZ and title/author display not yet checked. |
| KOReader, other Android/iOS apps | Not tested yet. |

Apps that drop URL credentials and have no login fields can use `anonymous_user` (LAN only, restricted user).

## Docker

`Dockerfile` + `docker-compose.example.yml` were built and run on Docker Desktop (Windows, 2026-10-11) with a small
sample library. Checked and passing: the image builds (191 MB, about 20 s); it runs as an unprivileged user
(uid 10001); healthcheck turns healthy; login, OPDS, EPUB metadata, downloads with Range, the reader page and the
progress API all answer through the published port; every packaged CSS, script, icon and theme file is inside the
image; sessions and reading progress survive `docker restart`; users added from the host or from inside the
container (`docker exec ... python -m pluteus user add ...`) reach the running server within seconds. Idle use
is about 77 MB of memory.

Setup notes:

- In `config.toml` set `host = "0.0.0.0"` and `data_dir = "/data"`, and use the container paths of the mounts.
- `/data` (state, metadata cache, backups) needs a volume, or sessions and progress are lost on recreate. A named
  volume is the safe default. A Windows folder mounted there also worked in a light test (20 writes, restart,
  integrity check ok), but that was not stress-tested.
- Mount the config **folder** (`./config:/config`), not the single file: replacing a file can leave a single-file
  mount pointing at the old one on Linux hosts (known Docker behaviour; not seen on Docker Desktop for Windows).
- **Not tested:** speed on real libraries. Docker Desktop on Windows reaches Windows drives through a slower layer;
  benchmark on the machine that owns the disks before choosing Docker over running natively.

## EPUB metadata

EPUB is the main use, so OPDS entries and web pages show what is inside each book: title, authors,
series and number (Calibre `calibre:series` tags and EPUB 3 collections), language and year. Only the
books on the page being shown are read (50 by default), never the whole library. Results are cached in
SQLite (WAL) keyed on path + size + modified time: a changed file is re-read, a damaged cache file is set
aside and rebuilt, and deleting it is always safe. Books that cannot be read fall back to their file name.
Measured on 50 real EPUBs over SMB: about 1 s cold, 18 ms warm. Listing order is still by file name.

## Access control

- **Per user:** `roots = [...]` limits which roots a user sees (omit for all).
- **Adult folders:** put an empty file named `.adult` (configurable) in any folder; it and everything
  below it is hidden from users without `adult = true`, in feeds, pages and direct URLs (404).
  Works at any depth, so an adult imprint can sit inside a mainstream publisher folder.
- **Hardening:** path segments are validated (no `..`, no `/`, and on Windows no `\` or `:`);
  the real path (symlinks, junctions) must stay inside the root; only configured file
  extensions are served; hidden and excluded items are indistinguishable from missing ones.

## Windows notes

- All real paths use the `\\?\` form. Windows normally strips a trailing dot from folder names
  (`Lescroart, John T.` becomes `Lescroart, John T`), which makes such folders unreachable;
  `fs.longpath()` avoids that, and **must not call `os.path.abspath()` on absolute paths**.
- Run natively on the machine that owns the disks if you can. Docker Desktop on Windows reaches
  Windows drives through a slow file-sharing layer; benchmark before relying on it.

## Tests

```powershell
.\.venv\Scripts\python -m pytest
```

29 tests build a fake library in pytest's `tmp_path`: login, per-user roots, natural order and
paging, display names, exclusions, the adult marker, path traversal, Range downloads, and a folder
whose name ends in a dot. A read-only smoke test against the real libraries over SMB showed
feed times of 10-40 ms, including a 476-file folder.

## Hard requirements from the owner (to shape every later phase)

- **Skinnable readers.** The EPUB reader and the comic/page reader must be restyled by CSS, not only the
  library pages (in Ubooquity the readers ignore its theme system). Build the readers with CSS variables,
  replaceable assets and themeable menus from the first line.
- **Themes hot-reload** and are not cached for an hour.
- **Crash-safe store.** Ubooquity's embedded database corrupted on this library. The planned cache is
  SQLite in WAL mode, disposable and rebuildable, scanned incrementally, never held whole in memory.
  Test with a very large synthetic library.
- **Reading progress** per user, and **reverse-proxy base paths** (`base_path` already works).

## Not yet (planned)

In priority order (search is only a nice-to-have, decided 2026-10-08):

1. Reader-app compatibility pass (KOReader plus one iOS and one Android app): MIME type for CBZ,
   absolute vs relative links, auth behaviour.
2. **Phase 2**: web login page and sessions, the skinnable EPUB and comic readers (CSS variables,
   hot-reloading themes), CBZ page streaming (OPDS-PSE), reading progress per user (this is where the
   crash-safe SQLite store first appears).
3. **Phase 3**: per-user upload/download areas, quotas, WebDAV (wsgidav).
4. Docker: tested (see "Docker" below). Still to do: benchmark native vs Docker on the server's own disks.
5. No rate limiting on failed logins yet.
6. **Nice to have**: filename search (background crawler, SQLite FTS5 cache keyed on path+size+mtime),
   OpenSearch link in feeds, epub/cbz metadata in feeds.
7. No covers by design (not wanted); no CBR support (not needed).

## Icon

The book icon (browser tab, home-screen icon, web manifest, sign-in card) is made from the owner's artwork of an
antique leather book, with a gold-foil "P" stamped on the cover in perspective. `tools/make_icon.py` rebuilds all
sizes from the original picture (needs Pillow; run with `--debug` to see where the stamp lands):

    python tools/make_icon.py path/to/pluteus.jpg pluteus/static/icon

A theme can hide the logo on the sign-in card with `.pl-logo { display: none; }`.

## Reading in the browser (EPUB)

Every EPUB in a folder listing has a **Read** link. The reader is epub.js (BSD-2-Clause, vendored with its licence
in `pluteus/static/vendor/`, no CDN) with an interface built from the theme's own variables and classes:

- contents menu, text size, font, line spacing, paged or scrolling layout, full screen, dark/light toggle;
- keys (arrows, page up/down, space), edge taps and swipes on a phone;
- **reading progress is saved per user on the server**, so a book opens where you stopped, on any device, and
  the home page shows "Continue reading" for unfinished books;
- the book text follows the theme: `--pl-book-bg/-fg/-link/-font/-line` feed a `reader-content.css` that is loaded
  into the book's page. A theme replaces that file to restyle the text itself, and it is re-applied live when the
  scheme changes. Book scripts never run (the book's page is sandboxed), and the page counting used for the
  percentage starts only after the first page is shown.

The reader is a convenience: the OPDS feed and plain downloads for Android reader apps are the main way to read.
The progress API is `GET/PUT /api/progress/{root}/{path}` (JSON `{locator, percent}`; same-site writes only).

## Reading comics (CBZ)

Every `.cbz` has a **Read** link too: a deliberately simple one-page-at-a-time reader (no libraries, `reader-comic.js`).
Fit to page or fit to width (scrolls down), arrows / space / page keys / Home / End, edge taps and swipes, click the
progress bar to jump, full screen, dark/light. The page number is saved per user like an EPUB's position, so comics
reopen where you stopped and appear under "Continue reading" until finished.

The server never extracts anything: it reads the zip's central directory once to list the pages (images only, natural
order, `__MACOSX`/dot files/`Thumbs.db` skipped; the list is cached in the SQLite cache), then serves one entry per
request from `GET /cbz/{root}/{path}?page=N` (0-based). Pages are cacheable (ETag) and bounded: at most 5000 pages and
80 MB per page, enforced while reading, so a zip header that lies cannot blow up memory. Measured on a real 184-page
graphic novel over SMB: about 0.1-0.2 s per page.

Not built: two-page spreads, right-to-left, webtoon scrolling, page resizing, ComicInfo.xml titles.

### OPDS page streaming (PSE) for Android comic apps

Each CBZ in an OPDS feed also carries an **OPDS-PSE v1.2** stream link, so apps that support it (Panels, Chunky,
KOReader and others) can read a comic page by page from the server instead of downloading the whole file first:

    <link rel="http://vaemendis.net/opds-pse/stream" type="image/png"
          href="/cbz/{root}/{path}.cbz?page={pageNumber}" pse:count="184" pse:lastRead="6" pse:lastReadDate="..."/>

`{pageNumber}` is 0-based (as the spec says) and the same endpoint the web reader uses; `pse:lastRead` is 1-based and
comes from the user's saved reading position, so an app can resume where the web reader stopped. The link `type` is
always one the spec allows (jpeg/png/gif, from the first page; clients decode by the real bytes, so WebP pages work).
Pages are served as stored: `{maxWidth}` is not used. A comic whose page list cannot be read simply gets no stream link
and stays downloadable. Built from the spec text and tested against it; **not yet tried in a real Android app**.

The first time a feed page lists its comics, each CBZ's page list is read once (a full page of 50 comics took about
2.2 s over SMB, 22 ms afterwards, because the lists are cached). Set `opds_pse = false` in `[server]` to turn it off.

## File viewers (show all files)

With `show_all_files = true` (the default) the web listing shows **every** file, and each one opens in a viewer chosen
by its type at `/view/{root}/{path}`. Set `show_all_files = false` to go back to a books-only server (only the types in
`extensions` are listed or served). **OPDS feeds always list books only**, whatever this is set to.

| Type | Viewer |
|---|---|
| EPUB, CBZ | the book readers (above) |
| images (jpg, png, gif, webp, avif, bmp, ico, svg) | fit to window / to width / actual size (click), the other images of the folder one key press away, next image preloaded |
| audio (mp3, m4a, aac, ogg, opus, wav, flac), video (mp4, m4v, webm, ogv, mov, mkv) | the browser's own player; the position is saved per user and resumes; Range requests work, so seeking is instant |
| PDF | pdf.js (vendored under `static/vendor/pdfjs`, nothing from a CDN) draws one page at a time; range requests show page 1 of a big file before it has downloaded; fit width/page, zoom, invert for night reading, page seek bar, swipe/tap/keys; the page is saved per user. Needs `blob:` in the CSP `style-src` (set) |
| text, code, logs, csv, json, xml, html source, ... | server-rendered, always HTML-escaped; encoding detected (UTF-8/16/32 with BOM, UTF-8, Windows-1252, Latin-1) and named in the footer; text size and wrap; shows the first `text_view_kb` KB (default 1024) with a clear notice and a download link |
| **anything else** | plainly-text files (a README, an unknown extension) open as text; binary files get an info page (type, size, date) with **Download** and **View as text**, which opens even binary data as text |

Progress (position, page, time) is kept for books, PDFs, audio and video and feeds "Continue reading".

### How files are served safely

Listing every file means serving files from the same web address as the login, so active content must never run there:

- `/dl` is **always an attachment** (never displayed), with `nosniff`, so an HTML or SVG file can only be saved.
- `/raw` is the only inline route, and only for images, audio, video and PDF, each with **the type pluteus states** (a file
  named `x.png` that contains HTML is still served as `image/png`). Nothing scriptable (html, xml, js, css, ...) is ever inline.
- SVG is only shown through an `<img>` tag (scripts never run there) and, if opened directly, carries
  `Content-Security-Policy: sandbox`.
- Text is rendered escaped inside a `<pre>`, so viewing an HTML file shows its source.
- Hidden folders, the `.adult` marker, excludes and per-user roots apply to every route, exactly as before.
- With `show_all_files`, pluteus **refuses to start** if `data_dir`, `cache_dir`, `themes_dir` or the config file itself sit
  inside a library root (the sessions and password hashes would otherwise be listed and downloadable).

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
4. Docker image: the `Dockerfile` and compose file are written but **untested**; benchmark native vs Docker on the server first.
5. No rate limiting on failed logins yet.
6. **Nice to have**: filename search (background crawler, SQLite FTS5 cache keyed on path+size+mtime),
   OpenSearch link in feeds, epub/cbz metadata in feeds.
7. No covers by design (not wanted); no CBR support (not needed).

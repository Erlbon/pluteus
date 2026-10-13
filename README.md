# pluteus

A small, filesystem-first book and comic server: browse your real folder tree, read it from an
OPDS app, download files, with per-user logins. Python, no Java, low memory, no scanning step.
Built as a light replacement for Ubooquity, for people who keep their library as plain folders on disk
(tested on about 10 TB of books and comics).

**Status: 0.6.0, in daily use, young.** Named after the *pluteus*, the sloping shelf or bookcase of a
medieval library. Pronounced "PLOO-tee-us".

## What you get

- **OPDS feeds** for reader apps (with page streaming for comics), and a **web UI** that shows the real folder tree.
- **Readers in the browser** for EPUB, comics (CBZ, CBR, CB7, CBT) and PDF, with reading progress per user; **viewers** for images, audio
  and m4b audiobooks, video, text, Markdown, CSV and zip files.
- **Per-user logins**, per-user folder access, `.adult`, `.private` and `.hidden` folder markers, and a personal
  **upload folder** per user (also from the Android share sheet).
- **Safe to put on the internet** behind HTTPS: login lockouts that escalate, known devices, same-site checks, a
  read-only view of your libraries (pluteus never writes to them).
- A **web admin page** on a separate, local-only port: status, locks, users.
- Runs on **Docker** (hardened compose example included) or natively on Windows and Linux; skinnable themes.

## What it deliberately does not do

No library database, no search across the whole library, no "recently added", no duplicate detection, no cover
images, no online metadata lookups (it only reads the title, author and series already inside your EPUBs), no HEIC conversion, no accounts for sign-up. Anything that would need scanning the
tree is left out on purpose: that lightness is the point. It is not a replacement for Nextcloud, Calibre or Jellyfin.

## Why filesystem-first

Library servers that index everything into a database flatten the folder tree and need long
rescans on a multi-terabyte library. pluteus does the opposite:

- the **folder tree is the catalogue**: every folder is one OPDS feed and one web page, read live
  with `os.scandir()` when you open it, so it works the moment it starts and never needs a rescan;
- numbering such as `3.23 - Vertigo` sorts naturally (3.9 before 3.10) and is hidden in display names;
- the only cache is a disposable SQLite one for the title, author and series read from the EPUBs on the page you are
  looking at; it is never a source of truth and can be deleted at any time.

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
an OPDS reader (KOReader, Panels, Moon+ Reader, Librera...). Reader apps log in with HTTP Basic; browsers
get a login page and a session cookie.
Put it behind HTTPS (a reverse proxy such as Caddy) before exposing it beyond your LAN.

## Download (no Python needed)

Each [release](https://github.com/Erlbon/pluteus/releases) has **`pluteus.exe`** (Windows) and **`pluteus-linux-x86_64.tar.gz`** (Linux,
runs on most current distributions). Both are the same program as `python -m pluteus`.

**Just run it** (double-click `pluteus.exe`, or `./pluteus`): the first time it asks for an administrator name and a password (you type
it, it is not shown), saves its settings in your own settings folder (`%APPDATA%\Pluteus` on Windows, `~/.config/pluteus` on Linux),
starts, and opens the admin page in your browser, where the **Libraries** tab adds your book and comic folders with a folder picker.
After that, running it again just starts the server. Other commands: `pluteus share FOLDER` (below) and `pluteus serve --config config.toml`. They are **not code-signed**, so Windows SmartScreen may warn on the first run ("More info",
then "Run anyway"). RAR and 7z comics (CBR, CB7) need `bsdtar` (built into Windows 10 and later; `libarchive-tools` on Debian and
Ubuntu). Docker stays the recommended way to run a permanent server. The Linux download includes a systemd unit.

## Adding a library

On the admin page (`http://localhost:8101/`, once `admin_port` is set), the **Libraries** tab lists your libraries and adds or removes
them: type the folder, or press **Browse folders** to pick it (the picker works for a browser on the same computer, which is the case
for the Windows and Linux programs; it only ever lists folders). A new library is in use at once and users whose folders are set to
"All folders" see it straight away. Pluteus never writes to a library folder. **In Docker** the folder must also be mounted into the
container (a `volumes:` line in `docker-compose.yml`); type the path *inside* the container, e.g. `/library/music`, or set
`library_bases = ["/library"]` in `config.toml` to get the picker below your mounted folders.

## Quick share (no config file)

```powershell
python -m pluteus share D:\Photos                 # read-only, open to anyone on this network
python -m pluteus share D:\Photos --private       # only people who scan the QR code get in
python -m pluteus share D:\Photos --upload        # guests may upload into ~/Pluteus uploads (implies --private)
```

It serves the folder(s) at once, prints the address and a **QR code** (scan it with a phone on the same Wi-Fi), and stops when you press
Ctrl+C. Nothing is written to the shared folders; uploads go to a separate folder (`--upload DIR` to choose it) and the session data
lives in a temporary folder that is deleted on exit. The default is open and read-only; `--private` makes the QR code carry a one-time
access link, which stops working when the share stops. Options: `--port N` (first port to try), `--address IP` (if the computer has
several network addresses and the first is the wrong one), `--no-qr`. For a permanent library with accounts, use the config file.

## Admin page (web, local network only)

Set `admin_port` (and, in Docker, `admin_host = "0.0.0.0"`) and pluteus also serves an **admin page** on that **second port**.
It works in Docker, on Linux and on Windows alike, with nothing to install on the machine you browse from.

- **Reach:** `http://<server>:<admin_port>/`. It is a separate listener, so a reverse proxy that routes only the main port
  (Caddy, nginx) never exposes it. **Never route or forward this port to the internet**; in Docker publish it on the
  LAN address only (`"<lan-address>:8101:8101"`). The page says so at the bottom of every screen.
  An administrator who opens the main site directly on the local network (not through a proxy) sees an **Admin** link
  in the top bar, pointing at the same host name or address on the admin port (publish it under the same number as
  `admin_port`). Through the public address the link never appears.
- **Update and security check (opt-in):** with `update_check = true` the Status page asks GitHub once a day, in the background,
  for the newest release tag and for published security advisories of `update_repo` (default `Erlbon/pluteus`). A dot beside the version
  is green (up to date), yellow (a newer version exists; update with `git pull` and a rebuild), grey (could not check) or red, with a red
  notice on top, when a published advisory names your version as vulnerable. Nothing about your library or users is sent. For the
  maintainer: a red flag is raised by publishing a GitHub security advisory (repository, Security, Advisories) with the affected
  range, for example `< 0.3.2`, and the patched version.
- **Who:** only users with `admin = true` (off for everyone by default; set it with `user add NAME --admin`,
  `user set NAME --admin`, in `config.toml`, or from the desktop app). Signing in is the browser's own login prompt; wrong
  passwords are counted by the same throttle as the main site, and every change needs a same-site request.
- **Status:** version and uptime, every library (ok or not, free disk), the upload folders and their sizes, where the
  state lives and its backups, and counts of users, sessions, known devices and locked names.
- **Security:** the accounts with wrong passwords or locks, with **Clear** to lift a lock at once; the **known devices**
  (browsers that have signed in, with first and last seen and the last address) with **Forget**; and **Sign out
  everywhere** for a user (every session and known device, and remembered reader-app logins), for when a password may
  have leaked.
- **Users:** add, change a password, choose folders and rights, remove. It uses the same checked editor as the command
  line, so a bad change is refused with the reason and nothing is written; you cannot remove yourself or the last admin.
  Changes reach the running server within a few seconds. Other settings live in `config.toml` and need a restart.
- **First admin** (before there is a page to sign in to), in Docker:
  `docker compose exec pluteus python -m pluteus user set NAME --admin --config /config/config.toml`.

## Admin app (a window, Windows) and users (command line)

**`python -m pluteus admin`** (or `pluteus-admin`, which pip installs next to it) opens a small window, standard library only:

- **Users:** add a user, change a password, choose which folders a user may open, adult access and the personal upload
  folder, remove a user. Passwords are typed twice and hashed on the spot.
- **Config file:** the whole `config.toml` as text, with **Check** (is it valid?), **Save** (validated first, the old file
  is kept as `config.toml.bak`) and a warning if the file changed on disk since you opened it.
- It edits the **file**, not the running server, so nothing extra is exposed to the network or the internet. Point it at
  the server's config through a network drive (for the Docker setup: `...\pluteus\config\config.toml`); it remembers the
  last file. A running server picks up user changes within seconds; other settings need `docker compose restart pluteus`.

The same user commands are available without a window, which edits `config.toml` in
place (your comments are kept, the old file is saved as `config.toml.bak`, and an edit that would make
the config invalid is refused before anything is written).

```powershell
python -m pluteus user list   --config config.toml
python -m pluteus user add    alice --config config.toml --all-roots --adult  # asks for a password (min 8 chars)
python -m pluteus user add    guest  --config config.toml --roots books
python -m pluteus user set    guest  --config config.toml --roots books,comics   # or --all-roots
python -m pluteus user set    alice --config config.toml --password / --adult / --no-adult / --upload / --no-upload
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

Docker and a native install are equally supported: **[docs/running.md](docs/running.md)** compares them and has the steps for
both (including moving from one to the other).

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
- **Speed (measured):** on Docker Desktop for Windows with the libraries bind-mounted read-only, a folder feed takes
  roughly 40 to 50 ms, and a folder of 900 comic archives about 40 ms warm (listings read size and date only for the
  entries on the page). The cost grows with the number of files in ONE folder, not with the size of the library.

## EPUB metadata

EPUB is the main use, so OPDS entries and web pages show what is inside each book: title, authors,
series and number (Calibre `calibre:series` tags and EPUB 3 collections), language and year. Only the
books on the page being shown are read (50 by default), never the whole library. Results are cached in
SQLite (WAL) keyed on path + size + modified time: a changed file is re-read, a damaged cache file is set
aside and rebuilt, and deleting it is always safe. Books that cannot be read fall back to their file name.
Measured on 50 real EPUBs over SMB: about 1 s cold, 18 ms warm. Listing order is still by file name.

## Access control

- **Per user:** `roots = ["books", "comics"]` lists the roots a user may open; `roots = ["*"]` means every root, also
  ones added later. **No list means no folders**: a new user starts with access to nothing and you grant it
  (the admin page, the desktop app and `user add --roots ... | --all-roots` all make you choose). A server started with
  such a user prints a warning.
- **Adult folders:** put an empty file named `.adult` (configurable) in any folder; it and everything
  below it is hidden from users without `adult = true`, in feeds, pages and direct URLs (404).
  Works at any depth, so an adult imprint can sit inside a mainstream publisher folder.
- **Private folders:** put a file named `.private` in a folder and list the user names that may open it, one per
  line (or comma separated, `#` starts a comment). Everyone else gets a 404 for it and everything below it, in feeds,
  pages and direct URLs, `adult = true` included. An empty file names nobody, so the folder is closed to everyone.
  Nested `.private` files must all name the user.
- **Unlisted folders:** a `.hidden` file leaves the folder out of its parent's listing and the OPDS feeds. Anyone
  with the link can still open it. Names written inside (same format) still see it listed; an empty file names nobody.
- Marker files are checked once a minute per folder, never indexed. Changes show within about a minute. The file
  names are settings (`adult_marker`, `private_marker`, `hidden_marker`).
- **Password guessing (for a server on the internet):** 5 wrong passwords for one user name within 5 minutes lock that name,
  and every further lock of the same name is 6 times longer (5 minutes, 30 minutes, 3 hours, then 6 hours at most). A name
  left alone for a day, or with a good login, starts again from the short lock. Every new lock is logged
  (`login for 'name' is locked for ... s ... (lock number N)`), never the guesses.
  - **Known devices:** a browser that has logged in as that user before keeps a device cookie (a year; only its hash is
    stored; forgotten when the password changes). Its mistakes count only against that device (5 per 5 minutes) and a
    locked account does not stop it, so someone hammering your name cannot keep you out of your own browser.
  - **Reader apps:** credentials that verified once are let through (14 days) while the name is locked; new guesses are not.
  - **Behind Docker Desktop's NAT** every visitor looks like one private address, so there is no per-address limit then; the
    per-name lock and the device rule do the work. A restart of pluteus clears all locks.
  - Use a long password for any account that is reachable from the internet: 8 characters is the minimum, not the goal.
- **Hardening:** path segments are validated (no `..`, no `/`, and on Windows no `\` or `:`);
  the real path (symlinks, junctions) must stay inside the root; only configured file
  extensions are served; hidden and excluded items are indistinguishable from missing ones.

## Windows notes

- All real paths use the `\\?\` form. Windows normally strips a trailing dot from folder names
  (`Lescroart, John T.` becomes `Lescroart, John T`), which makes such folders unreachable;
  `fs.longpath()` avoids that, and **must not call `os.path.abspath()` on absolute paths**.
- Docker Desktop on Windows reaches Windows drives through a file-sharing layer that costs about 0.7 ms per file
  stat, which is why listings read size and date only for the entries shown. Measured, it is fast enough.

## Tests

```powershell
.\.venv\Scripts\python -m pytest
```

The tests (about 260) build a fake library in pytest's `tmp_path`: login and sessions, throttling, per-user
roots, natural order and paging, sorting, display names, exclusions, the adult marker, path traversal (including
Windows short names), Range downloads, the readers, the viewers, and a folder whose name ends in a dot. A
read-only smoke test against the real libraries over SMB showed feed times of 10-40 ms, including a 476-file folder.

**Speed on the server (Docker Desktop, libraries bind-mounted, 2026-10-12):** the library roots and a big genre folder
list in 40-55 ms; a comic folder of 918 files lists in about 40 ms (it took 0.6 s before listings read the size and date of
only the 50 entries on the page: one system call per file costs about 0.7 ms across a Docker bind mount, where reading the
names costs 7 ms for all 918). Sorting a folder by size or date still reads every file once (about 0.65 s for 918 files).
Nothing is indexed, so a folder costs the same however big the library is.

## Design rules (from the owner)

- **Filesystem first.** Folders are read live from disk; there is no library-wide index, so nothing to rescan or
  corrupt, and no feature that needs one (global search, "recently added", duplicate detection) is planned.
  Folder listings can be sorted by name, type, size or modified date: that reorders the one folder being shown.
- **Light.** No new daemons or heavy dependencies; state is a few small SQLite files that are safe to lose.
- **Skinnable.** Every page and both readers are styled by CSS variables and replaceable theme files, which
  reload without a restart.
- **Behind a proxy.** `base_path` works whether the proxy strips the prefix or not; `trust_proxy`,
  `allowed_hosts` and `cookie_secure` cover HTTPS termination in front of it.

## Not yet (planned)

1. Reader-app compatibility pass on real devices (KOReader, an iOS app, Android comic apps with page streaming);
   PocketBook is the only one tried so far.
2. "Share to pluteus": on one real phone (Chrome 154, Android 16, a dynamic-DNS address behind Caddy with a valid certificate) Chrome refused to
   install the app ("This app cannot be installed"), even for a bare-bones manifest, while other sites installed fine. Cause unknown, so the
   Share-menu entry is unproven. A home-screen shortcut and uploads from the page itself work.
3. No covers by design (not wanted).

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
- **page width:** one centered page (about 50 rem, a comfortable line length) is the default, even on a wide screen;
  "Two pages" in the settings menu shows a spread on wide screens. The choice is remembered in the browser;
- keys (arrows, page up/down, space), edge taps and swipes on a phone;
- **reading progress is saved per user on the server**, so a book opens where you stopped, on any device, and
  the home page shows "Pick up where you left off" for unfinished books;
- the book text follows the theme: `--pl-book-bg/-fg/-link/-font/-line` feed a `reader-content.css` that is loaded
  into the book's page. A theme replaces that file to restyle the text itself, and it is re-applied live when the
  scheme changes. Book scripts never run (the book's page is sandboxed), and the page counting used for the
  percentage starts only after the first page is shown.

The reader is a convenience: the OPDS feed and plain downloads for Android reader apps are the main way to read.
The progress API is `GET/PUT /api/progress/{root}/{path}` (JSON `{locator, percent}`; same-site writes only).

## Reading comics (CBZ, CBR, CB7, CBT)

Every comic archive (`.cbz`, `.cbr`, `.cb7`, `.cbt`) has a **Read** link too: a deliberately simple one-page-at-a-time reader (no libraries, `reader-comic.js`).
**Zoom levels** (the fit button, or `+` `-` `0` `F`, or Ctrl + wheel): the whole page, 50 / 65 / 80 % of the window's width, the
full width, and 150 / 200 / 300 %; a zoomed page scrolls (drag with the mouse to pan) and the choice is remembered.
**Mouse wheel**: scrolls a page taller than the window, and a new push at its end (or the wheel on a whole page) turns the
page. Arrows, Space and Page Up / Down (Space scrolls a tall page first, then turns it), Home / End, edge taps and swipes,
click the progress bar to jump, full screen, dark/light. The page number is saved per user like an EPUB's position, so comics
reopen where you stopped and appear under "Pick up where you left off" until finished.

**Which archive it is** is decided by what the file contains, not by its name (many `.cbr` files are renamed ZIPs). ZIP and
TAR are read with Python's own libraries. **RAR and 7z need `bsdtar`** (libarchive): the Docker image installs it
(`libarchive-tools`, about 2 MB); on Windows 10 and later the system `tar.exe` is bsdtar; on Linux install `libarchive-tools`
(Debian, Ubuntu) or `libarchive`. pluteus runs it with a 30-second limit and an output limit, writing one page to its output;
nothing is extracted to disk. Not supported: password-protected and multi-part archives, and the oldest RAR variants. In a
user's **upload folder only ZIP comics open** (a stranger's RAR or 7z file is never handed to a C library); they can still
be downloaded. The admin Status page tells you whether bsdtar was found.

The server never extracts anything: for a ZIP it reads the central directory once to list the pages (images only, natural
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
| audio (mp3, m4a, **m4b**, aac, ogg, opus, wav, flac), video (mp4, m4v, webm, ogv, mov, mkv) | the browser's own player plus big buttons made for listening on a phone: **-30 s / +30 s**, **speed** (0.75x to 2.5x, remembered in the browser), a **sleep timer** (15, 30, 60 minutes or the end of the file). The position is saved per user and resumes; Range requests work, so seeking in a 1 GB audiobook is instant. When a file ends the next one in the folder starts (best effort: a browser may want a tap first). Lock-screen / notification controls (title, folder as album, skip, next/previous) through the Media Session API. Tried with a real 11-hour `.m4b` in a desktop browser (play, skips, speed, resume); **not yet on a phone**: whether playback continues with the screen off is up to the browser. Not built: chapter lists, tags and cover art from the file |
| PDF | pdf.js (vendored under `static/vendor/pdfjs`, nothing from a CDN) draws one page at a time; range requests show page 1 of a big file before it has downloaded; fit width/page, zoom, invert for night reading, page seek bar, swipe/tap/keys; the page is saved per user. Needs `blob:` in the CSP `style-src` (set) |
| Markdown (`.md`, `.markdown`) | rendered as a page: headings, lists, task lists, tables, code blocks, quotes, links; images and links to other files in the same library work. **Raw HTML in the file is shown as text, never run**; links may only go to http(s), mailto or the library; remote images are not loaded. A **Source** button shows the file as plain text. Files over 256 KB are shown as text instead. Rendered by the small `mistune` package |
| CSV / TSV (`.csv`, `.tsv`) | a table: separator guessed (comma, semicolon, tab, bar), sticky header row, zebra rows, numbers right-aligned, **click a header to sort** (numbers sort as numbers, also with decimal commas), a **filter box**, quoted cells with commas and line breaks handled. Shows at most 5000 rows and 100 columns (cut with a notice); anything that is not really a table falls back to text. **Source** shows the raw text |
| ZIP (`.zip`) | lists what is inside (path, size, date) with **View** for text and image entries and **Download** for any entry; nothing is unpacked to disk. Entries are chosen by their number, never by name. See "Zip files" below |
| text, code, logs, json, xml, html source, ... | server-rendered, always HTML-escaped; encoding detected (UTF-8/16/32 with BOM, UTF-8, Windows-1252, Latin-1) and named in the footer; text size and wrap; shows the first `text_view_kb` KB (default 1024) with a clear notice and a download link |
| **anything else** | plainly-text files (a README, an unknown extension) open as text; binary files get an info page (type, size, date) with **Download** and **View as text**, which opens even binary data as text |

Progress (position, page, time) is kept for books, PDFs, audio and video and feeds the **"Pick up where you left off"** box on the home page (a type icon, the library and folder, the percentage). A small **x** on a row takes the file off that list: its saved position is kept, and it comes back by itself the next time it is opened. The list is per user.

### How files are served safely

Listing every file means serving files from the same web address as the login, so active content must never run there:

- `/dl` is **always an attachment** (never displayed), with `nosniff`, so an HTML or SVG file can only be saved.
- `/raw` is the only inline route, and only for images, audio, video and PDF, each with **the type pluteus states** (a file
  named `x.png` that contains HTML is still served as `image/png`). Nothing scriptable (html, xml, js, css, ...) is ever inline.
- SVG is only shown through an `<img>` tag (scripts never run there) and, if opened directly, carries
  `Content-Security-Policy: sandbox`.
- Text is rendered escaped inside a `<pre>`, so viewing an HTML file shows its source.
- **Zip files** (`/zip/{root}/{path}?i=N&as=dl|raw|text`): an entry is picked by its number in the archive, so an entry
  named `../../x` or `C:\x` can never become a path; downloads are always attachments named with a sanitised local name;
  only images are shown inline (30 MB at most, with the type pluteus states, SVG sandboxed); the text viewer reads at most
  `text_view_kb` KB of an entry however large it is (a zip bomb costs one limit's worth of work); an entry that claims
  or turns out to be larger than the limit is refused; encrypted entries are listed but never opened; very large listings
  show the first 2000 entries. Names are decoded as UTF-8 when the archive did not say.
- Hidden folders, the `.adult` marker, excludes and per-user roots apply to every route, exactly as before.
- With `show_all_files`, pluteus **refuses to start** if `data_dir`, `cache_dir`, `themes_dir` or the config file itself sit
  inside a library root (the sessions and password hashes would otherwise be listed and downloadable).

## Uploads (personal folders)

Set `uploads_dir` and every user gets a private folder, `uploads_dir/<user name>`, shown as the top-level folder **uploads**
(web and OPDS, so a book you upload can be opened in a reader app straight away). There you can **upload** (several
files, with a progress bar), **create folders**, **rename** and **delete** (a folder only when it is empty).

- It is a **staging area**: pluteus never writes into the libraries. You sort what arrives into them on disk.
- **Streamed, not buffered:** the upload goes straight to a hidden temp file in the user's folder (no memory, no system
  temp folder, so it also works with a read-only container), then takes its final name. A name that already exists gets
  ` (1)`, ` (2)`...; nothing is ever overwritten. A dropped connection leaves nothing behind.
- **Names are made safe for Windows:** folders in a name are stripped, `< > : " | ? *` and control characters become `_`,
  trailing dots/spaces and leading dots are removed (leading-dot names are hidden by pluteus), `CON`/`NUL`/`COM1`... get a
  `_` in front, and the length is capped with the extension kept.
- **Limits** (`upload_max_mb`, optional `upload_quota_mb`) are enforced while the bytes arrive, a refused file costs no disk
  and does not stop the other files in the same request. The disk is also kept 512 MB from full.
- **Separation:** nobody sees another user's folder; the anonymous user and users with `upload = false` have none;
  rename/delete only work inside your own folder; the config refuses an `uploads_dir` that overlaps a library, `data_dir`,
  `cache_dir`, `themes_dir` or the config file, and user names that cannot be folder names.
- **Android "Share to pluteus":** when uploads are on, the web manifest declares a share target. Install pluteus from the
  browser's menu ("Add to Home screen" / "Install app"), then Share from any app and pick pluteus; the files land in the top
  of your upload folder. This needs **HTTPS** (browsers only install web apps from secure addresses, so put pluteus behind
  Caddy or similar) and **did not install on the one phone it was tried on** (see Not yet). The server side is tested; browsers differ on what they send
  with a share (no `Origin`), so the share route accepts a request the browser itself started (`Sec-Fetch-Site: none`).
- Needs the small `python-multipart` package (installed with pluteus).
- **Caddy snippet, pluteus settings and the step-by-step phone test:** [docs/https-and-phone-test.md](docs/https-and-phone-test.md).

| URL | What |
|---|---|
| `POST /upload/uploads[/{folder}]` | multipart upload into a folder of your upload area (`Accept: application/json` answers JSON) |
| `POST /files/{mkdir,rename,delete}/uploads/{path}` | new folder (in `path`), rename (`name` field), delete |
| `POST /share` | the Android share target |
| `GET /manifest.webmanifest` | web app manifest (adds `share_target` when uploads are on) |

## License

MIT, see [LICENSE](LICENSE). The bundled third-party scripts keep their own licences: epub.js (BSD-2-Clause), JSZip (MIT), pdf.js (Apache-2.0)
and its fonts and WebAssembly decoders; the licence texts sit next to the files in `pluteus/static/vendor/`.

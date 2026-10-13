# Changelog

Release notes are taken from the section of the tagged version (`## 0.5.0 ...`).

## 0.8.8 - 2026-10-13
- **`thumbnails = true`** (in `[server]`, off by default) switches cover thumbnails on: the cover links in the OPDS feeds, the `/thumb`
  route and the web cover view with its switches. Left off, none of it exists and no thumbnail is ever made.

## 0.8.7 - 2026-10-13
- **Favourites and Read next in the OPDS feeds** (Atom and JSON): reader apps list them at the top of the library, in the order chosen, with covers and page streaming like any folder.

## 0.8.6 - 2026-10-13
- **Favourites and Read next:** a star and a + on every file and folder (in the list and the cover view). The home page gets a
  "Read next" box (ordered, with up/down arrows) and a "Favourites" box. A finished book leaves "Read next" by itself. Per user, in the
  state database; entries that were moved, deleted or hidden are skipped.
- **Player:** the 30 s skips use < and > arrows; speed has - and + buttons (0.5x to 3x); the speed and sleep controls can be hidden in Settings.

## 0.8.5 - 2026-10-13
- Folder listings show plain file names again for music; the tags (title, artist, album ...) are read only when a file is opened in the player.

## 0.8.4 - 2026-10-13
- **Chapters for mp3 audiobooks** (ID3 CHAP frames), next to the m4b chapter list.

## 0.8.3 - 2026-10-13
- **App keys:** the per-user key now works as the password in reader apps (OPDS), downloads and WebDAV too, read only (no uploads, no
  changes, no admin page, no web session). Making or revoking a key signs the apps out at once.
- **Cover view** for folders (Settings, or a List / Covers switch on each folder).
- **Audiobooks:** chapter list (m4b) and bookmarks per user and file.
- Packaging: Pillow is bundled in the exe and the Linux binary, so thumbnails are real thumbnails there too.

## 0.8.2 - 2026-10-13
- **Cover thumbnails in the OPDS feeds** (Atom and JSON): the cover of an EPUB, the first page of a comic, the picture of a song, as small
  JPEGs cached on disk. New dependency: Pillow.
- **KOReader progress sync** (`kosync = true`), logging in with the same per-user key as the music apps.

## 0.8.1 - 2026-10-13
- **Read-only WebDAV** (`webdav = true`): mount the library in Explorer, Finder, Android file managers, Kodi.
- **OPDS 2.0 (JSON)** feeds next to the Atom ones, chosen by the app's Accept header.

## 0.8.0 - 2026-10-13
- **Subsonic / OpenSubsonic API** (`subsonic = true`): music apps such as Symfonium, Tempo and Substreamer can browse and stream the
  library, and Android Auto works through them. Login by a per-user **music app key** made on the admin page (token login, API key or
  legacy password); read-only; same folders and rights as the site.
- A folder's own picture (`folder.jpg`, `cover.jpg`, `front.jpg` ...) is shown at the top right of its listing.

## 0.7.1 - 2026-10-13
- Player: the top bar shows "Title – Artist – Album"; tighter spacing so the play button fits on a laptop screen.

## 0.7.0 - 2026-10-13
- **A modern music player:** cover art with a blurred backdrop, title, artist, album, year, genre, track and format read from the file's
  tags (`tinytag`, MIT), a real scrubber and a big play button. A `cover.jpg` / `folder.jpg` in the folder is used when the file has no picture.
- **Lyrics:** plain lyrics from the tag, or timed lyrics from a `.lrc` file next to the song, highlighted and scrolled as it plays.
- Folder listings of music show "Title - Artist" and the track number; the lock screen shows artist and cover.
- New dependency: `tinytag`.

## 0.6.0 - 2026-10-13
- **Libraries tab** on the admin page: add and remove libraries without editing `config.toml`, with a **folder picker** (for a browser on
  the same computer: the Windows and Linux programs; in Docker, type the path inside the container, or set `library_bases`). New libraries
  are in use at once, and a hand edit of the file is picked up without a restart.
- **Just run it:** `pluteus` with no arguments (a double-click on Windows) asks for an administrator name and password on the first run,
  saves its settings in your own settings folder, starts, and opens the admin page. No config file to write.
- `admin_url`: the Admin link in the top bar for administrators, also behind a proxy.

## 0.5.0 - 2026-10-13
- **Quick share:** `pluteus share FOLDER` serves folders at once, with no config file, and draws the address as a **QR code** in the
  terminal. Open and read-only by default; `--private` makes the QR code a one-time access link; `--upload` lets guests upload into a
  separate folder.
- **Download and run, no Python needed:** `pluteus.exe` (Windows) and `pluteus-linux-x86_64.tar.gz` (Linux) are built by GitHub Actions
  on every release. They are not code-signed, so Windows SmartScreen may warn on the first run.
- A "back" row (up one folder) at the top of every folder listing; comic reader zoom levels and mouse-wheel scrolling.

## 0.4.0 - 2026-10-12
- CBR, CB7 and CBT comics (RAR and 7z through bsdtar; ZIP-only in the upload folder).
- **Security default:** a user with no `roots` list may open no folders; `roots = ["*"]` is the explicit "every root".
- `.private` (allow-list) and `.hidden` folder markers next to `.adult`.
- Opt-in update and security check on the admin page (`update_check`), traffic-light indicators, user names in the status page.
- Admin page: version, link back to the site, sign-out, user icons and one column per right.

## 0.3.1 - 2026-10-12
- Admin page sign-out; the link to the admin page for administrators on the local network.

## 0.3.0 - 2026-10-12
- Web admin page on its own port (status, locks and known devices, users); sizes up to TB; lazy file stats (faster big folders).

## 0.2.0 - 2026-10-11
- Login protection (escalating locks, known devices), m4b audiobook player, Markdown, CSV and zip viewers, per-user upload folders,
  home page with "Pick up where you left off", gold theme.

## 0.1.0 - 2026-10-08
- First version: OPDS feeds, web UI over the real folder tree, EPUB, CBZ and PDF readers, per-user logins and progress.

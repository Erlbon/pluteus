# Changelog

Release notes are taken from the section of the tagged version (`## 0.5.0 ...`).

## Unreleased (for 0.9.5)
- **Subsonic internet radio:** `getInternetRadioStations` lists the station files (`.m3u`, `.pls`, `.url` with an http(s) stream) of a top-level `Radio` folder, in it and one folder down, so apps such as Tempo (and the car) show them as radio stations. The names come from the file (the `#EXTINF` name, else the file name); the list is kept for two minutes. Adding, renaming and deleting stations from an app is not supported: the files are the list.
- **Subsonic:** a folder named `Radio` at the top of a library (where you keep station `.m3u` files next to the music) is left out of the artist and album lists, so apps do not show it as an empty artist. A `Radio` folder deeper down is an ordinary folder.
- **Subsonic stars:** `star`, `unstar` and `getStarred2` use the user's Favourites shelf, so a heart tapped in an app is the same as the heart in the web pages, and starred songs and albums show the heart in the app. (Before, a star was ignored and the heart went away again.)
- **Subsonic lyrics:** OpenSubsonic `getLyricsBySongId` serves the timed lines of a `.lrc` file or tag, else the plain tag text; apps such as Tempo can show them.
- **Radio picker** (`tools/radio-picker.html`, not part of the server): a web page you open on your own computer. It searches the community directory radio-browser.info by country, genre and name (https only, working at the last check, no HLS), lets you listen, tick stations, and save one `.m3u` file per station into a library folder (Chrome or Edge), or download them as one list. Nothing is sent anywhere but the search.
- **Radio:** a `.m3u`, `.pls` or `.url` file that holds an `http(s)://` stream address opens in a radio player (play and stop, the station name, a volume control on wide screens, lock-screen controls); a file with several stations shows a list. The browser plays the stream, pluteus never fetches it, and only this page's content policy lets media come from other servers. HLS (`.m3u8`) streams are not supported, and a plain `http://` station is blocked by the browser on an `https://` page (the player says so). A file with no stream address stays a text file.

## 0.9.4 - 2026-10-14
- **Subtitles:** a movie shows the subtitle files that sit next to it (`Movie.srt`, `Movie.en.srt`, `Movie.no.forced.vtt` ...: the same name, then an optional language and flags). An `.srt` is converted to WebVTT as it is served (old Western European encodings are read too). The player gets a CC button that steps through the languages and off again and remembers the last choice; without the script the browser's own controls list them. Subtitles stored inside the video file, and the `.ass` and `.sub` formats, are not read.
- **Movie player on a phone:** the five buttons (-, rewind, play, fast-forward, +) are a little smaller on a narrow screen, so they fit and the page no longer scrolls sideways.
- **Movie player, full screen:** a wide picture turns the phone sideways while it is full screen (where the browser allows it), and the picture leaves room for the controls when the phone is held sideways.

## 0.9.3 - 2026-10-14
- **Update check:** with `update_check = true` the first check now starts when the admin server starts, not when the Status page is first opened, so the dot is ready.
- **EPUB reader, scrolling layout:** the mouse wheel over the book now scrolls it (the browser did not pass a wheel turn over the book's own page on to the box that scrolls it, so pluteus forwards it). The page-by-page layout is unchanged.

## 0.9.2 - 2026-10-13
- **Light or dark:** the choice is kept in a cookie as well as in the browser's storage, so the site and the admin page (two ports on one address) show the same one. A choice made earlier carries over the first time a page is loaded.
- **Movie player:** a movie now has the same gold controls as the music player under the picture (position bar, time, volume on wide screens, -, rewind, play with the speed on top, fast-forward, +), plus a full-screen button that takes the whole player full screen. The position bar, volume and full-screen button run the full width of the picture. A click on the picture plays or pauses, a double click goes full screen. Without the script the browser's own controls remain.
- **Music player:** the position bar and the volume slider are drawn by pluteus: a thin gold track that fills up to a small round gold handle, the same in Chrome and Firefox.
- **Music player:** a volume control (a speaker that mutes, and a short slider) at the end of the position bar, on wide screens only; the level is remembered. A phone keeps using its own volume buttons.
- **EPUB reader:** the mouse wheel (and a touchpad swipe) turns pages in the page-by-page view, one page per gesture. In the scrolling view the wheel scrolls as before, and Ctrl + wheel stays the browser's zoom.

## 0.9.1 - 2026-10-13
- **Cover view:** loose pictures (JPG, PNG, WebP, GIF) get a real thumbnail, not a type icon. A JPEG may have up to 120 million pixels (it is decoded at a reduced size); other formats keep the 20 million limit. A file that is not really a picture, and SVG, get none.
- A `folder.jpg` (or `cover.jpg` ...) added to or removed from a folder shows on the next refresh: it used to take up to two minutes, because the answer was remembered by time and not by whether the folder had changed.
- The admin Status page has a **Services** box: which optional servers are on or off (Subsonic, WebDAV, KOReader sync, thumbnails, uploads), the `config.toml` line that switches each one, the address apps use and how many users have a key.
- **Subsonic**, found by driving the API with a real, strictly typed client library (py-opensonic): `getArtists` and `getIndexes` always carry `ignoredArticles` (it was dropped when empty, and a strict app refuses the answer without it), and `getAlbum` carries `created`, like the other album items.

## 0.9.0 - 2026-10-13
The feature-complete pre-release. Since 0.6.0: a music player with tags, covers, lyrics, chapters and bookmarks; Subsonic, WebDAV, KOReader sync and OPDS 2.0
(all off until switched on); per-user app keys; favourites and read next; an optional cover view and cover thumbnails; and the fixes below
from a two-part code review. The player's controls were redone: old-school double-triangle skip arrows without text, round gold - and + for the speed on the outside, the speed written along the top of the play button when it is not normal (tap the area to switch between 1x, 1.25x, 1.5x and 2x), and the sleep timer and bookmarks only for audiobooks (m4b). It needs testing on real devices (Subsonic apps and Android Auto, WebDAV, PocketBook covers, KOReader sync) before 1.0:
only security and stability fixes are added until then.

### Review fixes
Security and stability fixes from a two-part code review of 0.8.8. No new features.
- **Secrets out of the log:** the access log no longer prints `p=`, `apiKey=`, `t=`, `s=` or `/join/TOKEN`; pluteus writes its own access line.
- **Logins by key:** a wrong key or token counts against the address only and never locks a user name; a correct key gets through while
  the name is locked for passwords; a stale key in a forgotten app can no longer lock its owner out of everything.
- **App keys are read only for real:** they can no longer write reading progress or bookmarks. Keys of users who were removed are deleted.
- **Request bodies** are read with a hard size limit (no buffering of whatever a client sends); odd characters in a token or key header
  give an error instead of a crash; the token login takes the same time for unknown names.
- **Thumbnails:** a picture is refused before it is decoded when it has more than 20 million pixels (a small file can hold a huge picture),
  only a few fixed widths are made, and three at a time.
- **Uploads:** a failed finish no longer leaves an empty file behind, and Windows retries a file that a virus scanner holds for a moment.
- **Player:** a reload or a restored tab of an auto-advanced file resumes where you stopped (the `?autoplay=1` flag is taken off the
  address); the arrow keys no longer fight the scrubber or the browser's Back.
- **Subsonic:** an empty `search3` lists the library (artists and albums, paged) as apps expect; albums carry `songCount`, `duration` and
  `created`, artists `albumCount`; music folder ids are whole numbers; lists that cannot be known (starred, by year ...) are empty; one album
  scan at a time and the cap holds for several libraries. Folder and file ids changed (they now start with `p`): apps re-read the library.
- **Favourites:** a long shelf costs no more than a short one (stops at 60 usable entries).
- README: where the app keys are stored.

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

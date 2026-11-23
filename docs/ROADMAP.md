# Roadmap after 1.0

pluteus 1.0 is the complete package: books, comics, music, radio, TV channels and video, one player that keeps going while you browse and read,
apps (OPDS, Subsonic, WebDAV, KOReader), per-user logins, uploads. This page is what comes next, most important first. Nothing here is
promised for a date; each item is decided by the owner when it starts.

## 1. The stream relay

**Version plan (owner, 2026-10-14):** 0.9.7 is the logos release; **0.9.8 is the relay**; **0.9.9 is reserved for the last check point before 1.0.0.**

**One sentence:** the server fetches a stream on the browser's behalf and passes it on, so streams that a web page cannot reach directly can be
played, and the server can read things a browser hides.

### Why we need it: everything it would solve

Each point below is something that exists today and cannot be fixed inside the browser.

| # | What goes wrong today | Evidence from this project | What the relay does |
|---|---|---|---|
| 1 | **Stream servers that do not allow a web page (CORS).** A player running in the browser cannot read the reply. | The TV picker's "Test ticked" marks channels that answer a page and channels that do not; in a first Norway sample 1 of 4 failed this way. Radio is less affected (an `<audio>` element does not need CORS), HLS through hls.js is hit hard. | The page asks pluteus (same origin); pluteus fetches. |
| 2 | **Plain `http://` streams on an `https://` site** are blocked by the browser (mixed content). | About 21% of the iptv-org catalogue (3,858 of 18,022 streams) is plain http; many older radio streams too. The pickers hide them today, so they are simply unavailable. Android Auto / Subsonic apps are not affected by this, a browser is. | pluteus serves them over its own https address. |
| 3 | **Streams that need a header** a web page cannot send (`Referer`, `User-Agent`). | 995 of 18,022 catalogue streams (about 5.5%) are listed with one of these and are hidden by the TV picker. | pluteus sends the header. |
| 4 | **Streams behind a login or a token** (a user name and password, or a signed address that must be refreshed). `https://user:pass@host/...` is refused by browsers for media. | Asked for by the owner as a later stage. A token inside the address already works when CORS allows it; a user name and password never does. | pluteus holds the credentials (per user, encrypted) and uses them. |
| 5 | **"Now playing" for radio** (what DAB shows: artist and title). Icecast and Shoutcast carry the current title inside the stream if the client asks for it with `Icy-MetaData: 1`; a browser hides it and a page cannot send that header. | Asked for by the owner. Without the relay the player bar can only show the station name. | pluteus reads the ICY title and tells the player bar (and Media Session / the lock screen). Some stations also publish titles on a web address of their own: the same fetch can read those. |
| 6 | **HLS details a browser handles badly:** expiring tokens in segment addresses, playlists with absolute addresses on other hosts, redirects. | Seen as "the stream did not start" with no further clue. | pluteus rewrites the playlist, follows redirects, and can say why a stream failed. |
| 7 | **No way to tell why a stream stopped.** | In one test environment NRK P3's mp3 stream ended after about 3 seconds even in a bare browser player, while it played fine for the owner: from the browser there is no reason to be seen, only "ended". | The relay sees the status code, the headers and the close, and can log them (without secrets). |
| 8 | **Stations hidden from apps that cannot play them:** Subsonic internet radio lists the stream address as it is; a client that wants https cannot play http. | Subsonic radio list is built from the station files. | The list can offer the relay address of a station where the direct one is http. |
| 9 | **Logos and other small pictures from other hosts** (already solved for logos by a guarded fetch at save time). | `pluteus/fetchimage.py`, see SECURITY.md. | Shares the same guard; the relay is not needed for logos. |

Not reasons (so the relay does not grow): recording streams, converting formats (transcoding), a programme guide (EPG), re-streaming to the
public. See "Out of scope".

### What it must be (requirements)

1. **A strict address guard**, the one already written for logos (`fetchimage.py`), extended: only addresses from the user's own station files or
   stored entries; http and https only; every address a name resolves to must be public; connect to the checked address (no second look-up
   that could answer differently); ports limited (80, 443, plus the ports a stored entry names on purpose, as a per-entry decision); redirects
   followed a few times and each one checked again; no `file:`, no credentials in the address itself.
2. **Credentials and headers stored per user,** never in the `.m3u` that sits in the library: a small secret store in the state database,
   encrypted with a key the server creates (the same "key in the data folder" idea as the other secrets, documented in SECURITY.md); a
   station file refers to a stored entry by name. Another user cannot use, list or see them; an administrator cannot read the clear text.
3. **No secrets in logs, pages or errors.** The access log already redacts known query parameters; relay addresses use an opaque id, never the
   original address or a password. Error messages say what failed (refused, timed out, 401 from the source), not what was sent.
4. **Limits:** a cap on simultaneous relayed streams per user and in total, a time limit to connect, an idle limit, a ceiling on bytes per
   stream and per day per user (the upload bandwidth of the server carries every listener), and clean stop when the browser goes away (no
   orphaned connections).
5. **Live-stream behaviour:** no buffering of the whole stream; pass-through with back-pressure; HLS playlists rewritten so segment and key
   addresses go through the relay (with the same checks), refreshed playlists passed on at the source's pace; `Range` is not used on live.
6. **ICY metadata:** ask for `Icy-MetaData: 1`, strip the metadata blocks from the audio it passes on (or pass on the clean stream and the
   title as a separate small event stream), parse `StreamTitle`, send it to the page (server-sent events or a short poll) and to Media Session.
   **How it shows (mockup first):** in the player bar the main line becomes `Artist – Title` with the station name and logo as the smaller line;
   with no title (a station that sends none, or between songs) the bar stays as today, station name and "Live radio". The same text goes to the
   radio page and the lock screen, and it changes live as the song changes.
7. **Fallback, not default:** the player tries the direct address first and uses the relay only when the stream cannot be played directly (CORS,
   mixed content) or the entry carries a login or header (decision 5). Radio that works directly does not pass through the server.
8. **Visible to the owner:** the admin page lists relayed streams now running, who, how long, how much; a switch turns the relay off
   completely (off by default, like Subsonic and WebDAV); the Services box shows its state.
9. **Honest failure:** every refusal has a one-sentence reason shown to the user.

### Decisions (made by the owner, 2026-10-14)

1. **Who may use the relay:** users with an upload folder (the same people who can use the pickers). Not the shared anonymous user. A per-user
   switch on the admin page is not needed for now.
2. **Bandwidth:** limit the number of streams at once, per user and in total, with the numbers set in the config (suggested defaults: 2 per
   user, 6 in total). No daily byte ceiling for now (step R5 can add one later).
3. **Stored logins and headers:** only the user who stored an entry can use it. No sharing with other users, no entries stored by the admin for
   everyone; the admin cannot read or use another user's entry.
4. **Encryption key:** either. If the config names a key (a `relay_key` setting) it is used; otherwise pluteus creates one in the data folder.
   Consequences to build in: the key's fingerprint is kept next to the stored entries so that a changed key is noticed and reported ("these
   entries were stored with another key and cannot be opened") and never silently breaks playback; the documentation says to back up the data
   folder (or keep the config key) because entries cannot be recovered without the key.
5. **When a stream goes through the relay:** automatically when the direct stream fails, for streams without a login, with a visible "via the
   server" label. Entries with a login or a header always use the relay. Nothing else passes through the server.

### How it would be built, in order

| Step | Contents | Size | Notes |
|---|---|---|---|
| R1 (done in 0.9.8) | The relay core for **radio**: guarded fetch, pass-through stream, limits, off by default, admin switch and Services line; plain http radio plays on the https site | large | Shares `fetchimage`'s guard; no credentials yet. |
| R2 (done in 0.9.8) | **Now playing** (ICY): title in the player bar, the lock screen and the radio page; a station's own "now playing" address where there is one | medium | The DAB-like feature. |
| R3 (done in 0.9.8) | **HLS relay** for TV channels: playlist rewriting, segments and keys through the relay, CORS and mixed content solved; the picker can offer the http and CORS-blocked channels again | large | Biggest part: token expiry, redirects, many small requests. |
| R4 (done in 0.9.8) | **Credentials and headers:** the encrypted per-user store, entries in a "my streams" page under Settings, station files that refer to an entry by name, `Referer` / `User-Agent` / basic auth / bearer token | large | SECURITY.md gets a full section. |
| R5 (done in 0.9.8) | **Admin view and quotas:** running relays, per-user and total caps, Stop (a daily byte ceiling was left out: totals are shown instead) | medium | Stop ends audio streams and HLS channels (a stopped channel is refused for two minutes). |
| R6 (done in 0.9.8) | Subsonic radio list with relay addresses for http stations | small | The app's key or token is echoed into the address; a plain `p` password is not. |

### How we will know it works (acceptance)

Checked with real signals, never state flags (see the playback-verification rule): the position advances between two reads, a
`securitypolicyviolation` listener sees nothing, the relay's own counters match, and a stream that is refused says why.

- A CORS-blocked HLS channel plays through the relay and fails without it.
- A plain `http://` radio station plays on the https page through the relay.
- A stream that needs a `Referer` plays only with the stored header.
- A stored password is never found in: the station file, the page, the access log, an error message, a backup of the state database in
  clear text, or another user's session.
- Address guard tests: private, loopback, link-local, a name that resolves to both a public and a private address, a redirect into the private
  network, a huge or endless reply, a reply that never ends, a client that disconnects (no connection left open).
- The ICY title appears in the player bar within a few seconds and changes with the song.

### Out of scope on purpose

Recording or saving streams; transcoding; a programme guide; public re-streaming; any feature aimed at pirated or paid-subscription catalogues
as such (the pickers only use catalogues of publicly available streams); a general-purpose open proxy (the relay only fetches addresses that
belong to the signed-in user's own entries).

## 2. Random mix for music and music videos

**One sentence:** a Shuffle button on folder pages and a Random mix button on the home page build a queue of mixed music files and music videos
that plays in the player bar while you browse and read; a video shows its picture in a panel docked under the bar.

**Decisions (owner, 2026-10-14):** the picture panel is **docked under the bar** for the first attempt (not a floating window); the work waits
for the new quota, after or next to the relay.

- **Server:** a random pick across the user's libraries, audio and video together, with a filter (both, only music, only videos) and a count
  (for example 50). There is no index, so it is approximate: random folders, then random files inside them (the way the Subsonic "random
  songs" call already works), a handful of directory listings, capped. The existing queue list for a folder (`/api/queue/...`) learns to include
  video files when asked.
- **Shuffle:** a button beside "Play all" on a folder page; the list is shuffled in the browser. **Done in 0.9.8** (with the subfolder switch in Settings, the `noshuffle` marker file in the three spellings, and `.m4b` left out); the minimum length and the audiences are still to do.
- **The shell's player already uses a video element** (since the TV sound), so it can show a picture: when the current item is a video, a panel
  opens under the bar with the picture and the site below shifts down; a "sound only" toggle hides the picture, a full-screen button takes the
  panel full screen; when the next item is a song the panel closes by itself. On a phone the panel is the same, full width.
- **Subfolders:** Shuffle and Play all include the subfolders of the folder by default (as Play all does now: up to 6 levels and 2000 files, the
  button says "2000+" when cut off). A Settings switch, "Play all and Shuffle include subfolders", turns that off for both buttons.
- **What a mix never includes (decided by the owner, 2026-10-14):** the **marker file** is the main way and the length and the type are the
  safety nets behind it.
  1. A folder with an empty **`.noshuffle`** file in it is left out of Shuffle and Random mix together with everything below it. It is still
     listed, still playable by hand and by Play all. Anyone can make one (it is just a file), and it needs no tags and no settings.
  2. **Shortest song to include:** files shorter than a minimum length are left out, default 45 seconds (sound effects are short); a Settings
     value, 0 turns it off.
  3. **Type:** `.m4b` audiobooks are never included.
  Not chosen for now (kept as later options, nothing built): a genre skip list (Audiobook, Podcast, Speech, Sound Effects ...) and a
  `shuffle = false` switch per library in the config.
- **Audiences (decided by the owner, 2026-10-14): keep adults' and children's content out of each other's mixes.** pluteus already has the
  adult half: a folder with the adult marker (`.adult`, name configurable) and everything below it is invisible to users without the "adult"
  right, so a mix built from the folders a user may see never contains it. New:
  1. **A kids marker** (`.kids`, name configurable like the other markers): the folder stays visible to everyone but is left out of Shuffle and
     Random mix (and the Subsonic random songs) for users who are not marked as children.
  2. **A "child" user flag** on the admin page (and in `config.toml`): for such a user the mix draws only from kids-marked folders and ordinary
     unmarked ones, never from adult ones (already enforced by the adult right). Browsing is unchanged.
  3. **Streams:** a station or channel carries its audience inside its own file, as an attribute on the `#EXTINF` line
     (`pluteus-audience="kids"`), or by the folder it sits in (a `.kids` marker in e.g. `Radio/Kids`). The pickers set it automatically: TV
     channels in the iptv-org category "kids" and radio stations with the radio-browser tags "kids" or "children" are saved as kids. Adult
     channels (`is_nsfw`) are never offered by the TV picker, so none end up in the files.
  4. The same rules apply to the Random mix, the Shuffle button and the Subsonic random calls, and are covered by tests (an adult folder never in
     a child's mix, a kids folder never in an adult's mix, a mixed queue, streams with and without the attribute).
- **Limits to state in the documentation:** subtitles and saved position belong to a video's own page and do not carry over into the panel; a
  video the browser cannot decode (some MKV) is skipped with a note.
- **Size:** about 20 to 25 minutes (server pick: medium, Shuffle button: small, the panel: medium, tests and real-signal checks: medium).
  The panel is a visual change: a mockup comes first.
- **Later:** a saved playlist from a mix once playlists exist (item 3); a random channel from the user's `IPTV` folder.

## 3. Playlists

A queue is already there (Play all, the player bar). Still to do: saved playlists per user in the state database; the option to mirror them
as `.m3u` files in the upload folder (`Playlists/`), one way; the Subsonic playlist calls (`getPlaylists`, `getPlaylist`, `createPlaylist`,
`updatePlaylist`, `deletePlaylist`) so Tempo and Android Auto get them; hand-dropped `.m3u` files with relative paths opening as playlists;
a visible queue list (see, reorder, remove).

## 4. Notes in folders (Markdown)

**One sentence:** short notes about a band or an author, and documentation, live as ordinary `.md` files next to the files they describe;
pluteus shows them on the folder page, in the music player and to Subsonic apps. Filesystem-first: no index, no database, no editor needed
(any text editor works). The owner's use: mainly notes and documentation, short notes on an author or a band.

pluteus already renders Markdown safely (raw HTML escaped, links and images only inside the library); this item adds the places where it is shown
and a few notebook conveniences.

**Status:** round 1 is done in 0.9.8: items 1 to 7 (folder notes, the player's About link, the Subsonic biography, anchors and contents, front matter, the editor with the personal overlay) and the library opt-in (step 2: `notes = "edit"` on a root, the `notes` user right). Also done from round 3: `artist.nfo`, `album.nfo` and `metadata.opf` are read as the fallback about text, `album.md` and `series.md` are recognised, and `docs/notes-format.md` and `docs/library-layout.md` exist. The readers' About link and the single-book description fallback (EPUB, ComicInfo) are done too. Still to do: the converters and the filler in the Redactor apps (separate repositories), and the extended structure checker.

**Round 1 (about 15 to 20 minutes, a mockup of the folder page first):**
1. **Folder notes:** a folder shows a note about itself. At the top, beside the cover: `artist.md` (also `artist.txt`, `artist.nfo`, the file
   names Kodi and Plex use), `author.md`, `about.md`. At the bottom, GitHub style: `README.md` (also `readme.md`, `index.md`). The first match wins
   in each place; nothing is shown when there is none.
2. **The music player and the book pages link to the note:** an "About the artist" link when the artist's folder has one.
3. **Subsonic:** `getArtistInfo2` and `getArtistInfo` answer with the biography from the artist note (plain text of the first paragraphs),
   so Tempo and other apps show it. No online look-up, on purpose.
4. **Heading anchors and a table of contents** in rendered Markdown (a contents list for long documents, copyable heading links).
5. **Wiki links: dropped (owner, 2026-10-15).** Notes hold text, not links to traverse; that goes against the filesystem-first idea. Releases in a discography are matched with the album folders by pluteus itself (see notes-format.md).
6. **Front matter** (`---` block with `title:` and `tags:`): read for the page title and hidden from the page.
7. **Edit in the browser** (owner: must be in round 1): an Edit button on a rendered `.md` (and `.txt`) page opens a text area with a Preview and
   Save. A new note can be created from a folder page ("Add a note": `artist.md`, `author.md`, `about.md`, `README.md`). Safe by construction:
   the same same-site checks as every other write; the file is written atomically; if the file changed on disk since it was opened the save
   is refused with a clear message (no silent overwrite); a size limit; only `.md`, `.markdown` and `.txt` files; never a file name outside
   the folder. **Where editing is allowed is an open decision (see below).**

**Decided by the owner, 2026-10-14: where the notes live and can be edited (option C, both).** Notes about a band belong next to its music, but
libraries are read-only by design (in Docker usually mounted `:ro`). So:
- **Step 1, the overlay (no mount change):** a note edited or added on a folder page is written to the user's own upload folder, at
  `uploads/<user>/Notes/<root>/<path of the folder>/artist.md` (or `author.md`, `about.md`, `README.md`), and shown on that folder for that
  user. Libraries stay read-only; these notes are personal. A note in the library itself is shown too; where both exist the user's own overlay
  note wins for that user.
- **Step 2, the library opt-in (shared notes beside the files):** a `notes = "edit"` line on a root in the config and a new per-user right
  "notes" (admin page); only the note file names above can be written there, nothing else; needs that library mounted read-write. Notes written
  there are seen by everyone who sees the folder.
- The overlay folder is an ordinary part of the user's upload folder (visible, downloadable, backed up with it) and follows the same space limit.

**Round 3: filling the notes in without typing them all (owner, 2026-10-14: "not necessarily within pluteus itself").** pluteus never looks
things up online, on purpose; the filling happens around it:
1. **Read the de-facto standards that other tools already fill in (inside pluteus, small):** `artist.nfo` and `album.nfo` (Kodi, Plex, Jellyfin,
   Lidarr and MusicBrainz Picard write them: biography, genres, years), Calibre's `metadata.opf` (book description), the description inside an EPUB,
   the `ComicInfo.xml` summary inside a CBZ (cbzredactor writes it). Shown as the folder's "about" text when there is no note of our own. Order on
   a folder page: the user's own `artist.md` first (overlay, then the library's), then an `.nfo` or `.opf` or ComicInfo summary, then nothing.
2. **The filler lives in the Redactor apps (owner, 2026-10-14: the redactors are the right tools), not in a new tool.** The note format is
   specified here (item 3); a shared writer in `redactor_common` (the package all four apps already depend on, pinned by tag) holds the front
   matter schema, the rules for which note name goes at which level of a library, a safe writer that never overwrites an existing note, and
   `status: draft` on everything it creates, plus the converters from OPF, NFO and ComicInfo to Markdown. Each app then creates the notes for its own
   kind of content, using the lookups it already has: **mp3redactor** writes `artist.md` and `album.md` (music), **epubredactor** writes `author.md`
   and per-book notes (Open Library and the local ISFDB database), **cbzredactor** writes `series.md` (the comic and manga sources it already
   uses), **videoredactor** writes notes for films and music videos. Same conventions as the rest of those apps: progress dialogs, a yes/no review
   page (never a spreadsheet), no Ctrl+Alt shortcuts, a version bump and changelog entry in the same commit, the `redactor_common` pin bumped in
   each app. Each app's CLI (inside its one exe) can do a whole library in a batch, the review comes afterwards. Drafts carry `status: draft`;
   nothing unreviewed looks final. It can also set the audience (kids) where the sources say so.
   **Build order:** the format description, the shared module, mp3redactor first (artist notes are the clearest case), then the others one at a
   time.
3. **A one-page description of the note format** (`docs/notes-format.md`): plain YAML front matter (title, years, genres, links, audience, status)
   at the top of a Markdown file, compatible with Obsidian and Jekyll, so other tools can write it without any pluteus code. A document, not a
   project.

**Not planned because it needs an index (decided against):** full-text search, backlinks, tag pages. A file-name search inside a folder is possible.

**Library layout guide (owner, 2026-10-14: with a few files per folder, the folder structure matters more):** `docs/library-layout.md` says
which note sits at which level: music `Artist/artist.md` and `Artist/Album/album.md`; books (Genre, Sub-genre, Author) `author.md` at the author
level and short `about.md` notes at genre level; comics `series.md` in each series folder and `about.md` for a publisher or lineage folder and for
the underscore grouping folders (`_Crossovers` ...), which also settles what `_` means. The note names `album.md` and `series.md` are added to the
recognised names. The owner's existing read-only structure checker (yes/no review page, renames only with a yes and with undo) is extended to
report where the layout deviates and where a folder at a note level still has no note. To be confirmed by the owner before it is written: the
music and books layouts (the comics layout is recorded; "780 - Music" suggests Dewey numbering).

**Audiences:** notes follow the same folder markers as everything else (a note inside an `.adult` folder is as hidden as the folder).

## 5. Casting to TVs and Chromecasts

**Owner, 2026-10-24:** wanted, **on the same network only** (remote access and a second site were tried as a design and dropped as a blind path).
Devices: Google Cast and DLNA/UPnP; not AirPlay, Roku or Miracast (nothing to test them on, and Miracast has never worked for the owner). The first
device is a TCL P745 (Google TV). Transcoding is allowed here as an optional ffmpeg feature (the one exception to "Out of scope on purpose" under
item 1: it applies to files a device cannot play, never to the relay's streams). Set-up and design are in `docs/cast.md`, the threat model in SECURITY.md.

| Step | Contents | Size |
|---|---|---|
| 1 (done, unreleased) | `cast` config and `[[cast_devices]]`, the Google Cast backend and the device manager, `/api/cast`, `/api/cast/play`, `/api/cast/control`, signed media addresses, Services line | large |
| 2 | "Play on…" in the player bar and folder pages, queue handoff from the resident player, Now Playing in the page | medium |
| 3 | DLNA/UPnP backend (SSDP and SOAP, standard library only); the calls are checked against the specification first | large |
| 4 | optional ffmpeg conversion for files a device cannot play; finding devices on the network instead of typing addresses | medium |

**Also wanted (owner, 2026-10-24), not started:** an Android / Google TV app for Pluteus. A Google TV could then run the player itself, so casting
matters mainly for devices that only receive.

## 6. Smaller items

Done in 0.9.8: Subsonic recently played (from the saved positions, nothing new is logged) and resume (`savePlayQueue`, `getPlayQueue`); the player bar merged into the pluteus bar on wide screens; a TV mode for the web pages (large buttons, remote arrow keys; Kodi over WebDAV still covers most of it); a way to forget the per-folder List / Covers choices; a manual Docker image workflow (`docker.yml`, publishes only when ticked). The release workflow already smoke tests the packaged exe, and the Linux build, on every tag.

Still open: a TV mode that has been tried on a real TV browser (the keys are checked in a desktop browser only).

## 7. Linked libraries (shared servers)

**Owner, 2026-11-23:** one pluteus installation can show another pluteus installation's library, so users can daisy-chain their libraries
to each other. It is meant for every platform (Windows, Linux, Docker, an Android phone). Not started. The design was decided on 2026-11-23
(critical review 85-96, card 97, decision page 98-107); two questions remain open at the end of the decisions.

**Where it came from:** an old Android phone as a portable pluteus server (Termux + Tailscale). It carries a small library of its own, and it passes the home server's full library on to devices that cannot run Tailscale (e-readers, TVs, a guest's
tablet). Without linked libraries that needs two sites on the phone (a local pluteus and a reverse proxy); with them it is one site.

**A more modular pluteus first? (owner, 2026-11-23: "might" want to consider it.)** Linked libraries mean a root that is not a
folder on disk, and every protocol (web, OPDS, WebDAV, Subsonic, the readers) would have to read through it. Today `app.py` holds most of
the routes in about 2,700 lines, and folders are read directly with `os.scandir` in several modules (`fs.py`, `uploads.py`,
`streams.py` and others). One storage layer that every protocol reads through, with the protocols as separate parts that can be switched
on, would let a remote root drop in without touching each protocol. **Ruled (review card 86):** no big restructure; with
delegation (card 85) only the folder, download and viewer routes move out of `app.py` into their own module, with a "local or linked" switch.

**Decided so far (owner, 2026-11-23):**

- **Shared servers are a separate folder option**, not mixed into the ordinary libraries: a linked server shows up as its own kind of folder,
  apart from the local roots.
- **Special remote-server users.** The server that shares creates a user that exists only for another server to connect with; it is not a person
  and has no web login. **Keys only** (no password): checked and decided as 98A below. The existing app keys (read only, revocable on the admin page, also
  usable in the address as `/k/<app key>/...`) are the starting point.
- **No sync between pluteus installations.** Linking shows another server's library live; nothing is copied or kept in step between servers.

**Ruled in the critical review (owner, 2026-11-23, cards 85-96 in `docs/linked-libraries-review-2026-11-23.md`):**

- **85 Delegation:** the other pluteus does the work. The linked folder's pages, feeds, downloads, CBZ pages and thumbnails come from the
  sharing server through a small pluteus API and are passed through; the receiving server does not read the other server's files.
  This also answers "how the servers talk": pluteus's own API, not WebDAV.
- **86 No big restructure** (see above).
- **87 Versioned API from day one:** `/link/v1/...`; each side states its version, and a mismatch is said in words.
- **88 Tests with two servers in one test process**, wired through the test client.
- **89 A linked server that is down says so** ("not reachable since ..."), never an empty folder; OPDS and WebDAV answer with an error.
- **92 One guest on the other side:** the admin page says that everyone who sees a linked folder reads as one server user there, and the
  receiving side chooses which local users see each linked folder.
- **93 Own servers first, friends' servers later.**
- **94 No re-sharing:** a server user never sees linked folders, only local ones. This also ends the loop question.
- **95 Keys:** one read-only key per linked server, stored hashed on the sharing side, each linked server a row on the admin page with
  "last seen" and Revoke. A lost device is handled in Tailscale (remove it from the network); pluteus needs nothing extra for that.
- **96 The phone stays the target device**, not a travel router: far more people have an old phone than a travel router.
- **97 Trusted link for your own servers (owner, 2026-11-23):** servers under one owner's control get a tighter link than friends'
  servers. Each server keeps its own users and nothing is kept in step between them; on a trusted link the receiving server says *who*
  is reading ("this is erlend"), the link key vouches for it, and the sharing server treats the reader as its own user of that name, with
  that user's rights there. Friends' servers keep the plain guest link (card 92); trust is switched on only for links marked as your own.
  **It includes importing users from the other machine:** on a trusted link, the admin page can list the other server's users and import
  chosen ones, so the same names exist on both sides. An import is a one-off copy started by the owner, not ongoing sync.
- **90, reading progress:** answered for your own servers by the trusted link (progress lands on the server that owns the book, under
  the reader's own name). For friends' servers it stays later.
- **Dropped:** 91, offline copies. Sync between installations was never part of the idea (owner: "I have never suggested sync between
  the pluteus"); it was added in this roadmap item by mistake.

**Decided on the decision page (owner, 2026-11-23, questions 98-107, all as recommended):**

- **98A Keys only:** a server user has no password, cannot open the web pages or the admin page, and exists only for its linked
  server's key. A new kind of user in the configuration.
- **99B 18+ on both sides:** the sharing side marks adult folders in what it sends; the receiving side shows them only to its own
  users with the adult right, whatever the server user may see.
- **100A Invite:** the sharing server makes a one-time invite for a short time; it is pasted on the receiving server, and the two
  servers swap the key themselves. Nobody sees or copies the key.
- **101A Import brings the password hash:** an imported user has the same password as on the other server from the start (both use
  scrypt). A password changed later on one server is not changed on the other (no sync).
- **102B Libraries matched by name:** a library with the same name carries the right across, "every library" stays every library, and a
  name with no match is dropped and shown before the import is saved.
- **103B Adult, upload and notes come along; admin never** (it is given on each server).
- **104A App keys never come along:** an imported user makes new ones, one per device.
- **105A A name that exists is skipped and reported:** the user here stays as it is, and the trusted link uses it for that name.
- **106A A removed user stops the link at once:** the name must exist on both servers at every request; nothing is removed on the other
  server.
- **107B Addresses:** private addresses (Tailscale, home network) as they are; a public address only over https with its certificate
  checked. Never plain http over the internet.

**Open questions:**

- **Reading progress on friends' servers (card 90, later):** kept on the server where you read, or sent back to the server that owns the book.
- **Speed:** a linked folder is slower than a local library, but should be close to opening the other server directly: with
  delegation the network trip is the same, plus one short hop and the receiving server passing the answer on. Two cases to measure: the
  phone's own processing (Python and Tailscale's encryption on a phone CPU), and a phone on the same Wi-Fi as the reader, where every
  byte crosses the air twice. Any caching must keep the "no index, live folders" design. Needs a measurement on two real installations first.

### Research (2026-11-23)

Facts gathered for the decision page. Nothing here is decided.

**What pluteus has today:**

- **Every user needs a password.** A `[[users]]` entry without a `password_hash` is refused (`config.py`). A keys-only server user is
  therefore a new kind of user, not a setting on an existing one.
- **App keys are close to what a server link needs:** read only, no uploads, no admin page, cannot start a web session, revoked at
  once from the admin page. But there is **one key per user**, and it is kept **in clear text** because Subsonic's token login and KOReader
  sync must recompute it. A server link uses neither, so **its key can be stored as a hash**: SHA-256 (or HMAC-SHA256 with a server
  secret) is enough for a long random key, since the protection comes from the key's randomness; bcrypt or scrypt would add
  delay to every request for nothing. A short visible prefix lets the admin page tell keys apart without showing them.
- **The receiving server must keep the key it was given in a form it can use**, so it cannot be hashed there. Pluteus already seals
  stored secrets for the stream relay (`vault.py`, master key from `relay_key` or `relay.key` in the data folder); the same vault fits.
- **An invite link already exists:** `pluteus share --private` signs a visitor in through `/join/<token>`. The same pattern could hand a
  key from one server to the other, so nobody copies a key by hand.
- **WebDAV** (`/dav`, read only) lists folders through the same per-user library calls as the web site, so the user's roots and the
  `.adult` rules apply, and it takes the app key as its password. A server user without the adult right simply never sees the adult
  folders. It lists at most 5000 entries per folder (`MAX_ENTRIES` in `webdav.py`), so a linked library would need paging or a higher
  cap for bigger folders. It carries no pluteus extras: EPUB titles and authors, covers, notes, progress.

**How others do it:**

- **Nextcloud (Open Cloud Mesh, an IETF draft):** server A sends an invite token; B sends it back to accept, which puts B on A's allow list.
  Each share then has its own shared secret, which the newer flow exchanges for a short-lived access token. The useful parts for pluteus are
  an invite-and-accept handshake, one secret per link, and revoking one link without touching the others.
- **Plex:** friends share libraries through one central plex.tv account. Pluteus has no central service and should not get one.
- **Jellyfin:** no federation; it has been asked for, but recent forum posts say it is not planned. People link servers by mounting each
  other's SMB shares over a VPN, which gets cumbersome as more people join.
- **Komga, Audiobookshelf, Navidrome:** nothing found.
- **Tailscale identity:** on a computer, a server can ask the local Tailscale who is connecting (`whois`: node, owner, tags) and could
  trust that instead of a key. The Android app gives Termux no such access, so the phone needs keys anyway. Tailscale identity could
  be an extra check, not the only one.

**Windows Explorer and WebDAV over plain http** (for people who mount a pluteus as a drive, not for server links): Windows sends a
password over WebDAV only over https unless the registry value `BasicAuthLevel` = 2 is set under the WebClient service's Parameters key
and the WebClient service is restarted. Even then Windows does not remember the password, so the drive must be mapped again after a
restart. Over Tailscale the traffic is encrypted anyway, so this is acceptable there.

**Sources:**
[OCM draft (IETF)](https://datatracker.ietf.org/doc/html/draft-ietf-ocm-open-cloud-mesh-05),
[OCM integration draft](https://www.ietf.org/archive/id/draft-nordin-ocm-integration-protocol-00.html),
[Nextcloud OCM commands](https://docs.nextcloud.com/server/stable/admin_manual/occ_ocm.html),
[Jellyfin forum: federation](https://forum.jellyfin.org/t-federation-system),
[API key hashing (ADR)](https://docs.sso.earthobservation.vam.wfp.org/architecture/decisions/adrs/0008-api-key-hashing),
[API key authentication (Zuplo)](https://zuplo.com/learning-center/how-to-implement-api-key-authentication),
[Tailscale LocalAPI client (Go)](https://pkg.go.dev/tailscale.com@v1.90.6/client/local),
[Microsoft: WebDAV drive not authenticated](https://learn.microsoft.com/en-us/troubleshoot/windows-client/networking/error-access-network-drive-mapped-web-share),
[Microsoft: Basic auth DAV shares do not reconnect](https://learn.microsoft.com/en-US/troubleshoot/windows-client/networking/cannot-automatically-reconnect-dav-share).

**Still to find out (needs a second installation or the phone):** how long a big folder takes to list through a linked server over Tailscale (now that pluteus's own API is the link, card 85), which
decides whether a short-lived cache is needed.

| Step | Contents | Size |
|---|---|---|
| 0 | Decision page (done 2026-11-23, questions 98-107) | small |
| 1 | Move the folder, download and viewer routes out of `app.py`, with a "local or linked" switch (card 86) | large |
| 2 | Server users with hashed, read-only keys and a Revoke row on the admin page (cards 94, 95) | medium |
| 3 | The versioned `/link/v1` API on the sharing side (cards 85, 87) | large |
| 4 | A linked server as a separate folder on the receiving side: browse, open, download, the readers, "not reachable" (cards 85, 89, 92) | large |
| 5 | Trusted link for your own servers: the reader's name passed through, progress on the owning server (cards 90, 97) | large |
| 6 | Import users from the other server over a trusted link (card 97) | medium |
| 7 | OPDS, Subsonic and WebDAV through a linked folder | large |
| 8 | Friends' servers (card 93) | to be sized |

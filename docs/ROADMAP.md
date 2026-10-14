# Roadmap after 1.0

pluteus 1.0 is the complete package: books, comics, music, radio, TV channels and video, one player that keeps going while you browse and read,
apps (OPDS, Subsonic, WebDAV, KOReader), per-user logins, uploads. This page is what comes next, most important first. Nothing here is
promised for a date; each item is decided by the owner when it starts.

## 1. The stream relay

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
| R1 | The relay core for **radio**: guarded fetch, pass-through stream, limits, off by default, admin switch and Services line; plain http radio plays on the https site | large | Shares `fetchimage`'s guard; no credentials yet. |
| R2 | **Now playing** (ICY): title in the player bar, the lock screen and the radio page; a station's own "now playing" address where there is one | medium | The DAB-like feature. |
| R3 | **HLS relay** for TV channels: playlist rewriting, segments and keys through the relay, CORS and mixed content solved; the picker can offer the http and CORS-blocked channels again | large | Biggest part: token expiry, redirects, many small requests. |
| R4 | **Credentials and headers:** the encrypted per-user store, entries in a "my streams" page under Settings, station files that refer to an entry by name, `Referer` / `User-Agent` / basic auth / bearer token | large | SECURITY.md gets a full section. |
| R5 | **Admin view and quotas:** running relays, per-user and total caps, daily ceiling | medium | |
| R6 | Subsonic radio list with relay addresses for http stations | small | |

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

## 2. Playlists

A queue is already there (Play all, the player bar). Still to do: saved playlists per user in the state database; the option to mirror them
as `.m3u` files in the upload folder (`Playlists/`), one way; the Subsonic playlist calls (`getPlaylists`, `getPlaylist`, `createPlaylist`,
`updatePlaylist`, `deletePlaylist`) so Tempo and Android Auto get them; hand-dropped `.m3u` files with relative paths opening as playlists;
a visible queue list (see, reorder, remove).

## 3. Smaller items

- Subsonic: recently played (`getAlbumList2` type recent) and resume across devices (`getPlayQueue`, `savePlayQueue`) from the saved positions.
- Artist information: `artist.md` / `artist.txt` / `artist.nfo` text on the folder page and in `getArtistInfo2`; artist links in the player.
- The player bar merged into the pluteus bar on wide screens (the mockup: option B wide, option A on phones).
- A TV mode for the web pages (large buttons, arrow-key navigation) for a browser on a TV; Kodi over WebDAV covers most of it meanwhile.
- A smoke test of the packaged exe in the release workflow; a Docker image on a registry.
- A way to forget the per-folder List / Covers choices.

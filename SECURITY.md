# Security

Pluteus is meant to run on a home server and, behind HTTPS, to be reachable from the internet, so security reports are welcome.

**Report a problem privately** with GitHub's *Report a vulnerability* button on the repository's Security tab, not as a public issue.
Please include the version (shown in the footer and on the admin Status page), how you run it (Docker or native, behind which proxy)
and the steps to reproduce.

Only the **latest release** gets fixes. When a fix is released, a published GitHub security advisory names the affected versions; an
installation with `update_check = true` then shows a red notice on the admin Status page.

**Known trade-off:** an app key (admin page, Users, Make a key) is stored in clear text in `state.sqlite3` and in its daily copies in
`backups/`, because the salted-token logins of Subsonic apps and KOReader cannot be checked against a hash. The key can only read, is never shown
again after it is made and never written to the log, and can be revoked at once. Keep `data_dir` protected like a password file.

What pluteus is built to hold up against, and what it is not, is described in the README (sections on running it on the internet and on
the admin page). The admin page is for the local network only: never route or forward its port to the internet.

## Station logos (server-side fetch)

When the radio or TV picker saves a station, the server downloads its logo once. The address comes from a request, so it is treated as hostile
(`pluteus/fetchimage.py`): only plain `http` and `https` on ports 80 and 443, no user name or password in the address, every address the name
resolves to must be public (private, loopback, link-local, multicast and reserved addresses are refused) and the connection goes to the checked
address itself (a name cannot answer differently the second time), at most three redirects with each one checked again, at most 1.5 MB read in at
most 10 seconds, and the downloaded bytes are never kept: they must open as a PNG, JPEG, GIF or WebP picture and a fresh PNG or JPEG of at most
256 pixels is written beside the station file (SVG is not taken). A logo that fails costs only that logo. The same guard is meant for any later
feature that fetches an address on the server's behalf.

## `relay_allow`: trusted hosts on a private address

The relay, the logo fetch and the playlist fetch only connect to public addresses (an address from a request must never reach your own network). `relay_allow` is the
owner's exception: each entry is an exact `"host:port"` string in the config (`192.168.1.99:9191`), no wildcards, no paths, no names that merely resemble it. A listed
host may be on a private address and on that port only (another port on the same host, or another host, keeps every rule). It applies to everything the server fetches
for a user, so list only machines whose streams and playlists you are happy for your users to reach through pluteus.

## An app key in the OPDS address (`/k/<key>/opds`)

For reader apps that never send the login they are given. The key is the music-app key of a user (admin page, Users), 144 random bits, so it cannot be guessed;
a wrong one is sent on as a wrong password (counted by the same limits, locked for strangers like any other). `pluteus/keypath.py` takes `/k/<key>` off the
path and gives the request the Basic login the key stands for, so the rest of the server is unchanged: the user stays read-only (no uploads, no changes, no admin),
and only the paths a feed links to answer (`/opds`, downloads, covers, thumbnails, comic pages): the web pages, uploads, the admin page, Subsonic, WebDAV and every
write are refused through a key address. Links in feeds answered this way carry the key so the app can follow them. **The trade-off:** the key is a secret in an
address: it ends up in the app's catalogue list and in the logs of anything in front of pluteus (a proxy such as Caddy logs the whole address). pluteus masks it in
its own access and debug logs. Treat the address like a password; revoking the app key ends every address that holds it.

## Debug logging (`debug = true`, or the admin page for 15 minutes)

Off by default. While on, each request to the site leaves one line in the log and in a short list in memory (`pluteus/debuglog.py`): method, path,
the *names* of query parameters, status, client address, what the proxy said (`X-Forwarded-*`), User-Agent and Accept, the names of the cookies, and
for a login its kind and what can be said without the secret (how long the name and password are, whether the name matches a user, why it was
refused). **Never recorded:** passwords, keys and tokens, cookie values, query values, request bodies, file contents. Nothing is written to disk by
it, and a restart forgets the lines. `debug = true` keeps it on for as long as the server runs (the owner asked for that: a config line is a
deliberate choice); the admin switch ends by itself after 15 minutes. The admin page can download a diagnostics text with the version, a summary of
the settings (names and switches, no paths, keys or addresses) and the lines. It is on the admin page only (local network).

## Notes written from the browser

Libraries are read-only by design, so a note edited or added on a folder page is a *personal copy* in the user's own upload folder
(`Notes/<library>/<folder>/artist.md`), written by `pluteus/notes.py`: only the recognised note names (`artist.md`, `author.md`, `series.md`,
`album.md`, `about.md`, `readme.md`) are accepted for a library folder, the folder must exist and be one the user may see, nothing outside the
upload folder is touched, the text is at most 64 KB and has no NUL bytes, it counts against the space limit, the write is atomic (temporary hidden
file, then rename), and a save that finds the file changed since the page was opened is refused instead of overwriting. The editor is a normal
same-site POST (the usual cross-site checks), and never available to the shared anonymous login or to an app key. A note in the user's own
upload folder is edited in place under the same limits (`.md`, `.markdown`, `.txt`).

The only way pluteus writes into a library is the opt-in **shared note**: a root with `notes = "edit"` in the config *and* a user with `notes = true`
(a column on the admin Users page). It writes the same recognised note names, into a folder inside that root which the user may see, atomically, with the
same changed-file check; it refuses a symbolic link or a folder where a note file should be, and an existing note in another letter case is the file
that is written (never two). A library mounted read-only (the Docker default) simply cannot take it. Notes are Markdown rendered by the same safe
renderer as any `.md` page; `artist.nfo`, `album.nfo` and `metadata.opf` are read only for their text, with the standard XML parser after a check that
refuses any document declaring entities, and at most 256 KB.

## The stream relay (`relay = true`, off by default)

With the relay on, the server fetches a radio station or an HLS TV channel for a browser that cannot play it directly (plain http on an https
site, a server that refuses web pages). It is the one place where the server connects to an address on a request's behalf, so it is built
around that: the code is in `pluteus/relay.py` and shares its address guard with the logo fetch (`pluteus/fetchimage.py`).

- **Who:** only signed-in users with an upload folder; never the shared anonymous user.
- **Which addresses:** only an address a station file the user can open holds. The page names the file and the number of the station
  (`/relay/<library>/<file>?i=N`); a request cannot carry an address, and an address in the query string is ignored. For HLS the server
  rewrites the playlist itself and replaces every address in it (variant playlists, segments, keys, maps) with a **signed token** (HMAC with a
  key made at start-up) that names that address for that user only and lapses after an hour. A client can fetch only what the server offered it.
- **How an address is checked:** plain http or https only, no user name or password in it, every address the name resolves to must be public
  (loopback, private, link-local, multicast and reserved addresses are refused), the connection goes to the checked address itself (a name
  cannot answer differently a second time), at most three redirects with each one checked again.
- **What comes back:** the audio type or the type of a piece is decided by what it contains, never taken from the source, and nothing of the
  source's headers is passed on. A reply that is not audio (for radio) or not a playlist where one is expected is refused with a reason.
- **Limits:** a stream that stalls for 15 seconds is ended; at most `relay_streams_user` (default 2) and `relay_streams_total` (default 6)
  streams are open at once (an HLS channel holds a lease that every playlist refresh renews and that lapses when the player leaves; a failed
  playlist does not keep its place); pieces are capped at 30 MB, playlists at 1 MB, and at most eight pieces per user at once.
- **Now playing:** a short side connection reads one ICY metadata block (about 16 KB); answers are kept for ten seconds and at most four
  look-ups run at once.
- **Stored logins and headers for protected streams (`pluteus/vault.py`):** a user stores an entry under Settings, My streams: a name, a login
  or token, headers (`Referer`, `User-Agent` ...) and the host names they may be sent to. A station file refers to it with
  `pluteus-stream="Name"` on its `#EXTINF` line, and that station then always plays through the server.
  - **Encrypted at rest:** every entry is a blob in the state database, sealed with keys derived (HMAC-SHA256) from one master key: the data is
    XOR-ed with an HMAC-SHA256 keystream in counter mode (a fresh random nonce each time) and an HMAC-SHA256 tag over nonce and ciphertext is
    verified in constant time before anything is decrypted. Built only from the Python standard library, no extra dependency. A wrong key or a
    changed blob is refused, never decrypted to garbage. The database holds no login, header or host in clear.
  - **The key:** `relay_key` in the config wins (stretched with PBKDF2); otherwise a random key in `relay.key` in the data folder (created with
    owner-only permissions where the system supports it, never replaced if it exists but is unusable); with no data folder, a key for this run
    only. Each entry carries the fingerprint of the key it was sealed with, so a changed key is reported ("stored with another key") and never
    breaks playback silently. **Back up the data folder or keep the config key:** entries cannot be recovered without the key.
  - **Only the owner:** an entry is looked up by the signed-in user's name and its name; another user, and the administrator, cannot use or read
    it, and the entries of a removed user are deleted.
    The admin page's relay box shows only counts of stored streams per user and the key's fingerprint, never a name or a value.
  - **Sent only where it belongs:** the login and headers go only to the host names the entry lists, decided again at every hop, so a redirect or
    a playlist piece on another host never carries them (like `curl`). Playlist tokens carry the entry's name, never a secret; nothing of an entry
    appears in a page, a log or an error message; headers that could break the request (`Host`, `Content-Length`, `Authorization` ...) cannot be set.
  - **Honest limit:** the key has to be readable by the server, so someone who can read both the data folder and the config (or the running process)
    can open the entries. The encryption protects a copy of the database, a backup or a leaked state file, not a compromised server.
- Traffic through the relay uses the server's upload bandwidth, which is why it is off by default and limited.

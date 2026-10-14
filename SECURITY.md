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
  - **Sent only where it belongs:** the login and headers go only to the host names the entry lists, decided again at every hop, so a redirect or
    a playlist piece on another host never carries them (like `curl`). Playlist tokens carry the entry's name, never a secret; nothing of an entry
    appears in a page, a log or an error message; headers that could break the request (`Host`, `Content-Length`, `Authorization` ...) cannot be set.
  - **Honest limit:** the key has to be readable by the server, so someone who can read both the data folder and the config (or the running process)
    can open the entries. The encryption protects a copy of the database, a backup or a leaked state file, not a compromised server.
- Traffic through the relay uses the server's upload bandwidth, which is why it is off by default and limited.

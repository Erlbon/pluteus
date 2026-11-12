# Critical code review, 2026-11-12 (pluteus 0.9.27, commit 7ca8e9b)

A read-only review of the whole code base: 14,300 lines of Python in `pluteus/`, 3,100 lines of first-party JavaScript, the packaging, the CI and the tests. Nothing in the repo was changed apart from this file. The answers go on the decision page (one card per finding, Yes / No / Later); this file holds the same findings and reasons.

**How it was checked.** The repo was fetched and pulled first (already up to date, working tree clean). Six Fable reviewers each read one slice in full: (1) `app.py`, `urls.py`, `bodies.py`; (2) login, sessions, throttle, state database, config and the editors; (3) the file layer, uploads, WebDAV, zip, comics, thumbnails and the tag readers; (4) the page builders, OPDS, notes, Markdown and all the JavaScript; (5) Subsonic, the relay, radio, cast and the update check; (6) the admin app, packaging, CI and the test suite. Each was told to prove a finding or mark it PLAUSIBLE. Most CONFIRMED findings were run in-process against the real code with temp trees (no server, no network, the libraries were not touched). I then read the code behind the headline findings myself (cards 1 to 5, 9, 12, 13, 17, 20, 24, 37); the rest stand on the reviewer's proof as stated on each card.

**The test suite** ran once in full: 1052 passed, 2 skipped, 0 failed, 200 seconds.

**Left out on purpose** (decided or accepted in writing): app keys in clear text in the state database; no library index and no background walks; no CBR; the admin page being LAN-only over Basic; everything already listed in `docs/ux-review-2026-11.md` and `docs/subsonic-gap-review-2026-11-08.md`.

**Effort:** small = under 2 minutes of work, medium = up to 10, large = more than 10.

**Overall.** The security core is good, and better than most projects of this size: the path containment survived direct attack on Windows (dot-dot, alternate data streams, trailing dots, 8.3 names, junctions out of the root), every route is guarded, HTML is escaped everywhere it was traced, the outbound-fetch guard is sound, and the tests are a real net. No way was found past per-user folders, the adult marker or the read-only key through the normal paths. The weaknesses are in three places. First, the login throttle does not hold: a parallel burst gets past it, and a playing music app resets it (cards 1 and 2). Second, hostile or merely damaged files: narrow `except` lists and missing caps mean one bad EPUB breaks a folder page and one Markdown file can pin the CPU (cards 12, 13, 14, 20, 21). Third, concurrency: worker threads held by the thumbnail gate and SQLite calls on the event loop (cards 24, 25). The open last-page bug in the ebook reader has a concrete, reproduced mechanism and a one-expression fix (card 37). Packaging is the least careful part: nothing is pinned, and the Windows task installer runs the server as SYSTEM (cards 52, 53).

---

## 1. Security

### 1. A parallel burst of password guesses gets past the login throttle
- **What:** the throttle is checked, then the password is hashed on a worker thread, then the failure is counted (`pluteus/app.py:391-403`; same shape in `login_submit` `app.py:580-587`, `adminapp.py:83-91` and the Subsonic password path). CONFIRMED by two reviewers independently: 60 parallel wrong passwords for one name gave 59 checked and one refused, against a limit of 5. Read by me.
- **Why:** the "5 guesses, then a growing lock" promise does not hold against anyone who sends guesses in parallel. Each guess is also a 16 MB scrypt on a worker thread, so a burst fills the pool. A reader app with a stale password and 8 parallel connections locks its own account.
- **Fix:** count the attempt before hashing and refund it on success (one atomic "reserve" call in `FailureLimiter`), plus a small semaphore around password hashing with a re-check after it is taken.
- **Effort:** medium. **Recommendation:** Yes (first).

### 2. A music app's key login clears the password lock for its user
- **What:** `throttle.ok` runs after a Subsonic key or token login (`pluteus/subsonic.py:265`, `:269`; `app.py:2214-2215`), and `limiter.success` drops the failures, the lock and the lock count for that name (`ratelimit.py:137-144`). CONFIRMED by two reviewers: five wrong passwords locked alice; one `ping` with her key unlocked her. Read by me. `app.py:400` does the same when a reader app re-verifies with its app key.
- **Why:** Subsonic logs in on every request, so while a user's music app is playing the lock on that name never holds, and the escalation to 30 minutes and 6 hours never happens.
- **Fix:** only a verified real password clears the name's state; key and token logins do not call `ok`.
- **Effort:** small. **Recommendation:** Yes.

### 3. Subsonic `getAlbum` opens `about.md` at a path taken from the id before any path check
- **What:** `album()` (`pluteus/subsonic.py:912-914`) calls `_album_songs`, which reaches `_layout` (`:458-462`): `os.path.join(longpath(r.path), *folder)` and an `open`. No `folder_path` call comes first (`artist()` at `:906` has one). CONFIRMED in-process: an id whose first part was an absolute path made the server open that folder's `about.md`. Read by me.
- **Why:** any logged-in Subsonic user can do it. On a native Windows install a first part like `\\host\share` makes the server connect to a host the caller chose, which hands over the service account's NTLM hash. On Linux it is an existence probe, and it reads layout notes inside adult or private folders.
- **Fix:** call `self.lib.folder_path(user, root, parts)` first in `album()`, and run `check_segment` on every part inside `_layout`.
- **Effort:** small. **Recommendation:** Yes.

### 4. The admin page accepts the config folder (or its parent) as a library
- **What:** the editors validate with `parse_config(data, check_roots=False)` (`pluteus/useredit.py:174-182`); the "config file is inside a root" check lives only in `load_config` (`config.py:406-412`), and there only when `show_all_files` is on. CONFIRMED with a temp tree: `libedit.add_root` accepted the folder, then `load_config` refused the file. Read by me.
- **Why:** with `show_all_files` on, the new library is live at once and `config.toml` plus `config.toml.bak` (password hashes, `relay_key`, `join_token`) are downloadable by every user who sees all libraries. The reloader then rejects the file on every poll, so later password changes silently do not apply, and the next restart fails (a crash loop under Docker's restart policy).
- **Fix:** move the config-path and uploads-path overlap checks into one function that takes the path; call it from `apply_edit` and the admin text save. Refuse the overlap whether or not `show_all_files` is on. Add a test.
- **Effort:** medium. **Recommendation:** Yes.

### 5. A marker probe that fails counts as "no marker", cached for a minute
- **What:** `os.path.exists` on the `.adult` / `.private` marker returns False on any error (`pluteus/fs.py:177-184`), and the answer is cached for 60 seconds. PLAUSIBLE: the logic was confirmed by simulating the failed probe; a real SMB fault was not reproduced. Read by me.
- **Why:** an SMB timeout or a share dropping for a moment opens an adult or private folder to users who must not see it. `_names_in` is deliberately closed on error; the probe in front of it is not.
- **Fix:** probe with `os.stat`; only "not found" means absent, any other error means closed and is not cached.
- **Effort:** small. **Recommendation:** Yes.

### 6. A link inside a library that points below an `.adult` folder opens it
- **What:** `_inside` re-checks excluded names on the real path but not the markers (`pluteus/fs.py:249-268`). CONFIRMED: a junction `Plain/New arrivals` to `Adult Stuff/Inner Folder` let a non-adult user list and download. A link that leaves the root is still listed and only fails when opened; `meta.lookup_many` reads a linked file without the `_inside` check.
- **Why:** only the owner can make links, so the risk is an accident, not an attack.
- **Fix:** when the real path differs from the requested one, check the markers on each real folder from the root down; drop listing entries whose link resolves outside the root.
- **Effort:** medium. **Recommendation:** Yes.

### 7. User changes do not reach the login form, Subsonic, KOReader sync or the admin page
- **What:** `reloader.maybe_reload()` is called only from `resolve` (`pluteus/app.py:367-368`). CONFIRMED: after a password change and a user removal, with no main-site request in between, the login form took the old password, refused the new one, and Subsonic accepted the removed user. `adminapp.py:375` does not call `auth.forget_user` either.
- **Why:** a removed user or a changed password should take effect everywhere at once.
- **Fix:** call `maybe_reload()` from one shared entry point, and have the admin app call `auth.set_users` after each edit.
- **Effort:** small. **Recommendation:** Yes.

### 8. A changed password keeps working in Subsonic for up to five minutes
- **What:** `_pw_ok` (`pluteus/subsonic.py:323-331`) is not cleared by `set_users` or `forget_user`. CONFIRMED by reading.
- **Why:** "sign out everywhere" should mean everywhere.
- **Fix:** clear `_pw_ok` from both, or keep the password hash in the cached value and compare it.
- **Effort:** small. **Recommendation:** Yes.

### 9. Server fetches with no total time limit can use up every worker thread
- **What:** `relay.fetch_body` (`pluteus/relay.py:386-401`) has a 15-second limit per read, none in total; `stations_parse` (`app.py:1660`) and `hls_playlist` (`app.py:1400-1404`) have no cap on how many run at once. The logo fetch has the same hole: its 10-second deadline covers only the body, not DNS, connect, TLS or headers (`fetchimage.py:96-116`), and stuck threads are left behind (`stationsave.py:55-67`). CONFIRMED by reading (not run: no network allowed).
- **Why:** a source that sends one byte every 10 seconds holds a thread for days. About 40 such requests from one user with an upload folder stop the site for everyone. SECURITY.md's "at most 10 seconds" is not true.
- **Fix:** an overall deadline in `fetch_body` and before every blocking step in `fetchimage`; a small per-user semaphore on the station parse and the playlist fetch.
- **Effort:** medium. **Recommendation:** Yes.

### 10. The Subsonic token login tells which users have an app key
- **What:** the dummy scrypt runs only when the user or key is missing (`pluteus/subsonic.py:296-300`). CONFIRMED, measured: 4.6 ms with a key, 58 ms without, 59 ms for an unknown name. It also lets a stranger burn one scrypt per request.
- **Fix:** drop the dummy scrypt here; compare against the md5 of a fixed dummy secret.
- **Effort:** small. **Recommendation:** Yes.

### 11. A name lock also refuses correct app keys, and after a restart every reader app
- **What:** the lock is checked before the app-key compare, and the "trusted earlier" list lives in memory only (`pluteus/app.py:391-397`, `auth.py:92-102`). CONFIRMED by reading.
- **Why:** five wrong guesses per lock period keep a known name locked. After a restart, reader apps with the right password or key get 429 until the lock ends.
- **Fix:** accept a password equal to the user's app key before the lock check (a cheap constant-time compare). Persisting the trusted list is optional.
- **Effort:** small. **Recommendation:** Yes.

## 2. Hostile or damaged files

### 12. One damaged EPUB turns its whole folder page and OPDS feed into an error
- **What:** `_READ_ERRORS` (`pluteus/meta.py:31-32`) misses `zlib.error`, `TypeError`, `EOFError` and `lzma.LZMAError`. CONFIRMED end to end: `/browse/books/Damaged` and `/opds/books/Damaged` answered 500, and 200 without the bad file. Read by me. The same hole is in `thumbs.py:47`, `:52`, and in the zip and comic handlers (`ziputil.py:18`, `cbz.py:32-33`), where a damaged page gives 500 where 422 was meant.
- **Why:** a single bad download hides a whole folder from the web and from reader apps, on every visit (failures are not cached). The module's own docstring promises the opposite.
- **Fix:** catch `Exception` in `read_epub_metadata`, `epub_cover` and `MetaService._one`, cache the failure as empty; add `zlib.error` and `lzma.LZMAError` to the zip and comic error lists. Also `meta._number("nan")` and `_number("1e999")` raise outside the `try`.
- **Effort:** small. **Recommendation:** Yes (first).

### 13. One Markdown file can pin the server's CPU for minutes
- **What:** the Markdown renderer has a 256 KB cap but no time limit and no cache (`pluteus/markdown.py:19`, `:204-206`, `:222-225`). CONFIRMED, measured: repeated `[a](` takes 2.6 s at 16 KB and 10.8 s at 32 KB (quadratic), so minutes at the cap. Two of the project's own regexes do the same (`:107`, `:82`, `:99`): a 60,000-character heading takes 10.6 s.
- **Why:** a `readme.md` or `artist.md` is rendered on every visit to its folder. Anyone with an upload folder or the notes right can plant one.
- **Fix:** cache rendered HTML by path, date and size; one render at a time with a time budget, falling back to the text viewer; cap line length before the two regexes.
- **Effort:** medium. **Recommendation:** Yes.

### 14. EPUB metadata has no length caps and expands XML entities
- **What:** `pluteus/meta.py:54-109`, `thumbs.py:34-36`. CONFIRMED: a 1.7 KB EPUB with an entity declaration parsed to a 90,000,000-character title, which then goes into SQLite, the page and the OPDS feed. A plain 1.9 MB title does the same at smaller scale.
- **Why:** SECURITY.md says documents that declare entities are refused; the EPUB reader does not refuse them.
- **Fix:** refuse a container or OPF containing `<!DOCTYPE` or `<!ENTITY`; cap title, series and language at about 300 characters and authors at about 20 names.
- **Effort:** small. **Recommendation:** Yes.

### 15. The entity check for `.nfo` files is skipped by a UTF-16 file
- **What:** `pluteus/aboutfiles.py:21` looks for the bytes `<!ENTITY`. CONFIRMED: a UTF-16 `artist.nfo` passes; only the bundled expat stopped the bomb.
- **Fix:** check the decoded text, refuse `<!DOCTYPE` too, or refuse anything that is not UTF-8.
- **Effort:** small. **Recommendation:** Yes.

### 16. Pillow limits are weaker than the comments say
- **What:** `pluteus/fetchimage.py:135-139`, `thumbs.py:22`, `:66-72`. CONFIRMED, measured: (a) Pillow only warns at `MAX_IMAGE_PIXELS` and raises at twice it, so a 155 KB, 49-megapixel PNG was fully decoded (about 150 MB); (b) a 682 KB progressive JPEG of 119 megapixels cost 368 MB, since draft mode does not help progressive files; (c) both modules write the same global, so after one thumbnail the logo limit is off (a race, plausible).
- **Why:** six at once is about 2 GB of memory from files under 1 MB each.
- **Fix:** stop using the global; check width times height against a hard cap before `load()`; give progressive JPEGs the 20-megapixel limit.
- **Effort:** small. **Recommendation:** Yes.

### 17. A zip's whole file list is read on every call, with no cap
- **What:** `pluteus/ziputil.py:47-49` and five more places, `cbz.py:164-177`, `meta.py:105`. CONFIRMED: a 27.6 MB zip with 300,000 empty entries took 1.1 s for one entry and 173 MB to list. The caps apply after everything is loaded.
- **Why:** with the 4 GB upload default a user can upload an archive with tens of millions of entries; renamed to `.cbz` or `.epub`, listing the upload folder triggers the parse.
- **Fix:** read the end-of-central-directory record first and refuse archives declaring more than about 100,000 entries or a directory over about 32 MB.
- **Effort:** medium. **Recommendation:** Yes.

### 18. The Matroska reader raises on a crafted Info element
- **What:** `pluteus/mediainfo.py:333-369`: a 200-byte TimestampScale gives `OverflowError`, which is not caught. CONFIRMED: `/view/.../film.mkv` and `/cover/...` answered 500. An infinite duration passes through and becomes invalid JSON. Not run but following from the code: one such file fails a whole Subsonic directory call.
- **Fix:** accept scale elements of at most 8 bytes, refuse non-finite durations, catch `ArithmeticError`.
- **Effort:** small. **Recommendation:** Yes.

### 19. Unbounded element loops in the Matroska and MP4 readers
- **What:** `pluteus/mediainfo.py:353-359`, `:458-490`, `:289`. CONFIRMED, timed: an attachment filled with 2-byte elements costs one seek each (4 MB took 1.6 s on local disk, linear in size); `read_chapters` walks every top-level atom of a fragmented MP4; a Matroska file with no SeekHead costs up to 5,000 seeks.
- **Why:** each step is a round trip on SMB.
- **Fix:** cap attachment children (about 64) and top-level atoms (about 2,000), stop after `moov`, lower the walk limit.
- **Effort:** small. **Recommendation:** Yes.

### 20. Uploads: parallel requests each get the full quota
- **What:** the quota is measured once before the body is read (`pluteus/app.py:1028-1030`, `:1621-1625`; `stationsave.py:104-106`). CONFIRMED by reading, by two reviewers.
- **Why:** N parallel uploads store N times the quota; only the 512 MB free-disk reserve stops it.
- **Fix:** a per-user lock or in-flight byte counter, or re-measure under a per-user lock when each file is finalised.
- **Effort:** medium. **Recommendation:** Yes.

### 21. Refused upload parts are remembered without bound
- **What:** every part past the 100th still appends its label to `failed` (`pluteus/uploads.py:148-150`, `:199`). CONFIRMED: 5,000 parts kept 19 MB. `too_big` allows 100 times `upload_max_mb` and passes any request without Content-Length.
- **Fix:** stop reading once `failed` passes about 200 entries; cut labels to the name limit.
- **Effort:** small. **Recommendation:** Yes.

### 22. Upload leftovers and shared temp names
- **What:** (a) a killed server leaves hidden `.up-*.part` files that count against the quota and that the user cannot see or delete (`pluteus/uploads.py:154`); (b) two threads making the same thumbnail share one temp name, and an empty cache file counts as a hit and is sent with a 24-hour cache header (`thumbs.py:111-126`); (c) two saves of the same station file collide on a fixed temp name. CONFIRMED by reading.
- **Fix:** delete `.up-*.part` files older than a day when the upload folder is first used; random temp suffixes; an empty cache file is a miss.
- **Effort:** small. **Recommendation:** Yes.

### 23. Downloads of uploaded `.js` files are script sources the page policy allows
- **What:** `/dl/uploads/x.js` is served as `application/javascript` from the site's own origin (`pluteus/app.py:2131`), and the policy is `script-src 'self'` (`app.py:70`). CONFIRMED by reading. No HTML injection was found to pair it with, so this is defence in depth.
- **Fix:** serve `/dl` and WebDAV GET as `application/octet-stream` for script and style types, or add `Content-Security-Policy: sandbox` to download responses.
- **Effort:** small. **Recommendation:** Yes.

## 3. Stability and speed

### 24. Waiting thumbnails hold worker threads and starve every other request
- **What:** the gate is taken inside the worker thread (`pluteus/app.py:1893`), so up to 37 of the 40 threads sit blocked on it. CONFIRMED: an OPDS page took 0.00 s alone and 2.76 s while 70 uncached thumbnails were in flight. Read by me.
- **Why:** a cover-grid page asks for 50 to 100 thumbnails at once; on a slow share with large comics the whole site freezes for tens of seconds.
- **Fix:** gate on the event loop (an `asyncio.Semaphore(3)` awaited before `run_in_threadpool`), so waiters hold no thread.
- **Effort:** small. **Recommendation:** Yes (first).

### 25. SQLite calls on the event loop, under the lock the backup holds
- **What:** `sessions.user_for` on every guarded request (`pluteus/app.py:374`), `get_prefs` six times per page (`:412`, `:439-451`), progress writes (`:1987-1991`), settings, login and logout. `state.py` has one connection and one lock, and `backup()` holds it for the whole copy. CONFIRMED by reading.
- **Why:** during the hourly backup, or any slow commit on a bind mount, every connection stalls, including running downloads and relays.
- **Fix:** read prefs once per request; move the session look-up and the writes to the thread pool (or cache sessions briefly in memory); run the backup from a second connection.
- **Effort:** medium. **Recommendation:** Yes.

### 26. A healthy state database is set aside on "database is locked"
- **What:** `pluteus/dbutil.py:37-47` treats every `sqlite3.DatabaseError` as damage; `OperationalError` is a subclass. CONFIRMED: one injected "database is locked" at open renamed `state.sqlite3` to `.corrupt-<time>` and started empty. Read by me. The real-world triggers (a second pluteus process, a full disk, a transient error on a bind mount) are PLAUSIBLE.
- **Why:** sessions, reading progress, app keys and stored streams appear lost. The data is still in the `.corrupt-` file, but nobody is told.
- **Fix:** set the file aside only on a failed `quick_check` or "malformed" / "not a database"; let other errors stop the start loudly.
- **Effort:** small. **Recommendation:** Yes.

### 27. Every storage error becomes a silent 503
- **What:** `guarded` turns any `OSError` into "The library storage is not available right now" with no log line (`pluteus/app.py:505-507`). CONFIRMED by reading; read by me.
- **Why:** a permission error on one file, a full disk during a note save, or a socket error inside a handler all look like an offline share. Clients retry for ever and there is nothing to diagnose from.
- **Fix:** log method, path and error before answering; keep 503 for `fs.Unavailable`, and let other errors be a logged 500.
- **Effort:** small. **Recommendation:** Yes.

### 28. One Subsonic request with a deep offset walks the whole music library
- **What:** `_album_run` skips `offset` albums by listing them (`pluteus/subsonic.py:577-579`), and the offset is unbounded. CONFIRMED: `getAlbumList2?offset=1000000&size=1` on a 30-artist test tree listed every artist, album and disc folder. The resume slot is shared by `getAlbumList2` and `search3`, so two devices restart from the top on each page.
- **Why:** this breaks the no-walk rule from outside: one request, tens of thousands of SMB listings, in a thread that does not stop when the client leaves.
- **Fix:** refuse an offset that does not continue a kept run; key the run by client as well.
- **Effort:** medium. **Recommendation:** Yes.

### 29. A folder without a cover costs 40 probes, repeated every 10 seconds
- **What:** `folder_cover` tries every name, spelling and extension with `isfile` (`pluteus/mediainfo.py:129-145`), and a miss is cached for 10 seconds although the folder date is already compared. CONFIRMED: 40 calls counted. On Linux `Cover.JPG` and `FOLDER.JPG` are never found.
- **Why:** a page of 100 cover-less folders is 4,000 round trips on SMB.
- **Fix:** one `os.scandir` with a case-insensitive match; give misses the same lifetime as hits.
- **Effort:** small. **Recommendation:** Yes.

### 30. Subsonic listings stat every entry when only names are needed
- **What:** `_folder` always lists with `stat=True` (`pluteus/subsonic.py:363`). CONFIRMED by reading. At about 0.7 ms per file on a Docker bind mount, a 500-album page is several seconds of stats.
- **Fix:** a `stat` argument, False where sizes and dates are not used.
- **Effort:** medium. **Recommendation:** Yes.

### 31. Stream-folder helpers list folders a second time on every page
- **What:** `is_stream_folder` scans the folder again and follows the first child down (`pluteus/streams.py:31-48`); `count()` walks Radio and IPTV three levels on every home page (`:62-68`); `collect()` calls `getmtime` per station, up to 5,000 per page (`:95-111`). CONFIRMED by reading.
- **Fix:** pass in the listing already read, cache `count()` for a minute, take the time from the entry.
- **Effort:** medium. **Recommendation:** Yes.

### 32. A `layout:` note is not capped; a flat library costs three opens per artist
- **What:** `layout: a/b/c/d/e/f/g/artist` gives depth 8 while the config caps `artist_level` at 5 (`pluteus/subsonic.py:150-159`); each top folder is probed for `about.md`, `About.md` and `ABOUT.MD` (`:459`). CONFIRMED by reading.
- **Fix:** clamp the depth to the same maximum; try one spelling on Windows.
- **Effort:** small. **Recommendation:** Yes.

### 33. No 304 for `/raw` and `/dl`; exact-match `If-None-Match` on covers and thumbnails
- **What:** `pluteus/app.py:1350`, `:2132` (no conditional handling), `:1788`, `:1926` (string equality, so `W/"..."` from a compressing proxy never matches). CONFIRMED by reading.
- **Why:** after five minutes every image or PDF is downloaded again in full.
- **Fix:** one `not_modified(request, etag)` helper used by all five routes.
- **Effort:** small. **Recommendation:** Yes.

### 34. Config edits and preference updates are read-modify-write with no lock
- **What:** `pluteus/useredit.py:228-256`, `libedit.py:137-150` (two admin POSTs at once: the second drops the first); `app.py:967-980`, `:996-1013` (two tabs: a theme change lost to a view switch). CONFIRMED by reading for the config; PLAUSIBLE for the prefs.
- **Fix:** one lock around read, edit and save in `apply_edit`; a `state.update_prefs(user, fn)` under the state lock.
- **Effort:** small. **Recommendation:** Yes.

### 35. The admin server can fail to start without anyone noticing
- **What:** if the admin port is taken, uvicorn exits inside its thread; the console still prints "Admin page on ..." and first run opens a browser on a dead port (`pluteus/adminapp.py:519-527`, `__main__.py:196-200`). CONFIRMED.
- **Fix:** wait briefly for `server.started` or the thread's death and say which.
- **Effort:** small. **Recommendation:** Yes.

### 36. Backup and supervise robustness
- **What:** a backup that fails midway leaves a partial file that counts as the newest, so there is no retry for 20 hours and a good copy is pushed out (`pluteus/state.py:380-406`). `supervise.py:76-99` restarts every 10 seconds with no back-off and a log that is never rotated. `pluteus share` leaves its temp data folder behind on Windows, since nothing closes the databases (`share.py:189-190`). PLAUSIBLE.
- **Fix:** write the backup to a `.tmp` name and rename; back off after repeated fast exits and rotate the log; close the databases before the cleanup.
- **Effort:** small. **Recommendation:** Yes.

## 4. Readers, players and client answers

### 37. The ebook reader skips a chapter's last page on screens with a fractional pixel ratio (your open bug)
- **What:** epub.js decides "is there another page" with `scrollLeft + offsetWidth + delta <= scrollWidth` (`pluteus/static/vendor/epub.min.js`, default view manager `next()`; called from `reader-epub.js:178`). The expression is in the vendored file (checked by me). Mechanism CONFIRMED in headless Chrome: at pixel ratio 2.625 with a 411-pixel page the scroll offset reads back as 411.0476, so on the second-to-last page the sum is 1233.05 against 1233 and the reader jumps to the next chapter. Skipped every time at ratios 2.625, 2.75 and 1.5; never at 1, 1.25 and 3. That this is the cause on your phone is PLAUSIBLE: it depends on the phone's pixel ratio.
- **Why:** it matches the report (phone only, last page of a chapter) and explains why desktop tests never caught it. The error grows per page, so a one-pixel tolerance is not enough.
- **Fix:** change the vendored test to allow half a page of slack (`<= scrollWidth + delta / 2`), note the patch in `vendor/README.md`, and add a browser test at phone width with a forced fractional pixel ratio that fails before the patch.
- **Effort:** medium. **Recommendation:** Yes (first).

### 38. A failed progress fetch overwrites the saved position with the start of the book
- **What:** on a failed GET the readers open at page 1 and then save (`pluteus/static/reader-epub.js:289-294`, `reader-comic.js:176-182`, `viewer-pdf.mjs:155-161`). CONFIRMED by code trace, not run.
- **Why:** on a flaky phone connection the real position is lost for good.
- **Fix:** a "progress loaded" flag; after a failed GET, do not save until a page has been turned.
- **Effort:** small. **Recommendation:** Yes.

### 39. Remove buttons carry a confirm text that nothing shows
- **What:** `button[data-confirm]` at `pluteus/web.py:908` (remove a note) and `:1041` (forget view choices), and the form at `:855` (delete a stored stream login); the only handler is for `form[data-confirm]` in `static/uploads.js:6`, loaded only on writable folder pages. CONFIRMED; the grep was repeated by me.
- **Why:** one tap on Remove, next to Save and Preview, deletes the note; for a library note it deletes the shared file for everybody. The text also says "The library is not touched", which is wrong for a library note.
- **Fix:** move the handler into a script every page loads and cover buttons; correct the text.
- **Effort:** small. **Recommendation:** Yes.

### 40. Next or Previous on a song page hands the old song to the player bar
- **What:** `pagehide` fires with the media still playing (`pluteus/static/viewer-media.js:66-73` against `:336`, `:384`). PLAUSIBLE: traced, not run in a browser.
- **Fix:** a flag set by `go()` that the `pagehide` handler checks.
- **Effort:** small. **Recommendation:** Yes.

### 41. Reader keys ignore Alt and Ctrl
- **What:** `reader-comic.js:104-117`, `viewer-pdf.mjs:98-105`, `viewer-image.js:39-42`, `reader-epub.js:198-203`. CONFIRMED by reading. Alt+Left turns a page where the browser should go back; Ctrl+plus and Ctrl+minus no longer zoom the browser in the comic and PDF readers.
- **Fix:** return early when Alt, Ctrl or Meta is held, as `viewer-media.js:172` does.
- **Effort:** small. **Recommendation:** Yes.

### 42. A pinch or a pan of a zoomed page counts as a swipe
- **What:** no swipe handler checks the number of touches or the browser's own zoom (`reader-comic.js:153-160`, `reader-epub.js:243-249`, `viewer-pdf.mjs:109-115`, `viewer-image.js:44-50`). PLAUSIBLE: not run on a touch device.
- **Fix:** ignore the gesture when it began with more than one touch or the viewport is zoomed.
- **Effort:** small. **Recommendation:** Yes.

### 43. The shell keeps every page it has ever shown
- **What:** `shell.js:38`, `:215` drop a listener only when it throws, and Chrome does not throw. CONFIRMED in headless Chrome: five page visits left five live listeners.
- **Why:** memory grows over a long TV or phone session, and every old page's paint function runs on each state change.
- **Fix:** clear both lists on the frame's `load` event.
- **Effort:** small. **Recommendation:** Yes.

### 44. The shell's address stays on the first film after in-page advance
- **What:** `viewer-media.js:378` changes the frame's address; the shell mirrors only on the frame's `load` (`shell.js:271-275`). CONFIRMED by trace. A reload or restored tab reopens the first film.
- **Fix:** let the page tell the shell to mirror.
- **Effort:** small. **Recommendation:** Yes.

### 45. The editor opens empty for an existing note between 64 KB and 256 KB
- **What:** `notes.py:112` returns nothing above 64 KB, and the Edit link is still offered (`app.py:853-857`, `:1220-1228`). PLAUSIBLE. No data is lost, but every save is refused.
- **Fix:** do not offer Edit above the limit, or say "too large to edit here".
- **Effort:** small. **Recommendation:** Yes.

### 46. The ebook page-count cache in the browser has no limit
- **What:** one `localStorage` entry per book and per re-tag, never removed (`reader-epub.js:133-141`). CONFIRMED by reading. Once the roughly 5 MB quota is full, every book is re-counted on each open.
- **Fix:** keep the newest N entries, or move the cache to IndexedDB.
- **Effort:** medium. **Recommendation:** Yes.

### 47. Multi-disc albums play interleaved when songs have track tags but no disc tags
- **What:** the sort key is disc, then track (`pluteus/subsonic.py:394`). CONFIRMED: `Box/CD1/01..03` and `Box/CD2/01..03` came back as CD1/01, CD2/01, CD1/02, and so on.
- **Fix:** put the parent folder in the key.
- **Effort:** small. **Recommendation:** Yes.

### 48. Small Subsonic answer bugs
- **What:** all CONFIRMED in-process. (a) The radio list cuts at 300 files before dropping the logo files, so 200 stations with logos list 150 (`subsonic.py:665-667`). (b) `createBookmark` is refused, since the no-op list names `savebookmark`, which does not exist (`:199`); audiobook apps show an error on every position save. (c) `id=²` gives HTTP 500, since `"²".isdigit()` is true (`:699-700`, `:858-860`, `:892`). (d) XML answers put the biography and lyric text in attributes where the schema wants element text (`:99-109`); JSON apps are fine. (e) `getOpenSubsonicExtensions` needs a login where the spec says public, and `tokenInfo` is missing (PLAUSIBLE).
- **Fix:** filter then cut; rename to `createbookmark`; `isascii() and isdigit()` with a length limit; let `_node` render chosen keys as child elements.
- **Effort:** medium. **Recommendation:** Yes.

### 49. One odd file name breaks a whole list (Linux libraries)
- **What:** a name that is not valid Unicode, or holds a control character, fails the whole answer: WebDAV PROPFIND (`pluteus/webdav.py:54`, PLAUSIBLE), OPDS (`opds.py:31-34`, `:111`, CONFIRMED at function level), Subsonic `getIndexes` and `getArtists` (`subsonic.py:64`, `:124-127`, CONFIRMED). NTFS names cannot hold these, so it bites on a Linux file system with legacy Latin-1 names.
- **Fix:** one helper that cleans a name for XML and JSON (strip characters XML does not allow, encode with replacement), used by all three.
- **Effort:** small. **Recommendation:** Yes.

### 50. A cast device may bind to another Chromecast than the one configured
- **What:** `get_chromecasts(known_hosts=[...])` returns every device found plus the known host, and the code takes `casts[0]` (`pluteus/castdevices.py:75-81`). PLAUSIBLE: pychromecast is not installed here; from its API. If `wait()` raises, the browser is never stopped (one leak per 30-second retry).
- **Why:** on a native install with several Chromecasts "Kids TV" may play in the living room, and the per-device user rule means nothing.
- **Fix:** pick the cast whose host equals the configured address; stop the browser in a `finally`.
- **Effort:** small. **Recommendation:** Yes.

### 51. Radio edge cases
- **What:** (a) "Fetch missing logos" rewrites a station file as UTF-8 with replacement, so a cp1252 "é" becomes a replacement mark for good (`pluteus/stationsave.py:278-281`, CONFIRMED by reading). (b) Shoutcast servers that answer `ICY 200 OK` cannot be relayed, since `http.client` refuses the status line (`relay.py:205`, `:228-230`, PLAUSIBLE). (c) The update check reads only the first 100 tags (`updatecheck.py:82`, PLAUSIBLE).
- **Fix:** use the same decode fallback as `radio.read_file`; a small response subclass that takes `ICY` as HTTP/1.0; use `/releases/latest`.
- **Effort:** medium. **Recommendation:** Yes for (a), Later for (b) and (c).

## 5. Packaging, CI and tests

### 52. The Windows task installer runs the internet-facing server as SYSTEM from a user-writable folder
- **What:** `tools/install-task.ps1:52-53`, `:96`: the venv's Python inside the repo, run as SYSTEM with highest rights. CONFIRMED by reading.
- **Why:** a server that parses untrusted archives, images and media as SYSTEM turns any parser bug into the whole machine, and any process running as the normal user can swap a file and be SYSTEM at next boot.
- **Fix:** default to a low-privilege account (LocalService with rights on the data folder), and warn when the repo is writable by non-admins.
- **Effort:** medium. **Recommendation:** Yes.

### 53. Nothing is pinned: the exe and the image take whatever PyPI serves that day
- **What:** `pyproject.toml:11` (`starlette>=0.40` and so on), `Dockerfile:1`, `:12`, `.github/workflows/release.yml:35`. CONFIRMED. The local venv is Python 3.14 with uvicorn 0.54 while CI and Docker use 3.12, so the tested set is not the shipped set.
- **Fix:** a hashed lock file installed with `--require-hashes` in the Dockerfile and release jobs; base image by digest; actions by commit.
- **Effort:** medium. **Recommendation:** Yes.

### 54. The release is published before the Linux build exists
- **What:** the Windows job creates the release; the Linux job runs after it (`.github/workflows/release.yml:55-65`). CONFIRMED. The Linux build also sits on `python:3.12-bullseye`, an end-of-life base (`tools/linux-build/Dockerfile:3`; PLAUSIBLE, not checked against the registry).
- **Why:** if the Linux build fails, a public release exists with only the exe and the update check announces it.
- **Fix:** both jobs upload artifacts and a third job publishes; build Linux on a maintained old-glibc base.
- **Effort:** medium. **Recommendation:** Yes.

### 55. The exe and the image are barely exercised before publishing
- **What:** the exe smoke test runs `share` and fetches four addresses; the Docker one runs `--version` and `--help` (`release.yml:43-53`, `docker.yml:41-45`). The admin app, thumbnails, tinytag and `serve --config` never run frozen. The gap is CONFIRMED; an actual breakage is PLAUSIBLE.
- **Fix:** both smoke steps start `serve` with a generated config and fetch `/healthz`, an admin page and one thumbnail.
- **Effort:** medium. **Recommendation:** Yes.

### 56. The browser tests never run in CI, and CI tests one Python version
- **What:** all six `*_browser.py` files skip without Playwright, which the dev extra does not install (`.github/workflows/tests.yml:20-26`, `pyproject.toml:14`). `requires-python >= 3.11` is never tested on 3.11. CONFIRMED.
- **Why:** a green CI run says nothing about the reader fixes the last releases were about.
- **Fix:** a `browser` extra and a CI job that fails if those tests skip; add 3.11 to the matrix.
- **Effort:** medium. **Recommendation:** Yes.

### 57. Nothing checks that every admin route is guarded
- **What:** the cross-site test names 7 of 17 POST routes (`tests/test_adminweb.py:113-120`). All 17 are guarded today (each was checked), but a new route without the decorator would pass.
- **Fix:** iterate `app.routes` and assert 401 without a login and 403 for a cross-site POST on each.
- **Effort:** small. **Recommendation:** Yes.

### 58. The systemd unit makes the config read-only, so the admin Users and Libraries tabs cannot save
- **What:** `tools/pluteus.service:11-16` allows writes only to `/var/lib/pluteus`; the editors write a `.bak` and a temp file beside `/etc/pluteus/config.toml`. CONFIRMED by reading; not run under systemd.
- **Fix:** add `ReadWritePaths=/etc/pluteus`, or move the config under `/var/lib/pluteus` in the unit and the README.
- **Effort:** small. **Recommendation:** Yes.

### 59. The typed library path on the admin page is not confined to `library_bases`
- **What:** the picker is confined, the typed path is not (`pluteus/adminapp.py:292-294`, `libedit.py:122-134`). CONFIRMED. A LAN admin can publish `/`, `/app` or another user's home to everyone.
- **Fix:** when `library_bases` is set, require the real path to be inside a base.
- **Effort:** small. **Recommendation:** Yes.

### 60. The work e-mail address is in 236 of 274 commits
- **What:** the author field up to about 20 October; newer commits use the GitHub noreply address. No addresses in file contents. CONFIRMED.
- **Why:** your rule: no work e-mail in public repos. This only matters on the day the repo goes public.
- **Fix:** before going public, rewrite the authors with a mailmap and force-push, or publish from a squashed history.
- **Effort:** medium. **Recommendation:** Later (before the repo goes public; the rule decides it then).

### 61. Hook files are tracked without the executable bit
- **What:** `git ls-files -s .githooks` shows mode 100644. CONFIRMED. Git for Windows runs them anyway; a Linux or macOS clone skips both gates with only a hint line.
- **Fix:** `git update-index --chmod=+x` on both.
- **Effort:** small. **Recommendation:** Yes.

### 62. Small test-rule breaks
- **What:** `tests/test_unit.py:78` uses the Unix-style fake path `/no/such/folder/anywhere` (only passed to `isdir`, so nothing is made, but it breaks your rule). Free ports are picked by bind-then-close in `test_adminweb.py:276-279`, `test_castdevices.py:154` and the browser tests. Fixed sleeps in `test_relay.py:188`, `:350`, `:389`, `:405` and `test_updatecheck.py:92-104` are the likeliest flakes. The `httpx` test client is deprecated in Starlette (`tests/conftest.py:7`), which the unpinned dependencies will pull in unannounced. CONFIRMED.
- **Fix:** `tmp_path / "missing"`; port 0 read back; poll on a condition; `httpx2`.
- **Effort:** medium. **Recommendation:** Yes.

## 6. Smaller hardening

### 63. Quoted booleans in the config silently mean true, including `admin`
- **What:** `bool(u.get("admin", False))` (`pluteus/config.py:260-261`, `:334-345`). CONFIRMED: `admin = "false"`, `trust_proxy = "false"` and `webdav = "no"` all parsed as True.
- **Fix:** one `_flag` helper that raises `ConfigError` unless the value is a real boolean.
- **Effort:** small. **Recommendation:** Yes.

### 64. First run accepts an administrator name that makes the new config unloadable
- **What:** `pluteus/firstrun.py:19`, `:42`, `:115-129`. CONFIRMED: "Bjørn", "ole." and "Nul" pass the question, the config is written, and the next start refuses it without asking again.
- **Fix:** validate the built text with `parse_config` before writing, and ask again.
- **Effort:** small. **Recommendation:** Yes.

### 65. Wrong value types in the config raise raw exceptions
- **What:** `name = 5`, `session_days = "x"` and friends give a traceback at start and a 500 from the reload and the admin text save (`pluteus/config.py:248`, `:294`, `:324`, `:353`). CONFIRMED.
- **Fix:** wrap `parse_config` and re-raise as `ConfigError`.
- **Effort:** small. **Recommendation:** Yes.

### 66. A re-created user inherits the old user's data
- **What:** app keys are purged at most hourly and only if the name is absent at that moment; progress, prefs, shelves, bookmarks and sync rows are never purged (`pluteus/state.py:249-260`). CONFIRMED by reading.
- **Fix:** one function that purges all per-user tables, called when a reload sees a name disappear.
- **Effort:** medium. **Recommendation:** Yes.

### 67. Removing a user deletes the comments above the next table
- **What:** `pluteus/useredit.py:163-170`. CONFIRMED. `libedit.remove_root_text` already handles this.
- **Fix:** trim trailing filler lines off the removed range.
- **Effort:** small. **Recommendation:** Yes.

### 68. The normal log records the length of every wrong password
- **What:** "wrong password (7 characters)" in a warning that is always on (`pluteus/app.py:348`, `:405`). CONFIRMED. SECURITY.md accepts lengths in the debug log only.
- **Fix:** keep the length for the debug mark; log only "wrong password".
- **Effort:** small. **Recommendation:** Yes.

### 69. `config.toml.bak` gets default permissions; no sync before the replace
- **What:** `pluteus/useredit.py:196-207`, `firstrun.py:98`. CONFIRMED by two reviewers (Linux only): a 0600 config gets a world-readable `.bak` with the hashes. A power cut during the replace can leave an empty config (PLAUSIBLE).
- **Fix:** `copymode` onto the `.bak`, create the first config 0600, flush and `fsync` before the replace.
- **Effort:** small. **Recommendation:** Yes.

### 70. Forged lines in the access log
- **What:** the access line is built from the decoded path (`pluteus/app.py:89-99`, `:154`). CONFIRMED: `GET /healthz%0A...` wrote a second, invented line. No login needed.
- **Fix:** build the line from `raw_path`, or escape characters below 0x20.
- **Effort:** small. **Recommendation:** Yes.

### 71. `/api/station-logo` does not check the last path part
- **What:** `os.path.join(folder, parts[-1])` without `check_segment` (`pluteus/app.py:1605-1613`). CONFIRMED: on Windows `Sub%5Ca.m3u` reached a subfolder that was never checked. Escaping the upload folder is blocked only because long-path form does not resolve dot-dot.
- **Fix:** use `lib.upload_entry(user, parts)`, as `file_op` does.
- **Effort:** small. **Recommendation:** Yes.

### 72. The shared anonymous user can change settings for every anonymous visitor
- **What:** the settings POST has no anonymous check (`pluteus/app.py:989-1014`); `browse` at `:964` deliberately skips remembering for that user. CONFIRMED by reading. Shelves, progress and bookmarks are shared the same way.
- **Fix:** refuse settings writes for the anonymous user; decide the same for shelves, progress and bookmarks, or document it.
- **Effort:** small. **Recommendation:** Yes.

### 73. API writes with Basic credentials and no Origin are refused as cross-site
- **What:** `pluteus/app.py:308-319` against the comment at `:459` ("for our own scripts"). CONFIRMED by reading. A script doing `PUT /api/progress/...` with Basic gets 403.
- **Fix:** skip the origin check when the request was authenticated by an Authorization header and carries no session cookie.
- **Effort:** small. **Recommendation:** Later (no script uses it today).

### 74. Login caches and limiter tables: small leaks and races
- **What:** (a) a login mid-hash at reload time can put the old password back in the cache for 300 seconds (`pluteus/auth.py:51-56`, `:86-90`, `:171-176`, PLAUSIBLE). (b) `_strikes` and `_locked` are never pruned, so invented names grow them without bound (`ratelimit.py:32-33`, `:93-95`, CONFIRMED by reading). (c) The vault's MAC does not cover the owner or entry name, so someone who can write the database can move one user's sealed login to another (`vault.py:56-60`; inside the documented limit).
- **Fix:** a generation counter on the cache; prune old entries with the `_fails` sweep; include `username|name` in the MAC input.
- **Effort:** small. **Recommendation:** Yes.

### 75. The logo fetch: bad ports raise, NAT64 counts as public
- **What:** `http://host:abc/` raises `ValueError` out of `fetch_logo`, whose docstring says it never raises (swallowed by today's only caller); `64:ff9b::/96` is treated as public and on a NAT64 network reaches private addresses (`pluteus/fetchimage.py:53-65`, `:155-160`). CONFIRMED.
- **Fix:** wrap the split and re-raise as `Refused`; refuse the NAT64 prefix.
- **Effort:** small. **Recommendation:** Yes.

### 76. SECURITY.md and the code disagree in three places
- **What:** it says the relay's audio type is "never taken from the source" (it is passed on, limited to `audio/*` with nosniff, so harmless); that an app key "can only read" (a key login stars, saves the queue and opens the relay); and "at most 10 seconds" for fetches (card 9).
- **Fix:** correct the text when cards 2 and 9 are done.
- **Effort:** small. **Recommendation:** Yes.

### 77. `create_app` is one 2,100-line closure
- **What:** every handler is a nested function sharing about 25 closure variables (`pluteus/app.py:258-2377`). `mark()` at `:350` uses `debug`, bound only at `:2355`. The path, `If-None-Match` and prefs logic is repeated per handler with small differences, which is where cards 25, 33 and 71 come from.
- **Fix:** an `AppContext` dataclass for the shared objects; `resolve`, `csrf_ok` and `guarded` into their own module; handlers split into route modules that each expose `routes(ctx)`.
- **Effort:** large. **Recommendation:** Later (after the fixes above; it is a pure move with the test suite as the net, and best done when nothing else is in flight).

## Checked and found sound
- **Paths:** dot-dot, backslash and colon segments, alternate data streams, trailing dots and spaces, 8.3 short names of marked and dot folders, letter-case variants, and a junction leaving the root were all refused in a run on Windows.
- **Guards:** every main and admin route is guarded, or deliberately public, or token-authenticated. The read-only key is enforced for all unsafe methods. Cross-site checks, redirects and header values are sound.
- **Pages:** every dynamic value traced through `web.py` is escaped; 35 hostile Markdown inputs produced nothing executable; the only `innerHTML` writes are server-rendered.
- **Passwords and sessions:** scrypt with a per-user salt, constant-time compares, 256-bit session tokens stored only as hashes, invalidation on password change, logout and removal.
- **Outbound fetches:** every resolved address is checked, the connection goes to the checked address, redirects are checked again.
- **Uploads:** names sanitised, exclusive creates, nothing overwritten, partial files removed on abort. WebDAV is read-only as claimed.
- **Zip and comics:** entries by index only (no zip-slip), encrypted entries refused, reads bounded by the declared size.
- **SQLite:** WAL, one connection behind a lock, parameterised SQL throughout, online backup, additive schema.
- **Config editing:** unknown keys refused; names with quotes, backslashes and `#` survive; every edit is re-parsed before an atomic replace.
- **Docker:** non-root, read-only root filesystem and dropped capabilities in the compose example, libraries mounted read-only.
- **Tests:** 860 test functions, almost all asserting, `tmp_path` throughout, no network.

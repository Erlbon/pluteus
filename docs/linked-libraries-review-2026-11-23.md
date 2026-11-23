# Critical review: linked libraries and the phone server, 2026-11-23

A critical review of roadmap item 7 (linked libraries, shared servers) and the idea that started it: an old Android phone as a portable
pluteus server and proxy over Tailscale. The question is whether it is usable for the people who would run it and feasible to build
in pluteus as it is. Nothing in the program was changed. Each card is a proposal about the plan, answered Yes / No / Later; the numbers
continue from the last pluteus question (84).

**How it was checked.** Read roadmap item 7 with its research notes, `fs.py` (the library: `roots_for`, `list_dir`, `file_info`),
`webdav.py`, `auth.py`, `state.py` (app keys), `vault.py`, `config.py` (users) and the modules that open files (`cbz.py`, `ziputil.py`,
`mediainfo.py`, `meta.py`, the office and text viewers, `thumbs.py`). The phone facts come from the earlier research for the Termux
guide (`C:\Temp\Claude\pluteus-android\termux-guide.md`). Claims about other products marked "from knowledge" were not checked
live today.

**Left out on purpose:** your two decisions in item 7 stand (shared servers are a separate kind of folder; special remote-server
users). Where a card touches one, it is about how, not whether.

**Effort:** small = under 2 minutes of work, medium = up to 10, large = more than 10.

**Overall.** Linking your own servers is feasible and worth having, **if** it is built as one pluteus asking another pluteus to do the
work, not as one pluteus reading the other's files over the network. Built the second way it touches nearly every module and is the
largest job on the roadmap. The phone is the weakest part of the idea: a fragile server for a job that a copied folder (offline) or a
travel router (proxy) does more reliably. Linking with friends adds trust and consent problems that your own servers do not have, so it
should come after linking your own. Progress that splits between servers is the usability trap most likely to annoy you in daily use.

---

## 1. Feasibility

### 85. Build it as delegation: the other pluteus does the work
- **What:** pluteus lists folders through one place (`fs.py`), but after that nearly every feature opens the file by its path: CBZ
  pages (`cbz.py:64`, `ziputil.py:96`), audio tags and covers (`mediainfo.py:58`), EPUB metadata, the office and text viewers,
  thumbnails. A linked root read as remote files would need every one of those to read over the network with range requests.
- **Why:** that is the expensive design, and it is slow: a CBZ page or an EPUB title means several round trips over Tailscale per file.
  The cheap design is delegation: the linked folder's pages, feeds, downloads, CBZ pages and thumbnails are fetched from the other
  pluteus, which already knows how to make them, and passed through under the server user's rights. Nextcloud's federated shares work
  this way (the owner's server serves the share); a remote file system is what Jellyfin users fall back to, and they call it cumbersome.
- **Fix/Where:** record delegation as the design in item 7. It needs a small versioned pluteus API on the sharing side (folder listing
  as JSON with the extras, plus the existing download, CBZ page and thumbnail addresses), and a pass-through on the receiving side.
- **Effort:** small (the roadmap note). The feature itself stays large either way, but much smaller this way. **Recommendation:** Yes.

### 86. No big restructure before linking; extract only what delegation needs
- **What:** your "might" about a more modular pluteus. With delegation (card 85) the linked folder is a pass-through and does not need
  every module to read through a new storage layer. What it needs is a clear place to plug in: the routes in `app.py` (about 2,700
  lines) for listing, download and viewing a folder.
- **Why:** a large restructure of a working 0.9.x program, during a fixes-only period, is the classic way to break things that work
  (your rule: user-facing features never ship broken). Restructure pays when there is a second storage kind to serve; delegation avoids
  creating one.
- **Fix/Where:** when item 7 starts, step 1 moves the folder, download and viewer routes out of `app.py` into their own module with a
  "local or linked" switch. No general plugin system.
- **Effort:** large (when it starts). **Recommendation:** Yes (this scoped version, not a general restructure).

### 87. Version the server-to-server API from day one
- **What:** two linked servers will often run different pluteus versions (yours updated, a friend's not).
- **Why:** without a version in the handshake, a change in one release silently breaks the link on the other side, and the user sees
  an empty or broken folder.
- **Fix/Where:** the sharing side answers a `/link/v1/...` address and states its version and what it supports; the receiving side
  says "this server is too old / too new" in words.
- **Effort:** small (part of the design). **Recommendation:** Yes.

### 88. Test linking with two servers in one test process
- **What:** the test suite builds apps with Starlette's test client on `tmp_path` folders.
- **Why:** two apps in one process (one sharing, one linking through it) can test the whole link, keys, rights and failure cases
  without a network or a second machine. That makes the feature testable at the same level as the rest.
- **Fix/Where:** a test helper that wires the receiving app's outgoing calls to the sharing app's test client.
- **Effort:** medium. **Recommendation:** Yes.

## 2. Usability

### 89. A linked server that is down must say so, never show an empty folder
- **What:** pluteus has been here before: when Docker lost the F: drive, libraries showed "0 items", which looked like data loss.
- **Why:** a linked server will be unreachable often (laptop asleep, friend's server off, Tailscale down on one side). If that shows as
  an empty folder, people will think files were deleted.
- **Fix/Where:** the linked folder shows its state in words ("Home is not reachable since 14:02; trying again") and the last time it
  answered. OPDS and WebDAV give an error, not an empty list.
- **Effort:** medium. **Recommendation:** Yes.

### 90. Reading progress will split between servers
- **What:** you read a book from the home library through the phone. The progress is saved where you read: on the phone. Back home, the
  home server does not know it, and "Continue reading" is wrong in both places.
- **Why:** of everything here, this will annoy you most in daily use. KOReader sync and Kobo sync have the same problem and solve it by
  sending progress to one owner.
- **Fix/Where:** progress for a linked book is sent to the server that owns the book, under the reader's own name there, when that
  server is reachable; kept locally and sent later when it is not. That needs the reader to exist as a user on the owner's server, so
  it fits your own servers better than friends' servers (card 93).
- **Effort:** large. **Recommendation:** Later (decide with the decision page; do not build linking without a stated answer).

### 91. Offline copies: use a copied folder, not a sync feature
- **What:** the "offline half" of the phone idea is keeping part of a library on the device.
- **Why:** sync is the hardest part of the whole idea (what to keep, space, deleted files, conflicts), and pluteus already serves any
  folder. Copying chosen folders to the phone (by cable, or `adb push`) and adding them as an ordinary local root gives the offline
  library today, with no new code. Progress is the only thing lost, and card 90 covers that.
- **Fix/Where:** take "offline copies" (step 4) off item 7; the Termux guide says how to copy folders onto the phone.
- **Effort:** small. **Recommendation:** Yes.

### 92. Users on the receiving side are one anonymous user on the sharing side
- **What:** every local user who opens a linked folder reaches the other server as the same server user.
- **Why:** for your own servers that is fine. For a friend's server it means the friend cannot see or limit which of your users read
  what, and whatever your 18+ setting is, the friend's server user decides it for all of them.
- **Fix/Where:** say this plainly on the admin page where a link is made ("everyone you let see this folder reads as one guest of
  Home"), and let the receiving side choose which local users see each linked folder.
- **Effort:** small (the text); medium (the per-user choice). **Recommendation:** Yes.

## 3. Security and consent

### 93. Link your own servers first; friends later
- **What:** your own servers share one owner, one Tailscale network and one set of users. A friend's server shares none of them:
  Tailscale node sharing between two networks, two admins making and swapping keys, two upgrade schedules.
- **Why:** the first version should prove the link where nothing about trust is in doubt. Friends multiply the setup and the questions
  in cards 92 and 94.
- **Fix/Where:** item 7 gets two stages: own servers, then friends' servers.
- **Effort:** small (roadmap note). **Recommendation:** Yes.

### 94. No re-sharing: a linked folder is never shared on to a third server
- **What:** "daisy-chain" taken literally: A links to B, and C links to A. Should C see B's library through A?
- **Why:** if it can, B's owner shares with people they never agreed to. It also opens the door to loops (A to B to A).
- **Fix/Where:** a linked folder is never visible to a server user. Only local folders are shared. That rule also ends the loop question.
- **Effort:** small. **Recommendation:** Yes.

### 95. Treat a lost phone as a lost key
- **What:** the phone keeps the key to the home server (sealed in its vault), and phones get lost.
- **Why:** your laptop-gone rule applies: whoever has the device must not keep access. The key is read-only and revocable, which limits
  the damage, but only if revoking it is quick and obvious.
- **Fix/Where:** on the sharing side, each linked server is a row on the admin page with "last seen" and one Revoke button. The key is
  read-only, one per linked server, and stored hashed on the sharing side (research notes in item 7).
- **Effort:** medium. **Recommendation:** Yes.

## 4. The phone

### 96. The phone as a portable proxy is fragile; consider a travel router for that job
- **What:** the proxy job (devices without Tailscale reach Home through a box you carry) needs a box that is always on, survives
  battery management, works on hotel Wi-Fi, and makes its own Wi-Fi network. The phone fights on all four: Android and Asus kill
  background apps, Termux needs care to survive restarts, guest networks often block devices from reaching each other, and Wi-Fi plus
  hotspot at once without a SIM is uncertain on the Zenfone 9.
- **Why:** a small travel router that runs Tailscale itself (GL.iNet models do, from knowledge, not checked today) makes its own Wi-Fi
  network, connects to the hotel's, and passes the home library to every device on it, with nothing to keep alive. That needs no pluteus
  feature at all.
- **Fix/Where:** keep the phone test as planned (it costs nothing and answers the question). Before building anything for the proxy
  use, compare with a travel router.
- **Effort:** small (a comparison). **Recommendation:** Yes.

## Considered and not proposed

- **Tailscale identity instead of keys:** not available to Termux on Android, and it ties pluteus to one VPN. Keys stay; Tailscale can
  be an extra check later.
- **WebDAV as the link:** it lacks the extras (titles, covers, notes, progress), caps a folder at 5000 entries, and reading CBZ pages or
  tags over it is the remote-file design that card 85 argues against. It stays useful for Explorer, Kodi and file managers.
- **A central account like Plex:** pluteus has no central service and should not get one.

## Answers (owner, 2026-11-23)

- **Yes:** 85, 86, 87, 88, 89, 92, 93, 94, 95.
- **95, note:** "This is handled through tailscale." A lost device is removed from the Tailscale network; pluteus adds nothing for
  that beyond the per-link key and Revoke.
- **Later:** 90 (reading progress split between servers).
- **91, Later, with the note:** "Where did this point come from? It was never in the feature list from me? I have never suggested sync
  between the pluteus, and so offline/online is a moot point." Correct: offline copies of a linked library were added to roadmap item 7
  by me, not by the owner. Dropped from item 7 (an untaken suggestion is not a task).
- **96, No, with the note:** "How many people have a travel router? How many people have an outdated phone?" The phone stays the target device.

All rulings are recorded in `docs/ROADMAP.md`, item 7.

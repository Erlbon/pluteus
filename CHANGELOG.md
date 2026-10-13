# Changelog

Release notes are taken from the section of the tagged version (`## 0.5.0 ...`).

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

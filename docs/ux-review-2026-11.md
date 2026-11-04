# UX review, November 2026 (pluteus 0.9.16)

Done on 2026-11-04 against commit `cb5df3b`, with a throwaway config and a small test library (books, comics, music, video,
radio, TV channels). Looked at in the built-in browser at phone size (375 × 812) and at TV size (960 × 540, the usual CSS size of
TV Bro on a 1080p Android TV) with `?tv=1`. The D-pad was tested with real arrow and Enter key presses. No code was changed.

Not tested here: a real Android TV, a real phone, iOS Safari, and playing a working radio or IPTV stream (the test stations point
at an address that does not answer, so only the error paths were seen).

**Severity:** blocker = a main task cannot be done; annoying = it can be done, but it goes wrong or is hard; polish = looks or
feels off. **Effort:** small = under 2 minutes, medium = up to 10, large = over 10. Items marked *mockup first* change how
something looks: 2-3 options to pick from before building.

## The problems you hit before

| What | State now |
|---|---|
| Settings icon looked like the reader's sliders | Fixed in 0.9.14: the site bar has the hex nut, the reader keeps the sliders. Seen on both. |
| Covers view has no "Up one folder" | Still open: see **3**. |
| TV Bro needed "Tap play to continue" for each video | Fixed in 0.9.16 (next file plays in the same page). Not testable in a desktop browser. |
| Empty library showed a bare "0 items" | Fixed in 0.9.13 (`pluteus/web.py:345`). Code checked, not seen live. |

## Top items

| # | Screen | Severity | What |
|---|---|---|---|
| 18 | Readers and players | blocker (TV) | The D-pad cannot reach a single button on any reader or player page |
| 19 | Video and channel pages | blocker (TV) | Play button and controls are below the picture, off the screen |
| 1 | Covers view | blocker (phone + TV) | The star and + sit under the cover: tapping the star opens the book |
| 12 | Player strip | blocker (phone) | The close button (×) is 18 × 8 px |
| 2, 5 | Covers and List views | annoying (TV) | Three D-pad presses per item; Download is in the path |
| 3 | Covers view | annoying | No "Up one folder" |
| 23 | Every toast | annoying | Long messages are cut off inside an oval |
| 27 | Radio and TV grids | annoying | The search box is an unstyled browser box |

---

## Folder listing: Covers view

**1. Star and + are hidden under the cover picture** · blocker (phone + TV) · small
- What goes wrong: on every tile that has a cover, the Favourite (star) and Read next (+) buttons are drawn *under* the picture. On
  a phone, tapping where the star should be opens the book instead (checked: the element under the finger is the `<img>`). On TV the
  D-pad focus lands on the hidden star, so the focus ring almost disappears (only a corner shows). Tiles without a picture (notes,
  books without a cover) show the buttons, so it looks random.
- Where: `pluteus/static/base.css:278` gives `.pl-tile-img` `z-index: 1`; `pluteus/static/base.css:294` gives `.pl-tile .pl-shelf`
  no z-index.
- Fix: `z-index: 2` on `.pl-tile .pl-shelf` (the same as `.pl-tile-badge`, line 282).

**2. Three D-pad presses per tile** · annoying (TV) · medium
- What goes wrong: Right from a tile goes to its star, then its +, and only then to the next tile. A row of six books takes 18
  presses. Together with **1**, two of the three stops are invisible.
- Where: `pluteus/static/tvnav.js:20-32` (`nearest()` takes any focusable element), tiles built in `pluteus/web.py:418`.
- Fix: in TV mode leave the shelf buttons out of Left/Right moves (or give them `tabindex=-1` when `html.pl-tv`), and reach
  favourite / read next another way (the Up key from the tile, or a small menu on the remote's menu key).

**3. No "Up one folder"** · annoying (phone + TV) · small
- What goes wrong: the List view starts with a big "Up: <parent>" row; the Covers view has nothing, so on a phone you reach for the
  breadcrumbs (18 px tall links), and on TV you must press Back. Also true for the radio and TV grids.
- Where: the Up row is built only for the list, `pluteus/web.py:294`; tiles at `pluteus/web.py:328`. Station grids:
  `pluteus/web.py:363`.
- Fix: a first tile "Up: <parent>" with the back icon, in the cover grid and the station grid.

**4. A note file takes the first cover slot** · polish · small
- What goes wrong: `author.md` shows as a big text-icon tile before the books, although the same note is shown under the grid.
- Where: `pluteus/web.py:418` (`_tiles` lists every entry).
- Fix: leave a folder's own note file out of the grid when it is shown as the folder note (the list can keep it).

## Folder listing: List view

**5. Three D-pad presses per row, with Download in the path** · annoying (TV) · small
- What goes wrong: Down from a title goes to that row's Download, then Favourite, then the next title (tested on the album
  folder). Every row has a Download stop, so one Enter too many starts a download on the TV.
- Where: `pluteus/static/tvnav.js:20-32`; row layout `pluteus/static/base.css:354-365`.
- Fix: in TV mode skip `.pl-dl` and `.pl-shelf-btn` on Up/Down (Left/Right still reaches them), or hide the download button in TV mode.

**6. TV focus starts on the first track, not on Play all** · annoying (TV) · small
- What goes wrong: on an album the likely first action is Play all, but the focus lands on track 1.
- Where: `pluteus/static/tvnav.js:85` (the start selector); Play all is added by `pluteus/static/queue.js:17`.
- Fix: put the Play all button first in the start selector when the page has one.

**7. Tall rows, small star and +** · polish (phone) · medium · *mockup first*
- What goes wrong: a file row is about 130 px tall (title, size line, then a line of its own for star and +), so a phone shows
  about five files per screen. The star and + are 32 × 27 px.
- Where: `pluteus/static/base.css:354-365` and `285-287`.
- Fix: put star and + at the right end of the size line, 40 × 40 px each. Rows drop to about 90 px.

**8. The biggest button on each row is Download** · polish (phone) · *question for you*
- What goes wrong: the left square, where a thumb lands, downloads; opening the file needs a tap on the title text. On a phone
  downloading is the rarer action.
- Where: `pluteus/static/base.css:355`, `pluteus/web.py` row builder below line 298.
- Fix: your call. Either keep it (it is deliberate, per the comment at `base.css:351`), or let the square open the file and move
  Download to the size line.

**9. The folder picture pushes the sort bar onto two lines** · polish (phone) · small
- What goes wrong: on an album with `cover.jpg` the picture floats right, and "Modified" wraps to a line of its own.
- Where: `pluteus/static/base.css:266`.
- Fix: below 520 px, make the picture smaller (about 96 px) or let the sort bar clear it.

**10. Video folders have no Play all** · polish · medium
- What goes wrong: a music folder gets "Play all (4)" and "Shuffle"; a folder of films gets nothing, although the video page now
  plays on to the next file by itself (0.9.16). Sibling gap.
- Where: `pluteus/static/queue.js:1-27` (audio only).
- Fix: on a video folder, "Play all" opens the first film; auto-advance does the rest.

**11. Subtitle files are listed as files of their own** · polish · small
- What goes wrong: `Clip 1 (2020).srt` gets its own row (with Download, star and +) next to the film. Station pictures next to station
  files are hidden in the same situation. Sibling gap.
- Where: station pictures are dropped by `pluteus/radio.py:51`; nothing does the same for `.srt`/`.vtt`.
- Fix: hide a subtitle file whose name matches a video in the same folder (the video page already offers it under CC).

## Shell: site bar and player strip

**12. The player strip's close button is 18 × 8 px** · blocker (phone) · small
- What goes wrong: × is plain text inside `.pl-vol-btn`, which has `line-height: 0`, so the button is 8 px tall. On a phone it
  can hardly be hit; the strip stays.
- Where: `pluteus/static/base.css:458`; button in `pluteus/web.py:766`.
- Fix: give it the close icon like its neighbours (or `line-height: 1`), and at least 40 × 40 px.

**13. A failed station only says so in the strip, cut off** · annoying (phone) · small
- What goes wrong: pressing play on a station that does not answer: the radio page shows nothing and its play button goes back to
  ▶. The only message is in the strip's title, cut on a phone to "NRK P1 – the …". The strip then stays with that text on every page.
- Where: `pluteus/static/shell.js:90` and `181`; the radio page hands over at `pluteus/static/radio.js:18`, so its own message
  (`radio.js:88`) is never shown.
- Fix: send the failure back to the page (`say()` in `radio.js`), and show it in the strip as a short line that clears itself.

**14. Two players at once** · annoying (phone) · medium · *mockup first*
- What goes wrong: after Play all, opening a track shows the strip (with its own play button) *and* the full player page for the
  same song. The strip names the file ("01 Song num…"), the page names the tag ("Song 1"). Pressing play on the page leaves the
  strip showing a paused song.
- Where: `pluteus/static/shell.js`, `pluteus/static/viewer-media.js`.
- Fix: when the page shows the song the strip holds, hide the strip (or let the page drive the strip's player). Use the tag title in
  the strip, as the page does.

**15. Bars take a lot of the screen in readers and players** · annoying (phone + TV) · medium · *mockup first*
- What goes wrong: in a reader on a phone: site bar 50 px + strip 43 px (when something played) + reader bar 48 px + reader footer
  about 40 px. On TV at 540 px tall: site bar 67 + reader bar 57 + footer 54, leaving about 360 px for a page or a film.
- Where: `pluteus/static/base.css:491` hides only the framed page's own bar; the shell bar stays.
- Fix: in a reader or player, fold the shell bar away (the reader bar's back arrow already leads home), or merge the two bars into one.

**16. Bar buttons are small and look different** · polish (phone) · small
- What goes wrong: Settings, dark/light, Admin and Sign out are 32-37 px (44 px is the usual minimum for a thumb). Admin has no
  button frame while the two beside it have one. The Settings nut sits alone on the left next to the name, the others on the right.
- Where: `pluteus/static/base.css:662`.
- Fix: 40-44 px minimum, the same frame on all four.

**17. The Settings nut stands apart from the other bar buttons** · polish · small · *question for you*
- What goes wrong: the nut sits on the left next to "Pluteus"; dark/light, Admin and Sign out sit on the right. On TV the D-pad
  has to cross the whole bar to get from one to the other.
- Where: the site bar in `pluteus/web.py:30` (`layout`).
- Fix: your call: keep it where it is, or move it into the right-hand group.

## Readers and players (EPUB, comic, PDF, image, music, video, radio, channel)

**18. The D-pad cannot reach any button** · blocker (TV) · large
- What goes wrong: on every reader and player page, `tvnav.js` stands aside (`own()`), and the pages only use Left/Right (turn the
  page, or skip 10 seconds). Up and Down do nothing; Enter does nothing because the focus is on the page body. Tested: EPUB reader,
  Up twice, focus stayed on the body; video page, Down twice and Enter, the video stayed paused at 0:00. So on a remote there is no
  way to press Play, Contents, Reading settings, Full screen, Next file, a station button, or the back arrow. The hardware Back key
  still works (TV Bro goes back in history).
- Where: `pluteus/static/tvnav.js:9` and `70`; the player pages set no focus (`pluteus/static/viewer-media.js`,
  `iptv.js`, `radio.js`).
- Fix: in TV mode, let Up/Down move the focus between the bar, the stage and the footer buttons (Left/Right keep turning pages or
  skipping while the focus is on the stage), and focus the Play button when a player page opens. Needs a test on the TV.
- Question: do you use TV Bro's mouse-cursor mode for these pages today? If so this is less urgent than it looks.

**19. Video and channel controls are off the screen on TV** · blocker (TV) · medium · *mockup first*
- What goes wrong: at 960 × 540 the video page shows the title, the facts line and the picture; the position bar, play button,
  Next file, CC and Full screen are below the fold. The channel page is the same (play, Live/Buffered, station buttons). With **18**
  they cannot be scrolled to either.
- Where: layout `pluteus/web.py:707-712` (video) and `796-806` (channel); picture height `pluteus/static/base.css:465`
  (`max-height: 62vh`).
- Fix: in TV mode, size the picture so the controls fit under it (about 45vh), or lay the controls over the picture as in full screen.

**23. Long messages are cut off inside an oval** · annoying (phone) · small
- What goes wrong: the toast has `border-radius: 999px`; a message of three lines turns into an oval and the text runs past its
  edge. Seen on the channel page ("The stream did not sta…") and the EPUB reader ("This book could not be opened…").
- Where: `pluteus/static/base.css:260`.
- Fix: `border-radius: 14px` and more padding (or 999px only while the text fits on one line).

**20. Reader bar not lined up in TV mode** · polish (TV) · small
- What goes wrong: with the 56 px TV buttons, the back arrow, the title and "About" sit at different heights (visible on the EPUB,
  video and channel pages).
- Where: `pluteus/static/base.css:196` and `720`.
- Fix: `display: inline-flex; align-items: center; justify-content: center` on `.pl-reader-btn`.

**21. Page-turn buttons are small on a phone** · polish (phone) · small
- What goes wrong: the previous/next buttons in the reader footers are 33 × 29 px. Edge taps and swipes exist, so it matters less.
- Where: `pluteus/static/base.css:196`.
- Fix: 44 px on narrow screens.

**22. The EPUB and comic readers have no Download button** · polish · small
- What goes wrong: the PDF, image, music, video, radio and channel pages have one in the bar; the two readers do not. Sibling gap.
- Where: `pluteus/web.py:475-537`; the shared button is `_download_link`, `pluteus/web.py:545`.
- Fix: add `_download_link` to both reader bars.

**24. The video position bar is short on a phone** · polish (phone) · small · *mockup first*
- What goes wrong: CC, Maximize and Full screen share the row with the position bar, which is left with 140 px.
- Where: `pluteus/web.py:690-693`.
- Fix: below 480 px, move the three buttons to the row under the big buttons.

**25. The video facts line shows audio facts** · polish · medium
- What goes wrong: "MP4 · 66 kbps · 44.1 kHz" on a film: no size (1280 × 720) or video codec.
- Where: `pluteus/mediainfo.py` (`facts`), used at `pluteus/web.py:652`.
- Fix: for video, show resolution and codec first.

**26. Two sets of Next / Previous file on the music and video pages** · polish · small
- What goes wrong: the big row has previous / next file, and the footer has ‹ › for the same thing.
- Where: `pluteus/web.py:720-723`.
- Fix: leave the footer arrows out on media pages (keep "1 / 4").

## Radio and TV grids, station and channel pages

**27. The search box is an unstyled browser box** · annoying (phone + TV) · small
- What goes wrong: 21 px tall on a phone, a plain grey box on TV, unlike every other field. Its 15 px text also makes iPhone
  Safari zoom in on tap.
- Where: `pluteus/web.py:393` (no class); field styles only cover `.pl-field`, `pluteus/static/base.css:155`.
- Fix: style `#stream-search` like the `.pl-field` inputs, at least 40 px tall, 16 px text.

**28. No next or previous channel** · annoying (TV) · medium
- What goes wrong: to change channel you go back to the grid and pick again. The music and video pages have next / previous
  file. Sibling gap.
- Where: `pluteus/web.py:772-811` (the footer has only "Show as text").
- Fix: previous / next station in the same folder: footer arrows, and on TV the remote's channel up / down keys.

**29. The station page shows a music note; the grid shows a radio** · polish · small
- Where: `pluteus/web.py:825` (`ico("audio")`) against `pluteus/web.py:376` (`pl-i-radio`).
- Fix: use the radio icon on the station page.

**30. The page bar shows the file name with ".m3u"** · polish · small
- What goes wrong: "NRK P1.m3u", "Discovery.m3u" in the bar, while the heading says "NRK P1".
- Where: `pluteus/web.py:809` and `838` pass the file name as the bar title.
- Fix: the station name (or the name without the extension).

**31. No favourites on stations** · polish · medium
- What goes wrong: the grids have no star / +, folders do. Sibling gap; just naming it.

## Settings

**32. TV mode does not say whether it is on** · polish · small
- What goes wrong: "Turn on for this device" and "Turn off" are both always there.
- Where: `pluteus/web.py:1060`.
- Fix: a line "TV mode is on in this browser" (set by `scheme.js`, which knows) and one button.

**33. Long choices are cut in the drop-downs** · polish (phone) · small
- What goes wrong: "Keep the sound playing in the player ba…".
- Where: `pluteus/web.py:1049`.
- Fix: shorter choices ("Keep playing" / "Stop"); the label above already explains.

## Admin pages

**34. Cancelling the sign-in prompt leaves a bare text page** · annoying · small
- What goes wrong: a monospace "Sign in with an administrator account." and nothing else: no way to try again, no link to the site.
- Where: `pluteus/adminapp.py:113`.
- Fix: a small themed page with "Sign in" (reloads, the prompt comes back) and a link to the site.

**35. Admin tabs are icons only on a phone** · polish (phone) · small
- What goes wrong: Status, Libraries, Security, Users and Debug show as five icons with no text; a phone has no tooltip.
- Where: `pluteus/static/base.css:654-661`.
- Fix: tiny labels under the icons, or a row of text tabs that scrolls sideways. (The admin page on a phone is secondary.)

## Checked and fine

- Login page, home page and folder lists at 375 px: no sideways scrolling anywhere, including the admin pages.
- TV mode: the focus ring is large and easy to see; focus moves between the site bar and the frame; the first item gets focus when
  a folder opens.
- EPUB and comic readers on a phone: edge taps and swipes, contents, reading settings.
- Admin Users and Libraries forms fit a phone; Remove asks first.

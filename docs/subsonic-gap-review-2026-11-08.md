# Subsonic gap review, 2026-11-08 (main at 04cf1e5, after the no-walk rewrite)

What music apps can and cannot do with pluteus today, measured against the premise: **the folder hierarchy is everything, and pluteus builds nothing.** No scan, no index, no walk, no background job, no database of the library. A call may only open the folders it needs, when an app asks. Nothing in the program was changed for this review. The answers go on the decision page (one card per proposal, Yes / No / Later); this file holds the same proposals and reasons.

**How it was checked.** Read `pluteus/subsonic.py` as it is after commit 04cf1e5 (every handler, the empty answers and the no-ops), `pluteus/config.py`, the README's music section and the tests. Each claim below was confirmed in the code (file and line given). The no-walk calls were also run read-only against the owner's real `D:/780 - Music/0 - Bands` folder over the network, with `artist_level = 2`: the artist list took 29 folder listings for 1586 artists (3.5 s from the laptop), one artist 6 listings, an alphabetical page 63 then 22, ten random albums 40, a search 40.

**Compared with** the Subsonic API 1.16.1 and the OpenSubsonic extensions, and with what Tempo, Symfonium, Ultrasonic and Android Auto ask for, from knowledge. The specification site was not opened for this review. Tempo, Ultrasonic and Android Auto were tested on real devices on 2026-11-04, before today's changes.

**How large the gap is.** About 80 calls in the API. pluteus answers 33 for real, 14 with an honest empty answer, and 5 by accepting and storing nothing; the rest get "not supported". By what a listener notices, the gap is now mostly what the premise rules out (newest albums, genres and years across the library, song-title search) plus three things that fit the premise and are missing: playlists, audiobook bookmarks, and albums nested deeper than one folder inside an artist folder.

**Left out on purpose:** everything that needs every folder opened or a list of the library kept (newest, byYear, byGenre across the library, frequent and highest from counts, song and album-name search, album counts in the artist list); online look-ups (similar songs, top songs, last.fm); user administration, chat, jukebox, shares and video, which a read-only music server has no use for.

**Effort:** small = under 2 minutes of work, medium = up to 10, large = more than 10.

**Overall.** With `artist_level` the artist and album views now follow the real folders, and every call costs a handful of listings. The best next steps keep to the premise: let the admin page set the level, show box sets two levels deep, fill fields from what the folder names already say (year), read `album.md` and folder pictures as album and artist information, and add playlists as `.m3u` files in the user's upload folder, starting with the gonic export. Transcoding is the one large item that matters in the car, and it is a separate decision because it adds ffmpeg.

---

## 1. Gaps in what exists

### 1. The artist level can only be set in config.toml
- **What:** `artist_level` is read from the `[[roots]]` entry (`pluteus/config.py:40`), but the admin page's Subsonic box only has the ticks. You said Subsonic is set on the admin page.
- **Why:** without it the setting is hidden, and a wrong level makes the artist list show letters or albums.
- **Fix:** a "Artists are … folders down" choice (1–5) beside each library's tick in the **Music apps (Subsonic)** box, saved with the same careful text edit as the ticks. A text sketch comes with this review; it is built after you pick.
- **Effort:** medium. **Recommendation:** Yes.

### 2. Box sets two levels deep inside an artist folder are not shown
- **What:** an album is a folder in the artist folder with songs directly or in the folders right inside it (`pluteus/subsonic.py:446`). `Tom Lehrer/Tom Lehrer - The remains of Tom Lehrer/Disc 1 - …/[1953] - Songs by Tom Lehrer/` has its songs one level further down, so the box does not appear under Tom Lehrer at all (checked on the real folder). Folder browsing still reaches it.
- **Why:** a listener looking for the box in the artist view finds nothing.
- **Fix:** when a folder in the artist folder holds no songs in its first two levels, look one level further (bounded: at most 20 folders per level), and list each album found there as its own album of that artist. Only when that artist is opened.
- **Effort:** small. **Recommendation:** Yes.

### 3. One artist level per library, but the music folder mixes depths
- **What:** the level is per library (`pluteus/config.py:40`). In `D:/780 - Music`, `0 - Bands` is Letter/Artist, `1.5 - Hip-hop` is Country/Artist, `6.2 - Compilations` holds albums directly.
- **Why:** pointing music apps at the whole music folder cannot give one right level.
- **Fix:** no code: add one library per genre folder you want in the car (`bands` = `/library/music/0 - Bands` at level 2, `hiphop` = `/library/music/1.5 - Hip-hop` at level 2, `compilations` = `/library/music/6.2 - Compilations` at level 1, where each album shows as its own "artist"), and tick only those. The folders are already mounted, so `docker-compose.yml` does not change. The cost: stars and saved positions are kept per library, so a heart given in the web pages under `music` does not show under `bands`.
- **Effort:** small. **Recommendation:** Yes (as a setup step; no code).

### 4. The artist list says 0 albums for every artist
- **What:** `albumCount` is 0 in `getArtists` (`pluteus/subsonic.py:540`), because counting would open every artist folder. The real number is given when the artist is opened.
- **Why:** some apps show the count under each artist, and a strict app might hide artists with 0. Not yet seen in an app.
- **Fix:** try Tempo, Ultrasonic and Android Auto after the next rebuild; change nothing unless one of them hides or mislabels artists. If one does, leave the field out instead of 0.
- **Effort:** small. **Recommendation:** Yes (test first).

### 5. Audiobooks do not resume in apps
- **What:** `getBookmarks` answers empty and `createBookmark` is accepted and dropped (`pluteus/subsonic.py:175`, `:183`), although pluteus keeps a saved position for every audio file played in the web player, and bookmarks in m4b files.
- **Why:** an audiobook started in the browser starts from the beginning in the car. Symfonium and Tempo use bookmarks for exactly this.
- **Fix:** `getBookmarks` answers from the user's saved positions of audiobooks (m4b, and long files), and `createBookmark` / `deleteBookmark` write to the same saved positions. The user's own data, as the play queue already is; nothing about the library is kept.
- **Effort:** medium. **Recommendation:** Yes.

### 6. "formPost" works but is not announced
- **What:** pluteus reads the login and the parameters from a POST body (`pluteus/subsonic.py:189`), but `getOpenSubsonicExtensions` lists only API keys and lyrics (`pluteus/subsonic.py:570`).
- **Why:** apps that know the extension send long requests (a big play queue) as POST only when the server says it can.
- **Fix:** add `{"name": "formPost", "versions": [1]}` to the list.
- **Effort:** small. **Recommendation:** Yes.

## 2. Easy gets from what the folders already say

### 7. The year from the album folder's name
- **What:** album items have no year unless a song's tag has one (`pluteus/subsonic.py:402`). Your album folders start with it: `[2016] - Unseen`.
- **Why:** apps show and sort albums by year on the artist page.
- **Fix:** when an album folder name starts with `[YYYY]` (or ends with `(YYYY)`), put that year on the album item and on its songs without a year tag. Read from the name already listed; nothing extra is opened.
- **Effort:** small. **Recommendation:** Yes.

### 8. album.md as the album's information
- **What:** `getAlbumInfo` and `getAlbumInfo2` answer empty (`pluteus/subsonic.py:175`), while the artist biography already comes from `artist.md` (`pluteus/subsonic.py:637`).
- **Why:** the same notes the Folder Redactor writes would show in the app's album view, as the biography does for artists. The siblings should behave alike.
- **Fix:** `getAlbumInfo2` answers `notes` from the album folder's `album.md` (else `album.nfo`), plain text as for the biography. One file read, when the app opens the album.
- **Effort:** small. **Recommendation:** Yes.

### 9. The artist picture from the artist folder
- **What:** `getArtistInfo2` gives only the biography (`pluteus/subsonic.py:669`). Covers already come from `folder.jpg` / `cover.jpg`.
- **Why:** apps show a large artist picture at the top of the artist page; today it is empty or the first album's cover.
- **Fix:** when the artist folder holds `artist.jpg`, `folder.jpg` or `cover.jpg`, give its cover-art address as `smallImageUrl`, `mediumImageUrl` and `largeImageUrl` (sizes 160, 300, 600 through the existing scaling).
- **Effort:** small. **Recommendation:** Yes.

## 3. Larger tasks that fit the premise

### 10. Playlists as .m3u files in the user's upload folder, starting with the gonic export
- **What:** `getPlaylists` answers empty and the other playlist calls are not supported (`pluteus/subsonic.py:175`, `:219`). gonic holds 6 playlists for you (120 songs) as `.m3u` files in `D:\temp\_docker\gonic\playlists\2`, pointing into the same music folder.
- **Why:** playlists are the main thing an app user misses after artists and albums, and Android Auto shows them on its first screen. A playlist file in a folder is the folder hierarchy at work: nothing is built.
- **Fix:** `getPlaylists` lists the `.m3u` files in the user's `Playlists` folder in the upload folder, and `getPlaylist` reads one. `createPlaylist`, `updatePlaylist` and `deletePlaylist` write or remove those files, only in the upload folder, never in a library. A one-off export copies gonic's 6 lists there with the paths turned into `music/...`. The web player learns to tell a song playlist from a radio station list. Open question from before: paths as library name plus path (my pick) or as full server paths.
- **Effort:** large. **Recommendation:** Yes.

### 11. Genres from the top folders
- **What:** `getGenres` answers empty and `byGenre` is an empty list (`pluteus/subsonic.py:175`, `:50`). In your music folder the top folders already are the genres (`1.1 - Blues`, `1.5 - Hip-hop`, `9.78 - Norsk Musikk`).
- **Why:** browsing by genre is the second way into music in most apps, and the car's genre screen is empty.
- **Fix:** for a library marked "top folders are genres", `getGenres` lists its top folders (one listing), and `byGenre` / `getSongsByGenre` page through that folder artist by artist, as the alphabetical lists do. Tags are not read for it.
- **Effort:** medium. **Recommendation:** Later (useful only if the whole music folder is given to the apps; with one library per genre folder, card 3, the music folders already do this job).

### 12. Transcoding for mobile data
- **What:** `stream` sends the file as it is and ignores `maxBitRate` and `format` (`pluteus/subsonic.py:1050`).
- **Why:** a FLAC album over mobile data in the car is large; every other Subsonic server can send a smaller mp3 or opus.
- **Fix:** only when ffmpeg is installed: convert on the fly when the app asks for a lower bit rate or another format, with `timeOffset` for seeking instead of Range. Nothing is stored. It adds an outside program and CPU use on the server, which is your call.
- **Effort:** large. **Recommendation:** Later (decide after the car test shows whether data use is a problem).

### 13. Ratings as the user's own data
- **What:** `setRating` is accepted and dropped (`pluteus/subsonic.py:183`), and `highest` is an empty list (`pluteus/subsonic.py:50`).
- **Why:** some listeners rate songs in the app and expect the rating back.
- **Fix:** keep ratings per user beside the favourites (path and number), and answer `highest` from those rows. The user's data, not the library's; no folder is opened to answer it.
- **Effort:** medium. **Recommendation:** Later (nobody has asked for it).

## Considered and not proposed

- **Newest albums, byYear and byGenre across a whole library, frequent and recently-added lists:** each needs every folder opened or a list kept. Ruled out by the premise.
- **Song-title and album-name search across the library:** same reason. A search matches artist folder names and opens only those artists.
- **Play counts (`scrobble`):** possible as the user's own data, but that is logging what someone listens to; by the opt-in rule it would need a switch, default off, and a delete button. Not proposed until someone asks.
- **Similar songs, top songs, artist info from last.fm or MusicBrainz:** online look-ups; pluteus reads only what is in the files.
- **Podcasts:** reading a podcast folder as channels would fit the premise, but fetching new episodes is a background job. Not proposed.
- **Internet radio editing from the app, shares, jukebox, chat, user administration, videos:** no use on a read-only music server for one household, or already covered by the files and the web pages.
- **An index or a scan "only when asked":** still a list of the library kept by pluteus. Ruled out.

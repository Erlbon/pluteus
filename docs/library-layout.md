# Library layout: which note goes where

*Proposed. The comics layout is the owner's own; the music and book layouts below are to be confirmed before anything depends on them.*

pluteus never needs a particular layout, it shows whatever the folders are. But with a few files per folder, **the structure is what carries the
information**, and a note sits at the level it describes. The recognised note names are in [notes-format.md](notes-format.md).

## Music

```
780 - Music/
  Artist/
    artist.md              the band: who, when, where (the player's "About" link and Subsonic's biography)
    Album (Year)/
      album.md             the album: recording, line-up, remarks
      01 Song.mp3
```

`artist.nfo` and `album.nfo` (Kodi, Plex, Lidarr, MusicBrainz Picard write them) are read when there is no `.md` note.

## Books (Genre, Sub-genre, Author)

```
Books/
  Science Fiction/
    about.md               a short note on the genre
    Space Opera/
      about.md
      Author, Name/
        author.md          the author
        Book Title.epub
```

`metadata.opf` (Calibre) is read for a book folder's description when there is no `.md` note.

## Comics

```
Comics/
  3 - Marvel (1961-2012)/             a lineage / publisher folder
    about.md                          the publisher or lineage
    Spider-Man (1963-)/
      series.md                       the series
      Amazing 001.cbz
  _Crossovers/                        an underscore folder groups things that belong to no single series
    about.md
```

The underscore at the start of a folder name means *a grouping, not a series or a publisher*; give it an `about.md`.

## The checker (outside pluteus)

The structure checker that already reads the comic library (read-only, with a yes/no review page and renames only on a yes, with undo) is the
place to report where this layout is broken and where a folder at a note level has no note yet. That is a separate tool, not part of pluteus.

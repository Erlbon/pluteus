# The note format

A *note* is a short Markdown file that sits next to what it describes. pluteus shows it on the folder page, links to it from the music
player, and hands the biography to Subsonic apps. The format is deliberately plain, so any editor, and any other tool, can write it without
a line of pluteus code.

## The files

| File name (any letter case) | Where it goes | Shown |
|---|---|---|
| `artist.md`, `author.md`, `series.md`, `album.md`, `about.md` | in the folder it describes | at the top of the folder page; the first one found in this order wins |
| `README.md`, `index.md` | in any folder | at the bottom, like on GitHub |
| `artist.nfo`, `album.nfo`, `metadata.opf` | in the folder | only when the folder has no note of the names above: the biography, review or description inside is shown as plain paragraphs |

When a folder has no note of the names above and no `.nfo` or `.opf`, but holds exactly one `.epub` or `.cbz`, the description written inside that book (the EPUB's `dc:description`, the CBZ's `ComicInfo.xml` Summary) is shown instead.

Other `.md`, `.markdown` and `.txt` files are ordinary documents: they open as a page, but they are not "the note of a folder".

A note a user wrote through the browser is kept in that user's own upload folder (`Notes/<library>/<folder path>/artist.md`) and wins over the
library's note, for that user only. A note written with the *notes* right on a library set to `notes = "edit"` is written into the library
itself and is seen by everybody who sees the folder.

## The text

Ordinary Markdown: headings, lists, task lists, tables, quotes, code, links and images. Safe by construction: raw HTML is shown as text,
links may only go to `http`, `https`, `mailto` or a file in the same library, and images only from the same library. A file over 256 KB is not
rendered.

One addition, optional:

- **Front matter**, a block at the very top between two lines of `---`:

  ```markdown
  ---
  title: Kaizers Orchestra
  years: 2000-
  genres: [rock, norwegian]
  tags:
    - band
    - stavanger
  audience: kids          # reserved: who the folder is for (kids, adult)
  status: draft           # reserved: tools that create notes write `draft` until a person has read them
  links: [https://example.org/]
  ---
  ```

  Plain `key: value` lines, `key: [a, b]` lists and `- item` lists, nothing nested. pluteus reads `title` (the page title) and hides the block;
  the other keys are kept for other tools (Obsidian, Jekyll and friends read the same block). A block that is not clean is left alone and shown
  as it is.

Notes hold text, not navigation: `[[double brackets]]` are not links and are shown as they are. The folders are the structure; a note does not need to point at them.

In a full page (not in a folder's note box) headings get an anchor (`#heading`, a link you can copy) and a document with four or more headings
gets a contents list.

## Discographies: the note names the releases, the folders say which ones the library has

A band's note may list its releases as plain lines, under a heading such as **Discography**, **Studio albums**, **Live albums**, **Compilations**, **EPs**, **Singles**,
**Remixes**, **Soundtracks** or **Releases** (a trailing "(all)" or "(selected)" in the heading is ignored):

```markdown
### Studio albums
- Speak & Spell (1981)
- A Broken Frame (1982)
- Violator (1990)
```

When the page is shown, pluteus compares each title with the folder names *next to the note* (one listing, nothing else is searched) and adds a badge: **in library** or
**missing**. Titles and folder names match ignoring case, a leading or trailing year (`[1981] - Speak And Spell`, `Speak & Spell (1981)`), brackets, punctuation, and `&` against
`and`. The badge is computed live, so it stays right as albums are added or removed: **do not write "(in this library)" or a separate "in this library" list into the note**,
it would only repeat what pluteus shows and go out of date. No links are needed or wanted in a note: the notes are text, the folders are the structure.

## For tools that write notes

- Write UTF-8 with a `---` front matter block and put `status: draft` in it.
- Never overwrite an existing note: write only where there is none.
- List releases as plain `- Title (Year)` lines (see Discographies above): no `[[links]]`, no "in this library" annotations.
- Use the file name for the level (see [library-layout.md](library-layout.md)).
- Plain text under about 64 KB is the sweet spot; the editor in pluteus refuses more than that.

## What pluteus does not do with notes

No search across notes, no backlinks, no tag pages: that would need an index, and pluteus has none on purpose. No online look-ups either: the
filling-in of notes is for other tools (the Redactor apps), and `status: draft` tells a reader that nobody has checked them yet.

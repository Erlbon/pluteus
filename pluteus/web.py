"""HTML pages. Every page shares one layout that links base.css, the default theme and the user's theme."""
from __future__ import annotations

import time
from dataclasses import dataclass
from html import escape

from . import __version__, kinds, mediainfo, radio
from .icons import COLOR_SPRITE, OUTLINE_SPRITE, ico
from .themes import DEFAULT
from .urls import Urls


@dataclass
class Ctx:
    urls: Urls
    user: str = ""
    theme: str = DEFAULT
    readers: bool = False        # show "Read" links (the in-browser readers)
    icons: str = "gold"          # the file-type icon style: gold (outlines), aurora (outlines, a colour each) or color (filled tiles)
    admin_link: str = ""         # address of the admin page, for an administrator on the local network; else empty
    extras: bool = True          # the speed and sleep controls in the audio/video player
    tv_sound: bool = True        # the sound of a live TV channel goes on in the shell's player when its page is left
    view_scope: str = "all"      # the List / Covers choice on a folder page holds for "all" folders or is kept per "folder"
    queue_subfolders: bool = True    # Play all and Shuffle include the folders below the one you are in
    view: str = "list"           # how a folder is shown: "list" (rows) or "grid" (covers)
    thumbs: bool = False         # thumbnails are switched on (thumbnails = true): the cover view and its switches exist


def layout(title: str, body: str, ctx: Ctx, *, head: str = "", scripts: tuple = (), bar: bool = True,
           body_class: str = "", module_scripts: tuple = (), shell: bool = True) -> str:
    u = ctx.urls
    css = ["<link rel='stylesheet' href='%s'>" % escape(u.static("base.css")),
           "<link rel='stylesheet' href='%s'>" % escape(u.theme(DEFAULT, "theme.css"))]
    if ctx.theme != DEFAULT:
        css.append("<link rel='stylesheet' href='%s'>" % escape(u.theme(ctx.theme, "theme.css")))
    chrome = ""
    # the shell's own bar: its page links open in the frame below, so the shell (and the music in it) is never reloaded
    into_frame = "" if shell else " target='shell-frame'"
    if bar:
        who = nav = ""
        if ctx.user:
            # Laid out like the admin page's bar: brand, version and the page links on the left; contrast button, the link
            # to the admin page (an administrator on the local network only) and Sign out on the right.
            nav = "<nav class='pl-admin-nav'><a class='pl-admin-tab pl-reader-btn' href='%s'%s title='Settings' aria-label='Settings'><span class='pl-icon pl-i-settings'></span></a></nav>" % (escape(u.settings()), into_frame)
            # a new tab: the admin page is another site (its own port), so opening it here would stop a playing song
            # hidden until adminlink.js has found the admin page reachable from this browser: from the public address it is not,
            # and a link that opens an empty page looks silly
            admin = ("<a class='pl-bar-link' href='%s' target='_blank' rel='noopener' hidden data-admin title='Admin' aria-label='Admin'>"
                     "<span class='pl-icon pl-i-admin'></span><span class='pl-lbl'>Admin</span></a>" % escape(ctx.admin_link)) if ctx.admin_link else ""
            if admin:
                scripts = scripts + (u.static("adminlink.js"),)
            who = (admin + "<form method='post' action='%s'><button class='pl-btn pl-bar-link' type='submit' title='Sign out (%s)' aria-label='Sign out (%s)'>"
                   "<span class='pl-icon pl-i-logout'></span><span class='pl-lbl'>Sign out<span class='pl-who'> (%s)</span></span></button></form>"
                   % (escape(u.logout()), escape(ctx.user), escape(ctx.user), escape(ctx.user)))
        chrome = ("<header class='pl-bar'><a class='pl-brand' href='%s'%s>Pluteus</a> <span class='pl-muted pl-ver'>%s</span>%s"
                  "<span class='pl-spacer'></span>%s%s</header>"
                  % (escape(u.home()), into_frame, escape(__version__), nav, scheme_button(), who))
    js = "".join("<script src='%s' defer></script>" % escape(s) for s in scripts)
    js += "".join("<script type='module' src='%s'></script>" % escape(s) for s in module_scripts)
    # scheme.js is NOT deferred: it must run before the first paint so a saved dark/light choice never flashes.
    # data-shell: a signed-in page opened on its own moves into the shell (scheme.js), where the player outlives page changes
    to_shell = " data-shell='%s'" % escape(u.shell()) if (shell and ctx.user) else ""
    return ("<!doctype html><html lang='en' data-icons='%s'%s><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>%s</title>%s%s<script src='%s'></script>%s%s</head>"
            "<body class='%s'>%s%s%s</body></html>"
            % (escape(ctx.icons), to_shell, escape(title), icon_links(u), "".join(css), escape(u.static("scheme.js")), head, js,
               escape(body_class), COLOR_SPRITE if ctx.icons == "color" else OUTLINE_SPRITE, chrome, body))


def icon_links(u: Urls) -> str:
    """Browser tab icon, home-screen icon and web manifest (Android 'add to home screen')."""
    return ("<link rel='icon' href='%s' sizes='any'><link rel='icon' type='image/png' sizes='32x32' href='%s'>"
            "<link rel='apple-touch-icon' href='%s'><link rel='manifest' href='%s'><meta name='theme-color' content='#14171f'>"
            % (escape(u.static("icon/favicon.ico")), escape(u.static("icon/favicon-32.png")),
               escape(u.static("icon/apple-touch-icon.png")), escape(u.manifest())))


def scheme_button() -> str:
    return ("<button class='pl-btn pl-scheme' type='button' title='Toggle dark / light' "
            "aria-label='Toggle dark / light'><span class='pl-icon pl-i-contrast'></span></button>")


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%d %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024


def login_page(ctx: Ctx, next_url: str = "", error: str = "", username: str = "") -> str:
    err = "<p class='pl-error' role='alert'>%s</p>" % escape(error) if error else ""
    body = ("<main class='pl-main'><div class='pl-login-wrap'><div class='pl-brand pl-wordmark'>PLUTEUS</div>"
            "<form class='pl-card pl-login' method='post' action='%s'>"
            "<div class='pl-login-tools'>%s</div><div class='pl-logo'></div>"
            "<h1>Sign in</h1>%s"
            "<label class='pl-field'><span>User name</span>"
            "<input type='text' name='username' value='%s' autocomplete='username' autocapitalize='off' autofocus required></label>"
            "<label class='pl-field'><span>Password</span>"
            "<input type='password' name='password' autocomplete='current-password' required></label>"
            "<input type='hidden' name='next' value='%s'>"
            "<button class='pl-btn pl-btn-primary' type='submit'>Sign in</button></form>"
            "<p class='pl-version pl-muted'>pluteus %s</p></div></main>"
            % (escape(ctx.urls.login()), scheme_button(), err, escape(username), escape(next_url), escape(__version__)))
    return layout("Sign in", body, ctx, bar=False, body_class="pl-login-page")


KIND_ICON_NAMES = {"epub": "epub", "comic": "comic", "pdf": "pdf", "image": "image", "audio": "audio", "video": "video",
                   "markdown": "text", "text": "text", "table": "table", "archive": "archive"}
ROOT_ICON, UPLOADS_ICON = ico("folder"), ico("upload")
ICON_STYLES = (("gold", "Gold outlines"), ("aurora", "Outlines, a colour for each type"), ("color", "Colour tiles"))
RECENT_TITLE = "Pick up where you left off"       # books, comics, PDFs, audiobooks and videos all appear in this box


def kind_icon(kind: str) -> str:
    """A small picture for a kind of file (book, comic, PDF, ...); a plain page for anything else."""
    return ico(KIND_ICON_NAMES.get(kind, "file"))


def _shelf_buttons(urls, root: str, parts: tuple, on: list) -> str:
    """The star (favourite) and + (read next) buttons of one entry; shelf.js toggles them."""
    def one(shelf: str, cls: str, label: str) -> str:
        return ("<button type='button' class='pl-shelf-btn %s' data-url='%s' aria-pressed='%s' title='%s' aria-label='%s'></button>"
                % (cls, escape(urls.shelf(shelf, root, parts)), "true" if shelf in on else "false", label, label))
    return "<span class='pl-shelf'>%s%s</span>" % (one("favourite", "pl-star", "Favourite"), one("next", "pl-next-btn", "Read next"))


def _shelf_box(title: str, items: list, movable: bool) -> str:
    if not items:
        return ""
    rows = "".join(
        "<li class='pl-row pl-recent-row'><a class='pl-recent-link' href='%s'><span class='pl-kind'>%s</span>"
        "<span class='pl-recent-text'><span class='pl-title'>%s</span><span class='pl-where pl-muted'>%s</span></span></a>%s"
        "<button type='button' class='pl-shelf-remove' data-url='%s' title='Remove from this list' aria-label='Remove %s from this list'>&times;</button></li>"
        % (escape(i["href"]), ROOT_ICON if i["kind"] == "folder" else kind_icon(i["kind"]), escape(i["label"]), escape(i["where"]),
           ("<button type='button' class='pl-shelf-move' data-url='%s?move=up' title='Move up' aria-label='Move up'>&uarr;</button>"
            "<button type='button' class='pl-shelf-move' data-url='%s?move=down' title='Move down' aria-label='Move down'>&darr;</button>"
            % (escape(i["api"]), escape(i["api"]))) if movable else "", escape(i["api"]), escape(i["label"]))
        for i in items)
    return ("<section class='pl-card pl-home-box' aria-label='%s'><h2>%s</h2><ul class='pl-list pl-recent pl-shelf-list'>%s</ul></section>"
            % (escape(title), escape(title), rows))


def roots_page(roots: list, ctx: Ctx, recent: list | None = None, read_next: list | None = None,
               favourites: list | None = None, stations: list | None = None) -> str:
    items = "".join("<li class='pl-row'><a class='pl-root-link' href='%s'><span class='pl-kind'>%s</span>%s</a></li>"
                    % (escape(ctx.urls.browse(r.name)), UPLOADS_ICON if r.name == "uploads" else ROOT_ICON, escape(r.name))
                    for r in roots)
    # the user's saved radio stations and TV channels (folders inside their upload folder) get a card of their own, above Browse
    live = ""
    if stations:
        live = ("<section class='pl-card pl-home-box pl-live-box' aria-label='Live'><h2>Live</h2><ul class='pl-list pl-roots'>%s</ul></section>"
                % "".join("<li class='pl-row'><a class='pl-root-link' href='%s'><span class='pl-kind pl-live-kind'>"
                          "<span class='pl-icon pl-i-%s'></span></span>%s</a></li>"
                          % (escape(href), "tv" if kind == "video" else "radio", escape(label)) for label, href, kind in stations))
    # Two boxes (cards): "Continue reading" (only when there is something) and "Browse", then the small print below them.
    # (the lists carry .pl-roots / .pl-recent and the boxes .pl-home-box, so a theme can restyle them)
    cont = ""
    if recent:
        rows = "".join(
            "<li class='pl-row pl-recent-row'><a class='pl-recent-link' href='%s'><span class='pl-kind'>%s</span>"
            "<span class='pl-recent-text'><span class='pl-title'>%s</span><span class='pl-where pl-muted'>%s</span></span></a>"
            "<span class='pl-badge'>%d%%</span>"
            "<form class='pl-dismiss-form' method='post' action='%s'><button class='pl-dismiss' type='submit' "
            "title='Remove from this list' aria-label='Remove %s from this list'>&times;</button></form></li>"
            % (escape(r["href"]), kind_icon(r["kind"]), escape(r["label"]), escape(r["where"]), round(r["percent"] * 100),
               escape(r["dismiss"]), escape(r["label"]))
            for r in recent)
        # not "Continue reading": the list also holds audiobooks, PDFs and videos
        cont = ("<section class='pl-card pl-home-box' aria-label='%s'><h2>%s</h2>"
                "<ul class='pl-list pl-recent'>%s</ul></section>" % (RECENT_TITLE, RECENT_TITLE, rows))
    cont += _shelf_box("Read next", read_next or [], True) + _shelf_box("Favourites", favourites or [], False)
    body = ("<main class='pl-main'><h1>File Library</h1>%s%s"
            "<section class='pl-card pl-home-box' aria-label='Browse'><h2>Browse</h2>"
            "<ul class='pl-list pl-roots'>%s</ul></section>"
            "<p class='pl-muted pl-foot'><a href='%s'>OPDS feed</a> for reader apps &middot; pluteus %s</p></main>"
            % (live, cont, items, escape(ctx.urls.opds()), escape(__version__)))
    return layout("File Library", body, ctx, scripts=(ctx.urls.static("shelf.js"),) if (read_next or favourites) else ())


def _book_label(entry, meta: dict) -> str:
    """The file name always leads (the owner's names are the browsing order); the book's own metadata (title, author, series) is added under it
    in small type, and left out when the title says nothing the name does not."""
    name = escape(entry.name)
    title = (meta.get("title") or "").strip()
    stem = entry.name.rsplit(".", 1)[0] if "." in entry.name else entry.name
    if not title or title.casefold() == stem.casefold():
        return name
    by = " &ndash; " + escape(", ".join(meta["authors"])) if meta.get("authors") else ""
    series = (" (%s%s)" % (escape(meta["series"]), " #" + escape(meta["series_index"]) if meta.get("series_index") else "")
              if meta.get("series") else "")
    return "%s<span class='pl-book-meta pl-muted'>%s%s%s</span>" % (name, escape(title), by, series)


UPLOAD_MESSAGES = {
    "toobig": "is larger than the size limit", "quota": "does not fit in your space", "disk": "the disk is full",
    "name": "has no usable name", "exists": "that name is already taken", "toomany": "too many files at once",
    "write": "could not be written", "notempty": "the folder is not empty", "missing": "it no longer exists",
    "format": "the upload was not understood",
}
OK_MESSAGES = {"folder": "Folder created.", "renamed": "Renamed.", "deleted": "Deleted.", "note": "Note saved.", "noteremoved": "Note removed."}


def flash_text(query: dict) -> str:
    """The one-line result shown after an upload or file operation. Only fixed sentences and a count are ever shown:
    nothing from the query string is echoed."""
    parts = []
    try:
        saved = int(query.get("saved", 0))
    except ValueError:
        saved = 0
    if saved > 0:
        parts.append("Uploaded %d file%s." % (min(saved, 1000), "" if saved == 1 else "s"))
    if query.get("ok") in OK_MESSAGES:
        parts.append(OK_MESSAGES[query["ok"]])
    if query.get("bad") in UPLOAD_MESSAGES:
        parts.append("Not done: %s." % UPLOAD_MESSAGES[query["bad"]])
    return " ".join(parts)


def _row_actions(urls: Urls, root: str, parts: tuple, name: str) -> str:
    return ("<span class='pl-actions'>"
            "<form method='post' action='%s' data-rename='%s'><input type='hidden' name='name' value=''>"
            "<button class='pl-btn pl-btn-small' type='submit'>Rename</button></form>"
            "<form method='post' action='%s' data-confirm='Delete %s?'>"
            "<button class='pl-btn pl-btn-small' type='submit'>Delete</button></form></span>"
            % (escape(urls.files("rename", root, parts)), escape(name),
               escape(urls.files("delete", root, parts)), escape(name)))


def _upload_forms(urls: Urls, root: str, parts: tuple) -> str:
    return ("<form class='pl-upload' method='post' enctype='multipart/form-data' action='%s'>"
            "<input type='file' name='files' multiple required>"
            "<button class='pl-btn pl-btn-primary' type='submit'>Upload</button>"
            "<div class='pl-upload-status' hidden><div class='pl-progress'><i></i></div><span></span></div></form>"
            "<form class='pl-newfolder' method='post' action='%s'>"
            "<input type='text' name='name' placeholder='New folder' maxlength='100' required>"
            "<button class='pl-btn' type='submit'>Create folder</button></form>"
            % (escape(urls.upload(root, parts)), escape(urls.files("mkdir", root, parts))))


SORT_LABELS = (("name", "Name"), ("type", "Type"), ("size", "Size"), ("date", "Modified"))


def _query(sort: str, desc: bool, page: int = 1, view: str = "") -> str:
    """?sort=..&dir=..&page=.. with only the non-default parts (the default listing keeps its plain URL)."""
    pairs = []
    if view:
        pairs.append("view=" + view)
    if sort != "name" or desc:
        pairs += ["sort=" + sort] + (["dir=desc"] if desc else [])
    if page > 1:
        pairs.append("page=%d" % page)
    return "?" + "&".join(pairs) if pairs else ""


def _sort_bar(here: str, sort: str, desc: bool, view: str = "") -> str:
    items = []
    for key, label in SORT_LABELS:
        if key == sort:                                       # the current column: its link flips the direction
            arrow = " &#9660;" if desc else " &#9650;"
            href, cls = here + _query(key, not desc, 1, view), " class='pl-sort-on'"
        else:                                                 # a new column: newest first for dates, else ascending
            arrow, cls, href = "", "", here + _query(key, key == "date", 1, view)
        items.append("<a%s href='%s'>%s%s</a>" % (cls, escape(href), label, arrow))
    return "<nav class='pl-sortbar' aria-label='Sort this folder'><span class='pl-muted'>Sort:</span> %s</nav>" % " ".join(items)


def listing_page(root: str, parts: tuple, entries: list, page: int, pages: int, total: int, ctx: Ctx,
                 metas: dict | None = None, progress: dict | None = None,
                 sort: str = "name", desc: bool = False, writable: bool = False, flash: str = "", cover_url: str = "",
                 view_asked: str = "", shelves: dict | None = None, note_lazy: str = "", note_links: dict | None = None) -> str:
    metas = metas or {}
    progress = progress or {}
    shelves = shelves or {}
    urls = ctx.urls
    crumbs = ["<a href='%s'>File Library</a>" % escape(urls.home()),
              "<a href='%s'>%s</a>" % (escape(urls.browse(root)), escape(root))]
    for i, seg in enumerate(parts):
        crumbs.append("<a href='%s'>%s</a>" % (escape(urls.browse(root, parts[:i + 1])), escape(seg)))
    # The first row goes one level up, with the same big button as the download buttons below it (a thumb on a phone finds it
    # in the same place without reaching for the breadcrumbs).
    if parts:
        up_url, up_name = urls.browse(root, parts[:-1]), (parts[-2] if len(parts) > 1 else root)
    else:
        up_url, up_name = urls.home(), "File Library"
    rows = ["<li class='pl-row pl-file pl-up'>"
            "<a class='pl-dl' href='%s' title='Up one folder' aria-label='Up one folder, to %s'><span class='pl-icon pl-i-back'></span></a>"
            "<a class='pl-title' href='%s'><span class='pl-title-text'>Up: %s</span></a></li>"
            % (escape(up_url), escape(up_name), escape(up_url), escape(up_name))]
    for e in entries:
        if e.is_dir:
            # The whole row is the link: a big target for a thumb.
            rows.append("<li class='pl-row pl-folder'><a class='pl-title' href='%s'><span class='pl-kind'>%s</span>%s</a>%s%s</li>"
                        % (escape(urls.browse(root, e.parts)), ROOT_ICON, escape(e.display),
                           _shelf_buttons(urls, root, e.parts, shelves.get("/".join(e.parts), [])),
                           _row_actions(urls, root, e.parts, e.name) if writable else ""))
            continue
        label = _book_label(e, metas.get(e.path, {}))
        pct = progress.get("/".join(e.parts))
        badge = "<span class='pl-badge'>%d%%</span> &middot; " % round(pct * 100) if pct else ""
        # Tapping the title opens the file in its reader or viewer (the default); download is its own big button on the
        # left, where a thumb finds it. Without readers (a plain file server) the title downloads.
        if not ctx.readers:
            open_url = urls.download(root, e.parts)
        elif kinds.kind_of(e.name) in ("epub", "comic"):
            open_url = urls.read(root, e.parts)
        else:
            open_url = urls.view(root, e.parts)
        rows.append("<li class='pl-row pl-file'>"
                    "<a class='pl-dl' href='%s' title='Download' aria-label='Download %s'>"
                    "<span class='pl-icon pl-i-download'></span></a>"
                    "<a class='pl-title' href='%s'><span class='pl-kind'>%s</span><span class='pl-title-text'>%s</span></a>"
                    "<span class='pl-meta pl-muted'>%s%s &middot; %s</span>%s%s</li>"
                    % (escape(urls.download(root, e.parts)), escape(e.name), escape(open_url),
                       kind_icon(kinds.kind_of(e.name)), label, badge,
                       _size(e.size), time.strftime("%Y-%m-%d", time.localtime(e.mtime)),
                       _shelf_buttons(urls, root, e.parts, shelves.get("/".join(e.parts), [])),
                       _row_actions(urls, root, e.parts, e.name) if writable else ""))
    here = urls.browse(root, parts)
    tiles = _tiles(ctx, root, entries, metas, progress, shelves) if ctx.view == "grid" else []
    pager = ""
    if pages > 1:
        prev = ("<a href='%s'>&larr; Previous</a>" % escape(here + _query(sort, desc, page - 1, view_asked))) if page > 1 else ""
        nxt = ("<a href='%s'>Next &rarr;</a>" % escape(here + _query(sort, desc, page + 1, view_asked))) if page < pages else ""
        pager = "<div class='pl-pager'>%s<span class='pl-muted'>Page %d of %d</span>%s</div>" % (prev, page, pages, nxt)
    title = parts[-1] if parts else root
    bar = _sort_bar(here, sort, desc, view_asked) if total > 1 else ""
    grid = ctx.view == "grid"
    other = "list" if grid else "grid"
    bar += ("<nav class='pl-sortbar pl-viewbar' aria-label='How to show this folder'><span class='pl-muted'>View:</span> "
            "<a%s href='%s'>List</a> <a%s href='%s'>Covers</a></nav>"
            % ("" if grid else " class='pl-sort-on'", escape(here + _query(sort, desc, 1, "list")),
               " class='pl-sort-on'" if grid else "", escape(here + _query(sort, desc, 1, "grid")))) if (total > 1 and ctx.thumbs) else ""
    note = "<p class='pl-flash' role='status'>%s</p>" % escape(flash) if flash else ""
    forms = _upload_forms(urls, root, parts) if writable else ""
    links = note_links or {}
    picture = ("<img class='pl-folder-cover' src='%s' alt='Cover of this folder' loading='lazy'>" % escape(cover_url)
               if cover_url else "")
    body = ("<main class='pl-main' data-queue='%s'><div class='pl-crumbs'>%s</div>%s<h1>%s</h1><div class='pl-muted'>%d items &middot; "
            "<a href='%s'>OPDS</a>%s</div>%s%s<ul class='%s'>%s</ul>%s</main>"
            % (escape(urls.api("queue", root, parts)), " / ".join(crumbs), picture, escape(title), total, escape(urls.opds(root, parts)),
               (" &middot; <a href='%s'>Add a note</a>" % escape(links["add"])) if links.get("add") else "",
               note + forms, bar + pager, "pl-list pl-grid" if grid else "pl-list", "".join(tiles if grid else rows),
               pager + (("<div class='pl-notes-lazy' id='folder-notes' data-src='%s'></div>" % escape(note_lazy)) if note_lazy else "")))
    scripts = (((urls.static("uploads.js"),) if writable else ()) + ((urls.static("grid.js"),) if grid else ())
               + ((urls.static("notes-lazy.js"),) if note_lazy else ())
               + (urls.static("shelf.js"), urls.static("queue.js")))
    return layout(title, body, ctx, scripts=scripts)


def station_folder_page(root: str, parts: tuple, stations: list, countries: list, cut: bool, ctx: Ctx, shuffle: bool = True, others: int = 0) -> str:
    """A folder of station files as one flat grid of logo tiles: search box, country chips (the sub-folders), a random pick. streams.js filters
    in the browser, so nothing is fetched while typing. Radio logos are square, channel logos 16:9."""
    urls = ctx.urls
    title = parts[-1] if parts else root
    crumbs = ["<a href='%s'>File Library</a>" % escape(urls.home()), "<a href='%s'>%s</a>" % (escape(urls.browse(root)), escape(root))]
    for i, seg in enumerate(parts):
        crumbs.append("<a href='%s'>%s</a>" % (escape(urls.browse(root, parts[:i + 1])), escape(seg)))
    tv = sum(1 for s in stations if s["kind"] == "tv") * 2 > len(stations)
    tiles = []
    for st in stations:
        full = parts + st["parts"]
        icon = "<span class='pl-icon pl-i-%s'></span>" % ("tv" if st["kind"] == "tv" else "radio")
        art = icon
        if radio.find_logo(st["path"]):
            art = ("<img class='pl-tile-img pl-logo-img' src='%s' alt='' loading='lazy'><span class='pl-tile-fallback'>%s</span>"
                   % (escape(urls.cover(root, full)), icon))
        tiles.append("<li class='pl-tile pl-stream' data-name='%s' data-country='%s'><a href='%s' title='%s'><span class='pl-tile-art'>%s</span>"
                     "<span class='pl-tile-name'>%s</span></a></li>"
                     % (escape(st["name"].lower()), escape(st["country"]), escape(urls.view(root, full)), escape(st["name"]), art, escape(st["name"])))
    chips = ""
    if countries:
        chips = ("<div class='pl-chips pl-stream-chips' role='group' aria-label='Country'><button type='button' class='pl-chip pl-chip-on' data-country=''>All</button>%s</div>"
                 % "".join("<button type='button' class='pl-chip' data-country='%s'>%s</button>" % (escape(c), escape(c)) for c in countries))
    note = "<p class='pl-muted'>Only the first %d are shown.</p>" % len(stations) if cut else ""
    body = ("<main class='pl-main pl-streams-main'><div class='pl-crumbs'>%s</div><h1>%s</h1>"
            "<div class='pl-muted'><span id='stream-count'>%d</span> %s &middot; <a href='%s?plain=1'>%s</a></div>"
            "<div class='pl-stream-tools'><input type='search' id='stream-search' placeholder='Search %s' aria-label='Search' autocomplete='off'>%s</div>%s%s"
            "<ul class='pl-list pl-grid pl-streams%s' id='streams'>%s</ul>"
            "<p class='pl-muted' id='stream-none' hidden>Nothing matches.</p></main>"
            % (" / ".join(crumbs), escape(title), len(stations), "channels" if tv else "stations", escape(urls.browse(root, parts)),
               ("Show %d other file%s too" % (others, "" if others == 1 else "s")) if others else "Show as an ordinary folder",
               "channels" if tv else "stations",
               "<button type='button' class='pl-btn' id='stream-random'>Random</button>" if shuffle else "", chips, note,
               " pl-streams-tv" if tv else "", "".join(tiles)))
    return layout(title, body, ctx, scripts=(urls.static("grid.js"), urls.static("streams.js")))


def _note_box(html: str, where: str, edit_url: str = "") -> str:
    """A folder's own note (rendered Markdown, already made safe by the renderer) at the top or the bottom of its page."""
    if not html:
        return ""
    edit = "<p class='pl-note-edit'><a href='%s'>Edit note</a></p>" % escape(edit_url) if edit_url else ""
    return "<section class='pl-note pl-note-%s pl-markdown'>%s%s</section>" % (where, html, edit)


COVER_KINDS = ("epub", "comic", "audio", "image")


def _tiles(ctx: Ctx, root: str, entries: list, metas: dict, progress: dict, shelves: dict | None = None) -> list:
    """The cover view: one tile per entry, a picture (a cover when the file has one, else its type icon) over the title."""
    urls, out = ctx.urls, []
    for e in entries:
        if e.is_dir:
            out.append("<li class='pl-tile pl-tile-folder'><a href='%s'><span class='pl-tile-art'>%s</span>"
                       "<span class='pl-tile-name'>%s</span></a>%s</li>"
                       % (escape(urls.browse(root, e.parts)), ROOT_ICON, escape(e.display),
                          _shelf_buttons(urls, root, e.parts, (shelves or {}).get("/".join(e.parts), []))))
            continue
        kind = kinds.kind_of(e.name)
        if not ctx.readers:
            open_url = urls.download(root, e.parts)
        elif kind in ("epub", "comic"):
            open_url = urls.read(root, e.parts)
        else:
            open_url = urls.view(root, e.parts)
        art = kind_icon(kind)
        if kind == "text" and radio.is_radio_file(e.name) and radio.find_logo(e.path):         # a station file with its logo beside it
            art = ("<img class='pl-tile-img pl-logo-img' src='%s' alt='' loading='lazy'><span class='pl-tile-fallback'>%s</span>"
                   % (escape(urls.cover(root, e.parts)), art))
        elif kind in COVER_KINDS:
            art = ("<img class='pl-tile-img' src='%s?w=320' alt='' loading='lazy'><span class='pl-tile-fallback'>%s</span>"
                   % (escape(urls.thumb(root, e.parts)), art))
        pct = progress.get("/".join(e.parts))
        badge = "<span class='pl-badge pl-tile-badge'>%d%%</span>" % round(pct * 100) if pct else ""
        label = _book_label(e, metas.get(e.path, {}))
        out.append("<li class='pl-tile'><a href='%s' title='%s'><span class='pl-tile-art'>%s%s</span>"
                   "<span class='pl-tile-name'>%s</span></a>%s</li>"
                   % (escape(open_url), escape(e.name), art, badge, label,
                      _shelf_buttons(urls, root, e.parts, (shelves or {}).get("/".join(e.parts), []))))
    return out


def _icon_button(ident: str, icon: str, label: str, extra: str = "") -> str:
    return ("<button class='pl-reader-btn %s' id='%s' type='button' title='%s' aria-label='%s'>"
            "<span class='pl-icon pl-i-%s'></span></button>" % (extra, ident, escape(label), escape(label), icon))


def _back_link(url: str) -> str:
    """The reader bar's arrow back to the folder (EPUB, comic and every file viewer). scheme.js turns a click into a step back in the browser's
    history when the file was opened from that folder, so the folder's own entry is not added a second time."""
    return ("<a class='pl-reader-btn' id='back' href='%s' title='Back to the folder' aria-label='Back to the folder'>"
            "<span class='pl-icon pl-i-back'></span></a>" % escape(url))


def _about_link(url: str) -> str:
    """The reader bar's link to the note about the author or series (the folder page that shows it); nothing when there is none."""
    return ("<a class='pl-reader-btn' id='about' href='%s' title='About the author or series' aria-label='About'>About</a>" % escape(url)) if url else ""


def epub_reader_page(ctx: Ctx, title: str, subtitle: str, *, book_url: str, progress_url: str, back_url: str,
                     content_css_url: str, key: str, about_url: str = "") -> str:
    """The EPUB reader shell. The page is styled by the theme; epub.js and reader-epub.js do the rest."""
    u = ctx.urls
    data = " ".join("data-%s='%s'" % (k, escape(v)) for k, v in (
        ("book", book_url), ("progress", progress_url), ("content-css", content_css_url), ("key", key)))
    body = (
        "<div class='pl-reader' id='reader' %s>"
        "<header class='pl-reader-bar'>"
        "%s%s<div class='pl-reader-title' title='%s'>%s</div>%s%s%s</header>"
        "<div class='pl-reader-stage'><div id='viewer' style='position:absolute;inset:0'></div>"
        "<div class='pl-tap pl-tap-left' id='tap-prev'></div><div class='pl-tap pl-tap-right' id='tap-next'></div>"
        "<nav class='pl-menu' id='toc' hidden aria-label='Contents'><h2>Contents</h2><ul id='toc-list'></ul></nav>"
        "<aside class='pl-menu pl-menu-right' id='settings' hidden aria-label='Reading settings'>"
        "<h2>Text size</h2><div><button class='pl-btn' id='size-down' type='button'>A&minus;</button> "
        "<span id='size-value'>100%%</span> <button class='pl-btn' id='size-up' type='button'>A+</button></div>"
        "<label class='pl-field'><span>Font</span><select id='font'><option value='theme'>Theme default</option>"
        "<option value='serif'>Serif</option><option value='sans'>Sans-serif</option></select></label>"
        "<label class='pl-field'><span>Line spacing</span><select id='line'><option value='1.3'>Tight</option>"
        "<option value='1.55'>Normal</option><option value='1.8'>Loose</option></select></label>"
        "<label class='pl-field'><span>Layout</span><select id='flow'><option value='paginated'>Pages</option>"
        "<option value='scrolled-doc'>Scrolling</option></select></label>"
        "<label class='pl-field'><span>Page width</span><select id='spread'>"
        "<option value='single'>One page, centered</option><option value='double'>Two pages (wide screens)</option>"
        "</select></label></aside>"
        "<div class='pl-toast' id='status'>Loading the book&hellip;</div></div>"
        "<footer class='pl-reader-foot'>%s<div class='pl-progress'><i id='bar'></i></div>"
        "<span id='pct'>&ndash;</span>%s</footer></div>"
        % (data, _back_link(back_url),
           _icon_button("toc-btn", "menu", "Contents"), escape(subtitle), escape(title),
           _about_link(about_url) + "<button class='pl-reader-btn pl-scheme' type='button' title='Toggle dark / light' aria-label='Toggle dark / light'>"
           "<span class='pl-icon pl-i-contrast'></span></button>",
           _icon_button("settings-btn", "settings", "Reading settings"),
           _icon_button("fs-btn", "fullscreen", "Full screen"),
           _icon_button("prev", "prev", "Previous page"), _icon_button("next", "next", "Next page")))
    return layout(title, body, ctx, bar=False, body_class="pl-reader-page",
                  scripts=(u.static("vendor/jszip.min.js"), u.static("vendor/epub.min.js"), u.static("reader-epub.js"),
                           u.static("menus-back.js")))


def comic_reader_page(ctx: Ctx, title: str, *, pages_url: str, count: int, progress_url: str, back_url: str,
                      key: str, about_url: str = "") -> str:
    """The single-page comic reader shell; reader-comic.js fetches the pages one by one."""
    data = " ".join("data-%s='%s'" % (k, escape(str(v))) for k, v in (
        ("pages", pages_url), ("count", count), ("progress", progress_url), ("key", key)))
    body = (
        "<div class='pl-reader pl-comic' id='reader' %s>"
        "<header class='pl-reader-bar'>"
        "%s<div class='pl-reader-title' title='%s'>%s</div>%s%s%s</header>"
        "<div class='pl-reader-stage pl-comic-stage pl-fit-page' id='stage'>"
        "<img class='pl-page' id='page' alt='' draggable='false'>"
        "<div class='pl-tap pl-tap-left' id='tap-prev'></div><div class='pl-tap pl-tap-right' id='tap-next'></div>"
        "<div class='pl-toast' id='status'>Loading&hellip;</div></div>"
        "<footer class='pl-reader-foot'>%s<div class='pl-progress' id='seek' title='Jump to a page'><i id='bar'></i></div>"
        "<span id='pct'>&ndash;</span>%s</footer></div>"
        % (data, _back_link(back_url), escape(title), escape(title),
           _about_link(about_url) + "<button class='pl-reader-btn pl-scheme' type='button' title='Toggle dark / light' aria-label='Toggle dark / light'>"
           "<span class='pl-icon pl-i-contrast'></span></button>",
           _icon_button("fit-btn", "fit", "Zoom: fit page, fit width, 150%, 200%, 300% (keys + - 0 F)"),
           _icon_button("fs-btn", "fullscreen", "Full screen"),
           _icon_button("prev", "prev", "Previous page"), _icon_button("next", "next", "Next page")))
    return layout(title, body, ctx, bar=False, body_class="pl-reader-page",
                  scripts=(ctx.urls.static("reader-comic.js"),))


def _reader_scheme() -> str:
    return ("<button class='pl-reader-btn pl-scheme' type='button' title='Toggle dark / light' "
            "aria-label='Toggle dark / light'><span class='pl-icon pl-i-contrast'></span></button>")


def _download_link(url: str) -> str:
    return ("<a class='pl-reader-btn' id='download' href='%s' title='Download' aria-label='Download'>"
            "<span class='pl-icon pl-i-download'></span></a>" % escape(url))


def _nav_link(ident: str, icon: str, label: str, url: str | None) -> str:
    if not url:
        return ("<button class='pl-reader-btn' id='%s' type='button' disabled aria-label='%s'>"
                "<span class='pl-icon pl-i-%s'></span></button>" % (ident, escape(label), icon))
    return ("<a class='pl-reader-btn' id='%s' href='%s' title='%s' aria-label='%s'>"
            "<span class='pl-icon pl-i-%s'></span></a>" % (ident, escape(url), escape(label), escape(label), icon))


def _viewer(ctx: Ctx, title: str, back_url: str, *, tools: str, stage: str, foot: str, classes: str,
            stage_class: str = "", data: tuple = (), scripts: tuple = (), module_scripts: tuple = ()) -> str:
    """The shared frame of every file viewer: the reader bar, a stage and a footer, all themed like the readers."""
    attrs = " ".join("data-%s='%s'" % (k, escape(str(v))) for k, v in data)
    body = ("<div class='pl-reader %s' id='reader' %s><header class='pl-reader-bar'>"
            "%s<div class='pl-reader-title' title='%s'>%s</div>%s</header>"
            "<div class='pl-reader-stage %s'>%s</div><footer class='pl-reader-foot'>%s</footer></div>"
            % (classes, attrs, _back_link(back_url), escape(title), escape(title), tools, stage_class, stage, foot))
    return layout(title, body, ctx, bar=False, body_class="pl-reader-page", scripts=scripts,
                  module_scripts=module_scripts)


def image_viewer_page(ctx: Ctx, title: str, *, raw_url: str, download_url: str, back_url: str, prev_url, next_url,
                      next_raw, position: int, total: int) -> str:
    data = (("prev", prev_url or ""), ("next", next_url or ""), ("next-raw", next_raw or ""))
    stage = ("<img class='pl-page' id='page' src='%s' alt='%s'>" % (escape(raw_url), escape(title)) +
             ("<div class='pl-tap pl-tap-left' id='tap-prev'></div>" if prev_url else "") +
             ("<div class='pl-tap pl-tap-right' id='tap-next'></div>" if next_url else "") +
             "<div class='pl-toast' id='status' hidden></div>")
    foot = ("%s<span class='pl-progress-spacer' style='flex:1'></span><span id='pct'>%d / %d</span>%s"
            % (_nav_link("prev", "prev", "Previous image", prev_url), position, total,
               _nav_link("next", "next", "Next image", next_url)))
    tools = _download_link(download_url) + _reader_scheme() + _icon_button("fit-btn", "fit", "Fit to window / actual size") + \
        _icon_button("fs-btn", "fullscreen", "Full screen")
    return _viewer(ctx, title, back_url, tools=tools, stage=stage, foot=foot, classes="pl-image",
                   stage_class="pl-comic-stage pl-fit-page", data=data, scripts=(ctx.urls.static("viewer-image.js"),))


def text_viewer_page(ctx: Ctx, title: str, *, text: str, encoding: str, size: int, shown: int, truncated: bool,
                     download_url: str, back_url: str, forced: bool) -> str:
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    note = ""
    if truncated:
        note = (" &middot; <strong>only the first %s of %s is shown</strong> &middot; <a href='%s'>download the whole file</a>"
                % (_size(shown), _size(size), escape(download_url)))
    if forced:
        note += " &middot; shown as text although it looks like a binary file"
    tools = (_download_link(download_url) + _reader_scheme() +
             "<button class='pl-reader-btn' id='size-down' type='button' title='Smaller text' aria-label='Smaller text'>A&minus;</button>"
             "<button class='pl-reader-btn' id='size-up' type='button' title='Larger text' aria-label='Larger text'>A+</button>"
             "<button class='pl-reader-btn' id='wrap-btn' type='button' title='Wrap long lines' aria-label='Wrap long lines'>Wrap</button>")
    art = " pl-art" if title.lower().endswith((".nfo", ".diz", ".asc")) else ""         # DOS art: tight lines so the blocks join up
    stage = "<pre class='pl-text%s' id='text' tabindex='0'>%s</pre>" % (art, escape(text))
    foot = "<span>%s &middot; %s &middot; %d lines%s</span>" % (escape(encoding), _size(size), lines, note)
    return _viewer(ctx, title, back_url, tools=tools, stage=stage, foot=foot, classes="pl-textview",
                   scripts=(ctx.urls.static("viewer-text.js"),))


def _lyrics_html(synced: list, plain: str) -> str:
    """The lyrics panel: timed lines (each knows its second, the script highlights the current one) or the plain text. Timed lyrics (a .lrc file
    next to the song, or timed lines in its tag) are shown from the start, below the controls; the Lyrics button folds them away. Plain lyrics
    stay folded until the button is pressed."""
    if synced:
        lines = "".join("<p data-t='%s'>%s</p>" % (t, escape(words) or "&nbsp;") for t, words in synced)
        return "<div class='pl-lyrics pl-lyrics-synced' id='lyrics'>%s</div>" % lines
    if plain:
        return "<div class='pl-lyrics pl-lyrics-plain' id='lyrics' hidden>%s</div>" % escape(plain)
    return ""


def _chapters_html(chapters: list) -> str:
    """The chapter list of an audiobook: one button per chapter, the script seeks to its second and marks the current one."""
    if not chapters:
        return ""
    rows = "".join("<button type='button' class='pl-chapter' data-t='%s'><span class='pl-chapter-time'>%s</span> %s</button>"
                   % (t, _clock(t), escape(title or "Chapter %d" % (i + 1))) for i, (t, title) in enumerate(chapters))
    return "<div class='pl-panel pl-chapters' id='chapters' hidden>%s</div>" % rows


def _bookmarks_html() -> str:
    return ("<div class='pl-panel pl-bookmarks' id='bookmarks' hidden><button type='button' class='pl-btn' id='bookmark-add'>"
            "Add a bookmark here</button><div id='bookmark-list' class='pl-bookmark-list'></div></div>")


def _clock(seconds: float) -> str:
    s = int(seconds)
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60) if s >= 3600 else "%d:%02d" % (s // 60, s % 60)


def media_viewer_page(ctx: Ctx, title: str, *, kind: str, raw_url: str, mime: str, download_url: str, back_url: str,
                      progress_url: str, prev_url, next_url, position: int, total: int, album: str = "",
                      tags: dict | None = None, cover_url: str = "", synced: list | None = None, chapters: list | None = None,
                      bookmarks_url: str = "", extras: bool = True, subtitles: list | None = None, queue_url: str = "") -> str:
    """The audio/video viewer. Audio gets a cover, the tags (title, artist, album, year, genre, track), a scrubber, big transport
    buttons and lyrics; video keeps the browser's own controls under the same information. Tags are untrusted text: escaped.
    viewer-media.js adds resume, auto-advance to the next file, the lyrics highlight and the lock-screen controls."""
    tags, synced, chapters = tags or {}, synced or [], chapters or []
    tag = "video" if kind == "video" else "audio"
    audiobook = title.lower().endswith(".m4b")
    name = title.rsplit(".", 1)[0] if "." in title else title
    heading = tags.get("title") or name
    artist = tags.get("artist") or tags.get("albumartist") or ""
    album_name = tags.get("album") or album
    by = " &middot; ".join(escape(x) for x in (artist, album_name) if x)
    chips = "".join("<span class='pl-chip'>%s</span>" % escape(f) for f in mediainfo.facts(tags))
    lyrics = _lyrics_html(synced, tags.get("lyrics", ""))

    def button(ident: str, label: str, icon: str, text: str = "", after: bool = False, extra: str = "") -> str:
        glyph = "<span class='pl-icon pl-i-%s'></span>" % icon
        words = "<span class='pl-btn-text'>%s</span>" % text if text else ""
        return ("<button class='pl-btn %s' id='%s' type='button' aria-label='%s'>%s</button>"
                % (extra, ident, escape(label), words + glyph if after else glyph + words))
    skip_back = button("back30", "Back 30 seconds", "rew")                    # the old tape-deck double triangles, no text
    skip_fwd = button("fwd30", "Forward 30 seconds", "ff")
    if extras:
        # music and movies alike: a small - 1x + pill right under the play button. The 1x is only a readout (tapping it does nothing),
        # so a thumb reaching for play cannot change the speed by accident; the - and + step through the speeds
        speed_val = ("<span class='pl-speedpill'><button class='pl-speed-step' id='speed-down' type='button' aria-label='Slower'>&minus;</button>"
                     "<span class='pl-speed-readout' id='speed' role='img' aria-label='Playback speed'>1&times;</span>"
                     "<button class='pl-speed-step' id='speed-up' type='button' aria-label='Faster'>+</button></span>")
        # sleep timer and bookmarks are for audiobooks (m4b), where you stop in the middle and fall asleep; not for a song
        sleep = button("sleep", "Sleep timer: pause after a while", "moon", "Sleep") if audiobook else ""
    else:
        speed_val = sleep = ""
    if not audiobook:
        bookmarks_url = ""
    def panel_button(panel: str, label: str, is_open: bool = False) -> str:
        return ("<button class='pl-btn pl-panel-btn' id='%s-btn' data-panel='%s' type='button' aria-label='Show: %s' "
                "aria-expanded='%s'><span class='pl-btn-text'>%s</span></button>" % (panel, panel, label, "true" if is_open else "false", label))
    words_button = (panel_button("lyrics", "Lyrics", is_open=bool(synced)) if lyrics else "") + (panel_button("chapters", "Chapters") if chapters else "") + \
        (panel_button("bookmarks", "Bookmarks") if bookmarks_url else "")
    lyrics += _chapters_html(chapters) + (_bookmarks_html() if bookmarks_url else "")
    info = ("<div class='pl-track'><h1 class='pl-track-title'>%s</h1>%s%s</div>"
            % (escape(heading), "<div class='pl-track-by'>%s</div>" % by if by else "",
               "<div class='pl-chips'>%s</div>" % chips if chips else ""))
    # the position bar, volume and (for a movie) full screen, and the row of big buttons: the same for music and movies
    fullscreen = ("<button type='button' id='mx' class='pl-vol-btn pl-fs-btn' aria-label='Maximize: fill the screen with the controls under the picture' title='Maximize (controls under the picture)'><span class='pl-icon pl-i-maximize'></span></button>"
                  "<button type='button' id='fs' class='pl-vol-btn pl-fs-btn' aria-label='Full screen: the picture fills the screen' title='Full screen (picture only, controls fade away)'><span class='pl-icon pl-i-fullscreen'></span></button>"
                  if kind == "video" else "")
    subtitles = subtitles or []
    captions = ("<button type='button' id='cc' class='pl-vol-btn pl-cc-btn' aria-label='Subtitles' aria-pressed='false'>CC</button>"
                if subtitles and kind == "video" else "")
    scrub_row = ("<div class='pl-scrub'><span id='cur'>0:00</span><input type='range' id='scrub' min='0' max='1000' value='0' "
                 "aria-label='Position'><span id='dur'>&ndash;:&ndash;&ndash;</span>"
                 "<span class='pl-vol'><button type='button' id='mute' class='pl-vol-btn' aria-label='Mute'><span class='pl-icon pl-i-volume'></span></button>"
                 "<input type='range' id='volume' min='0' max='100' value='100' aria-label='Volume'></span>%s%s</div>" % (captions, fullscreen))
    tracks = "".join("<track kind='subtitles' src='%s' label='%s'>" % (escape(url), escape(label)) for url, label in subtitles) if kind == "video" else ""
    # the row is five buttons for a song and a movie alike: previous / next file, the 30 second skips, play. Previous always works
    # (on the first file it restarts it); next is greyed on the last one. The speed pill hangs under the play button.
    song_prev = button("prev-song", "Previous file" if kind == "video" else "Previous song", "song-prev")
    song_next = button("next-song", "Next file" if kind == "video" else "Next song", "song-next").replace(
        "<button ", "<button %s" % ("" if next_url else "disabled "), 1)
    transport_row = ("<div class='pl-transport'>%s%s<span class='pl-play-wrap'>%s%s</span>%s%s</div>"
                     % (song_prev, skip_back, button("play", "Play", "play", extra="pl-play"), speed_val, skip_fwd, song_next))
    if kind == "video":
        stage = ("<div class='pl-media-box pl-player pl-videobox'>%s<div class='pl-videowrap'><video id='media' controls preload='metadata' playsinline src='%s'>%s"
                 "<p>Your browser cannot play this file; <a href='%s'>download it</a>.</p></video></div>%s%s<div class='pl-tools'>%s</div>%s"
                 "<div class='pl-toast' id='status' hidden></div></div>"
                 % (info, escape(raw_url), tracks, escape(download_url), scrub_row, transport_row, words_button + sleep, lyrics))
    else:
        picture = ("<img class='pl-cover-img' src='%s' alt='Cover'>" % escape(cover_url) if cover_url
                   else "<div class='pl-cover-blank'>%s</div>" % ico("audio"))
        backdrop = "<img class='pl-bg' src='%s' alt=''>" % escape(cover_url) if cover_url else ""
        stage = ("<div class='pl-media-box pl-player'>%s<div class='pl-cover'>%s</div>%s"
                 "<audio id='media' controls preload='metadata' src='%s'><p>Your browser cannot play this file; "
                 "<a href='%s'>download it</a>.</p></audio>%s%s<div class='pl-tools'>%s%s</div>%s"
                 "<div class='pl-toast' id='status' hidden></div></div>"
                 % (backdrop, picture, info, escape(raw_url), escape(download_url), scrub_row, transport_row, sleep, words_button, lyrics))
    foot = ("%s<span style='flex:1'></span><span>%d / %d</span>%s"
            % (_nav_link("prev", "prev", "Previous file", prev_url), position, total,
               _nav_link("next", "next", "Next file", next_url)))
    tools = _download_link(download_url) + _reader_scheme()
    # the top-left title reads like a library path: Band – Album (year) – 05 Title (year and track number only when the tags have them)
    year = str(tags.get("year") or "")
    album_part = album_name + (" (%s)" % year if album_name and year.isdigit() else "")
    track = tags.get("track")
    title_part = ("%02d %s" % (track, heading)) if isinstance(track, int) and track > 0 else heading
    bar_title = " – ".join(x for x in (artist, album_part, title_part) if x)
    return _viewer(ctx, bar_title, back_url, tools=tools, stage=stage, foot=foot, classes="pl-mediaview pl-" + tag,
                   stage_class="pl-media-stage",
                   data=(("progress", progress_url), ("kind", kind), ("prev", prev_url or ""), ("next", next_url or ""),
                         ("title", heading), ("artist", artist), ("album", album_name), ("bookmarks", bookmarks_url), ("queue", queue_url),
                         ("artwork", cover_url or ctx.urls.static("icon/icon-512.png"))),
                   scripts=(ctx.urls.static("player-common.js"), ctx.urls.static("viewer-media.js")))


def pdf_viewer_page(ctx: Ctx, title: str, *, raw_url: str, download_url: str, back_url: str, progress_url: str) -> str:
    """The PDF viewer shell; viewer-pdf.mjs renders pages with pdf.js (vendored), one page at a time."""
    stage = ("<canvas id='canvas' class='pl-pdf-canvas'></canvas>"
             "<div class='pl-tap pl-tap-left' id='tap-prev'></div><div class='pl-tap pl-tap-right' id='tap-next'></div>"
             "<div class='pl-toast' id='status'>Loading&hellip;</div>")
    foot = ("%s<div class='pl-progress' id='seek' title='Jump to a page'><i id='bar'></i></div><span id='pct'>&ndash;</span>%s"
            % (_icon_button("prev", "prev", "Previous page"), _icon_button("next", "next", "Next page")))
    tools = (_download_link(download_url) + _reader_scheme() +
             _icon_button("minus-btn", "minus", "Zoom out") + _icon_button("plus-btn", "plus", "Zoom in") +
             _icon_button("fit-btn", "fit", "Fit to width / fit to page") +
             "<button class='pl-reader-btn' id='invert-btn' type='button' title='Invert colours (dark pages)' "
             "aria-label='Invert colours'>Invert</button>" + _icon_button("fs-btn", "fullscreen", "Full screen"))
    return _viewer(ctx, title, back_url, tools=tools, stage=stage, foot=foot, classes="pl-pdf",
                   stage_class="pl-pdf-stage", data=(("src", raw_url), ("progress", progress_url), ("download", download_url)),
                   module_scripts=(ctx.urls.static("viewer-pdf.mjs"),))


def shell_page(ctx: Ctx, start_url: str) -> str:
    """The outer page: the pluteus bar, a slim player strip and the whole site in a frame below. The audio lives here, so it keeps
    playing while the frame goes from page to page (folders, readers). Pages shown in the frame hide their own bar (scheme.js)."""
    def btn(ident: str, label: str, icon: str) -> str:
        return ("<button type='button' class='pl-vol-btn' id='%s' aria-label='%s'><span class='pl-icon pl-i-%s'></span></button>"
                % (ident, label, icon))
    strip = ("<div class='pl-shell-player' id='sp' data-hls='%s' data-mpegts='%s' hidden>%s%s%s<img class='pl-shell-logo' id='sp-logo' alt='' hidden><span class='pl-shell-title' id='sp-title'></span>"
             "<a class='pl-shell-link' id='sp-link' target='shell-frame' href='#' hidden>Picture</a>"
             "<span class='pl-muted' id='sp-pos'></span>%s<div class='pl-shell-prog'><i id='sp-bar'></i></div></div>"
             % (escape(ctx.urls.static("vendor/hls.min.js")), escape(ctx.urls.static("vendor/mpegts.min.js")), btn("sp-prev", "Previous", "prev"), btn("sp-play", "Play", "play"),
                btn("sp-next", "Next", "next"),
                "<button type='button' class='pl-vol-btn' id='sp-close' aria-label='Close the player'>&times;</button>"))
    frame = ("<iframe class='pl-shell-frame' id='shell-frame' name='shell-frame' title='Pluteus' src='%s' allow='fullscreen; autoplay'></iframe>"
             % escape(start_url))
    return layout("Pluteus", strip + frame, ctx, body_class="pl-shell-page", scripts=(ctx.urls.static("shell.js"),), shell=False)


def iptv_page(ctx: Ctx, title: str, *, stations: list, download_url: str, text_url: str, back_url: str, page_url: str = "",
              logo_url: str = "", relay_urls: list | None = None, always: list | None = None) -> str:
    """A live channel (or several) from a station file: the picture and sound play in a video element; HLS goes through the vendored
    hls.js (iptv.js) where the browser cannot play it itself. The addresses are written by the owner of the library."""
    name = title.rsplit(".", 1)[0] if "." in title else title
    first_name, first_url = stations[0]
    heading = first_name or name
    relays = relay_urls or []                                   # each channel's playlist through the server, when the relay is on
    listing = ("<div class='pl-stations' id='stations'>%s</div>"
               % "".join("<button type='button' class='pl-station%s' data-url='%s' data-name='%s' data-relay='%s' data-always='%s'>%s</button>"
                         % (" pl-now" if i == 0 else "", escape(url), escape(label or name), escape(relays[i] if i < len(relays) else ""),
                            "1" if (always and i < len(always) and always[i]) else "0", escape(label or "%s %d" % (name, i + 1)))
                         for i, (label, url) in enumerate(stations))) if len(stations) > 1 else ""
    # the same pieces as the movie player: a row under the picture (here the LIVE badge in place of the position bar, then the volume
    # and full screen) and the big round play button; without the script the video keeps the browser's own controls
    live_row = ("<div class='pl-scrub pl-live-row'><span class='pl-live'><span class='pl-live-dot'></span>LIVE</span><span class='pl-live-fill'></span>"
                "<span class='pl-vol'><button type='button' id='mute' class='pl-vol-btn' aria-label='Mute'><span class='pl-icon pl-i-volume'></span></button>"
                "<input type='range' id='volume' min='0' max='100' value='100' aria-label='Volume'></span>"
                "<button type='button' id='cc' class='pl-vol-btn pl-cc-btn' aria-label='Subtitles' aria-pressed='false' hidden>CC</button>"
                "<button type='button' id='mx' class='pl-vol-btn pl-fs-btn' aria-label='Maximize: fill the screen with the controls under the picture' title='Maximize (controls under the picture)'><span class='pl-icon pl-i-maximize'></span></button>"
                "<button type='button' id='fs' class='pl-vol-btn pl-fs-btn' aria-label='Full screen: the picture fills the screen' title='Full screen (picture only, controls fade away)'><span class='pl-icon pl-i-fullscreen'></span></button></div>")
    transport = ("<div class='pl-transport'><span class='pl-play-wrap'><button class='pl-btn pl-play' id='play' type='button' aria-label='Play'>"
                 "<span class='pl-icon pl-i-play'></span></button></span></div>")
    logo = "<img class='pl-tv-logo' src='%s' alt=''>" % escape(logo_url) if logo_url else ""
    stage = ("<div class='pl-media-box pl-player pl-videobox pl-iptv'><div class='pl-track pl-tv-head'>%s<h1 class='pl-track-title' id='now'>%s</h1></div>"
             "<div class='pl-videowrap'><video id='tv' controls playsinline data-url='%s' data-page='%s' data-logo='%s' data-relay='%s' data-always='%s' data-mpegts='%s'%s></video><div class='pl-toast' id='status' hidden></div></div>%s%s%s"
             "<div class='pl-bufferbar' id='bufferbar' role='group' aria-label='Buffer' hidden>"
             "<span class='pl-seg'><button type='button' data-sec='0' title='Smallest delay: closest to live, but every hiccup shows'>Live</button>"
             "<button type='button' data-sec='8' title='Buffered: about 8 seconds behind live, rides out short stalls'>Buffered</button>"
             "<button type='button' data-sec='20' title='Extra buffered: about 20 seconds behind live, for a poor connection'>Extra buffered</button></span></div>"
             "<p class='pl-tvinfo-bar'><button type='button' class='pl-btn pl-btn-small' id='info' aria-expanded='false' hidden>Stream info</button></p>"
             "<pre class='pl-tvinfo pl-muted' id='tvinfo' hidden></pre></div>"
             % (logo, escape(heading), escape(first_url), escape(page_url), escape(logo_url), escape(relays[0] if relays else ""), "1" if (always and always[0]) else "0",
                escape(ctx.urls.static("vendor/mpegts.min.js")), " data-keep-sound='1'" if ctx.tv_sound else "", live_row, transport, listing))
    tools = _download_link(download_url) + _reader_scheme()
    foot = "<a href='%s'>Show as text</a>" % escape(text_url)
    scripts = (ctx.urls.static("vendor/hls.min.js"), ctx.urls.static("player-common.js"), ctx.urls.static("iptv.js"))
    return _viewer(ctx, title, back_url, tools=tools, stage=stage, foot=foot, classes="pl-mediaview pl-video",
                   stage_class="pl-media-stage", scripts=scripts)


def radio_page(ctx: Ctx, title: str, *, stations: list, download_url: str, text_url: str, back_url: str, logo_url: str = "",
               relay_urls: list | None = None, always: list | None = None) -> str:
    """The radio player for a station file: the stations are written by the owner of the library, the browser plays the stream
    itself (an <audio> element; the page's CSP allows media from anywhere, see app.RADIO_CSP). radio.js does the rest."""
    name = title.rsplit(".", 1)[0] if "." in title else title
    first_name, first_url = stations[0]
    heading = first_name or name
    many = len(stations) > 1
    listing = ("<div class='pl-stations' id='stations'>%s</div>"
               % "".join("<button type='button' class='pl-station%s' data-url='%s' data-name='%s'>%s</button>"
                         % (" pl-now" if i == 0 else "", escape(url), escape(label or name), escape(label or "%s %d" % (name, i + 1)))
                         for i, (label, url) in enumerate(stations))) if many else ""
    art = "<img class='pl-cover-img pl-logo-img' src='%s' alt=''>" % escape(logo_url) if logo_url else "<div class='pl-cover-blank'>%s</div>" % ico("audio")
    stage = ("<div class='pl-media-box pl-player pl-radio'><div class='pl-cover'>%s</div>"
             "<div class='pl-track'><h1 class='pl-track-title' id='now'>%s</h1><div class='pl-track-by'>%s</div>"
             "<div class='pl-chips'><span class='pl-chip'>Live radio</span></div><div class='pl-now-playing' id='now-playing' hidden></div></div>"
             "<audio id='radio' controls preload='none' src='%s'><p>Your browser cannot play this stream.</p></audio>"
             "<div class='pl-transport'><span class='pl-play-wrap'><button class='pl-btn pl-play' id='play' type='button' aria-label='Play'>"
             "<span class='pl-icon pl-i-play'></span></button></span></div>"
             "<div class='pl-scrub pl-radio-scrub'><span class='pl-vol'><button type='button' id='mute' class='pl-vol-btn' aria-label='Mute'>"
             "<span class='pl-icon pl-i-volume'></span></button><input type='range' id='volume' min='0' max='100' value='100' aria-label='Volume'></span></div>"
             "%s<div class='pl-toast' id='status' hidden></div></div>"
             % (art, escape(heading), escape(name) if heading != name else "&nbsp;", escape(first_url), listing))
    tools = _download_link(download_url) + _reader_scheme()
    foot = "<a href='%s'>Show as text</a>" % escape(text_url)
    return _viewer(ctx, title, back_url, tools=tools, stage=stage, foot=foot, classes="pl-mediaview pl-audio",
                   stage_class="pl-media-stage",
                   data=(("title", heading), ("artwork", logo_url or ctx.urls.static("icon/icon-512.png")), ("logo", logo_url),
                         ("relay", "|".join(relay_urls or [])),               # one address per station, when the relay is on
                         ("always", "|".join("1" if a else "0" for a in (always or [])))),    # 1: the station needs a stored stream: only the server may fetch it
                   scripts=(ctx.urls.static("player-common.js"), ctx.urls.static("radio.js")))


def streams_page(ctx: Ctx, entries: list, key_note: str, message: str = "", error: str = "") -> str:
    """My streams (Settings): the user's stored logins and headers for protected streams. It lists what each one holds without any value, and
    takes a new one; a secret is never written into a page."""
    badges = {"ok": "", "other-key": " <span class='pl-badge'>stored with another key: add it again</span>",
              "damaged": " <span class='pl-badge'>cannot be opened: add it again</span>"}
    rows = []
    for e in entries:
        shown = e.get("shown") or {}
        what = [{"basic": "a login", "bearer": "a token"}.get(shown.get("login", ""), "")]
        if shown.get("headers"):
            what.append("headers: " + ", ".join(shown["headers"]))
        rows.append("<li class='pl-row'><span class='pl-title-text'><strong>%s</strong>%s<br><span class='pl-muted'>%s &middot; sent only to %s</span></span>"
                    "<form method='post' action='%s' data-confirm='Delete the stored stream %s?'><input type='hidden' name='action' value='delete'>"
                    "<input type='hidden' name='name' value='%s'><button class='pl-btn pl-btn-small' type='submit'>Delete</button></form></li>"
                    % (escape(e["name"]), badges[e["status"]], escape(", ".join(w for w in what if w) or "nothing"),
                       escape(", ".join(shown.get("hosts", [])) or "no host"), escape(ctx.urls.my_streams()), escape(e["name"]), escape(e["name"])))
    listing = ("<ul class='pl-list'>%s</ul>" % "".join(rows)) if rows else "<p class='pl-muted'>Nothing stored yet.</p>"
    note = ("<p class='pl-flash' role='status'>%s</p>" % escape(message)) if message else ""
    note += ("<p class='pl-flash pl-error' role='alert'>%s</p>" % escape(error)) if error else ""
    form = ("<form class='pl-card' method='post' action='%s' autocomplete='off'><input type='hidden' name='action' value='save'>"
            "<label class='pl-field'><span>Name</span><input name='name' maxlength='60' required placeholder='My subscription'></label>"
            "<label class='pl-field'><span>Login</span><select name='login'><option value='none'>No login</option>"
            "<option value='basic'>User name and password</option><option value='bearer'>A token</option></select></label>"
            "<label class='pl-field'><span>User name</span><input name='user' maxlength='200' autocomplete='off'></label>"
            "<label class='pl-field'><span>Password</span><input name='password' type='password' maxlength='200' autocomplete='new-password'></label>"
            "<label class='pl-field'><span>Token</span><input name='token' type='password' maxlength='2000' autocomplete='off'></label>"
            "<label class='pl-field'><span>Headers, one per line (for example Referer: https://www.example.com/)</span><textarea name='headers' rows='3'></textarea></label>"
            "<label class='pl-field'><span>The stream's server: send the login only there</span>"
            "<input name='hosts' required placeholder='stream.example.org' aria-describedby='hosts-help'>"
            "<small class='pl-muted' id='hosts-help'>The part of the stream's address between <code>https://</code> and the first <code>/</code>. "
            "For <code>https://tv.example.net/live/1.m3u8</code> that is <code>tv.example.net</code>. Several servers are separated by commas. "
            "The login and headers are never sent anywhere else, even if the stream sends you on to another server.</small></label>"
            "<button class='pl-btn pl-btn-primary' type='submit'>Save</button></form>" % escape(ctx.urls.my_streams()))
    body = ("<main class='pl-main'><h1>My streams</h1>%s"
            "<p class='pl-muted'>Logins and headers for streams that need them. They are kept encrypted, only you can use them, and they are never shown again. "
            "To use one, add <code>pluteus-stream=\"Name\"</code> to the station's <code>#EXTINF</code> line in its <code>.m3u</code> file: that station then always "
            "plays through the server, which adds the login and headers itself, and only for the stream's server you name below.</p>"
            "<section class='pl-card pl-home-box'><h2>Stored</h2>%s</section><h2>Add or replace one</h2>%s"
            "<p class='pl-muted'>%s</p><p><a href='%s'>&larr; Back to Settings</a></p></main>"
            % (note, listing, form, escape(key_note), escape(ctx.urls.settings())))
    return layout("My streams", body, ctx)


def note_editor_page(ctx: Ctx, root: str, parts: tuple, *, name: str, names: tuple, text: str, versions: dict, in_place: bool, exists: bool,
                     from_library: bool, preview_html: str = "", error: str = "", back_url: str = "", where_kind: str = "own",
                     choices: tuple = ()) -> str:
    """Edit (or start) a folder's note. `name` is empty for a new note, which then picks one of `names`."""
    urls = ctx.urls
    where = ("your upload folder" if in_place else "your own upload folder (under Notes), visible only to you; the library itself is not changed")
    if where_kind == "library":
        where = "the library itself, next to the files, for everybody who sees this folder"
    picker = ""
    if len(choices) > 1:                                     # both kinds are open to this user: they choose where the note goes
        picker = ("<label class='pl-field'><span>Save it as</span><select name='where'>%s</select></label>"
                  % "".join("<option value='%s'%s>%s</option>" % (k, " selected" if k == where_kind else "", escape(label)) for k, label in choices))
    elif choices:
        picker = "<input type='hidden' name='where' value='%s'>" % escape(choices[0][0])
    chooser = ("<input type='hidden' name='name' value='%s'>" % escape(name)) if name else (
        "<label class='pl-field'><span>Note name</span><select name='name'>%s</select></label>"
        % "".join("<option>%s</option>" % escape(n) for n in names))
    lead = ("<p class='pl-muted'>This starts from the note in the library. Saving keeps your own copy in %s.</p>" % where if from_library else
            "<p class='pl-muted'>Saved in %s.</p>" % where)
    err = ("<p class='pl-flash pl-error' role='alert'>%s</p>" % escape(error)) if error else ""
    preview = ("<section class='pl-card'><h2>Preview</h2><div class='pl-markdown'>%s</div></section>" % preview_html) if preview_html else ""
    remove = ("<button class='pl-btn pl-btn-danger' type='submit' name='action' value='remove' formnovalidate "
              "data-confirm='Remove your note? The library is not touched.'>Remove</button>") if exists else ""
    title = "Edit note" if name else "Add a note"
    form = ("<form class='pl-card' method='post' action='%s' autocomplete='off'><input type='hidden' name='v_own' value='%s'><input type='hidden' name='v_library' value='%s'>%s%s"
            "<label class='pl-field'><span>Text (Markdown)</span><textarea name='text' rows='18' maxlength='65536' spellcheck='true'>%s</textarea></label>"
            "<p><button class='pl-btn pl-btn-primary' type='submit' name='action' value='save'>Save</button> "
            "<button class='pl-btn' type='submit' name='action' value='preview'>Preview</button> %s "
            "<a class='pl-btn' href='%s'>Cancel</a></p></form>"
            % (escape(urls.note(root, parts)), escape(versions.get("own", "")), escape(versions.get("library", "")), picker, chooser, escape(text), remove, escape(back_url)))
    body = ("<main class='pl-main'><h1>%s</h1><p class='pl-muted'>%s</p>%s%s%s%s</main>"
            % (title, escape("/".join((root, *parts))), err, lead, preview, form))
    return layout(title, body, ctx)


def info_page(ctx: Ctx, title: str, *, rows: list, download_url: str, text_url: str, back_url: str, note: str = "") -> str:
    table = "".join("<tr><th>%s</th><td>%s</td></tr>" % (escape(k), escape(v)) for k, v in rows)
    stage = ("<div class='pl-info pl-card'><h1>%s</h1><table class='pl-info-table'>%s</table>%s"
             "<p><a class='pl-btn pl-btn-primary' href='%s'>Download</a> <a class='pl-btn' href='%s'>View as text</a></p></div>"
             % (escape(title), table, "<p class='pl-muted'>%s</p>" % escape(note) if note else "",
                escape(download_url), escape(text_url)))
    return _viewer(ctx, title, back_url, tools=_download_link(download_url) + _reader_scheme(), stage=stage,
                   foot="<span>No viewer for this file type</span>", classes="pl-infoview", stage_class="pl-info-stage")


def _source_button(url: str) -> str:
    return ("<a class='pl-reader-btn' id='source' href='%s' title='Show the file as plain text' "
            "aria-label='Show the file as plain text'>Source</a>" % escape(url))


def _cut_note(shown: int, size: int, truncated: bool, download_url: str) -> str:
    if not truncated:
        return ""
    return (" &middot; <strong>only the first %s of %s is shown</strong> &middot; <a href='%s'>download the whole file</a>"
            % (_size(shown), _size(size), escape(download_url)))


def markdown_page(ctx: Ctx, title: str, *, html: str, size: int, shown: int, truncated: bool, download_url: str,
                  back_url: str, source_url: str, edit_url: str = "") -> str:
    """A Markdown file rendered as a page. `html` comes from markdown.render (raw HTML already escaped)."""
    edit = ("<a class='pl-reader-btn' id='edit' href='%s' title='Edit this note' aria-label='Edit this note'>Edit</a>" % escape(edit_url)) if edit_url else ""
    tools = edit + _download_link(download_url) + _reader_scheme() + _source_button(source_url)
    stage = "<article class='pl-markdown' id='doc'>%s</article>" % html
    foot = "<span>Markdown &middot; %s%s</span>" % (_size(size), _cut_note(shown, size, truncated, download_url))
    return _viewer(ctx, title, back_url, tools=tools, stage=stage, foot=foot, classes="pl-mdview", stage_class="pl-md-stage")


def table_page(ctx: Ctx, title: str, *, table, size: int, shown: int, truncated: bool, download_url: str, back_url: str,
               source_url: str) -> str:
    """A CSV/TSV file as a table (see tabular.parse). The header row sticks; clicking a header sorts; a box filters rows."""
    head, body = table.rows[0], table.rows[1:]
    ths = "".join("<th scope='col' data-col='%d'%s>%s</th>" % (i, " data-num='1'" if table.numeric[i] else "", escape(h))
                  for i, h in enumerate(head))
    trs = "".join("<tr>%s</tr>" % "".join("<td%s>%s</td>" % (" class='pl-num'" if table.numeric[i] else "", escape(c))
                                          for i, c in enumerate(row)) for row in body)
    notes = []
    if table.rows_cut:
        notes.append("only the first %d rows are shown" % len(table.rows))
    if table.cols_cut:
        notes.append("only the first %d columns are shown" % len(head))
    if table.cells_cut:
        notes.append("%d very long cell%s shortened" % (table.cells_cut, "" if table.cells_cut == 1 else "s"))
    sep = {"\t": "tab", ",": "comma", ";": "semicolon", "|": "bar"}.get(table.delimiter, table.delimiter)
    tools = ("<input class='pl-table-filter' id='filter' type='search' placeholder='Filter rows' aria-label='Filter rows'>" +
             _download_link(download_url) + _reader_scheme() + _source_button(source_url))
    stage = ("<div class='pl-table-wrap'><table class='pl-table' id='table'><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>"
             % (ths, trs))
    foot = ("<span id='count'>%d rows &times; %d columns &middot; %s separated &middot; %s%s%s</span>"
            % (len(body), len(head), sep, _size(size), " &middot; " + escape("; ".join(notes)) if notes else "",
               _cut_note(shown, size, truncated, download_url)))
    return _viewer(ctx, title, back_url, tools=tools, stage=stage, foot=foot, classes="pl-tableview",
                   stage_class="pl-table-stage", scripts=(ctx.urls.static("viewer-table.js"),))


def zip_page(ctx: Ctx, title: str, *, root: str, parts: tuple, entries: list, total: int, comment: str, size: int,
             download_url: str, back_url: str) -> str:
    """The contents of a zip file: every entry with its size, and View / Download where they are possible."""
    urls = ctx.urls
    rows = []
    for e in entries:
        name = escape(e["name"])
        if e["is_dir"]:
            rows.append("<tr class='pl-zip-dir'><td colspan='3'><span class='pl-kind'>%s</span>%s</td><td></td></tr>" % (ROOT_ICON, name))
            continue
        if e["encrypted"]:
            action = "<span class='pl-muted'>password protected</span>"
        else:
            kind = kinds.kind_of(e["name"])
            view = ""
            if kind == "image":
                view = "<a href='%s'>View</a> &middot; " % escape(urls.zip_entry(root, parts, e["index"], "raw"))
            elif kind in ("text", "markdown", "table"):
                view = "<a href='%s'>View</a> &middot; " % escape(urls.zip_entry(root, parts, e["index"], "text"))
            action = "%s<a href='%s'>Download</a>" % (view, escape(urls.zip_entry(root, parts, e["index"], "dl")))
        rows.append("<tr><td class='pl-zip-name'>%s</td><td class='pl-num'>%s</td><td>%s</td><td class='pl-zip-actions'>%s</td></tr>"
                    % (name, _size(e["size"]), escape(e["date"]), action))
    cut = ("<p class='pl-muted'>Only the first %d of %d entries are listed.</p>" % (len(entries), total)
           if total > len(entries) else "")
    note = "<p class='pl-muted'>%s</p>" % escape(comment) if comment else ""
    stage = ("<div class='pl-zip-wrap'>%s%s<table class='pl-zip'><thead><tr><th>Name</th><th class='pl-num'>Size</th>"
             "<th>Modified</th><th></th></tr></thead><tbody>%s</tbody></table>%s</div>" % (note, "", "".join(rows), cut))
    tools = _download_link(download_url) + _reader_scheme()
    foot = "<span>Zip file &middot; %s &middot; %d entr%s</span>" % (_size(size), total, "y" if total == 1 else "ies")
    return _viewer(ctx, title, back_url, tools=tools, stage=stage, foot=foot, classes="pl-zipview",
                   stage_class="pl-zip-stage")


VIEW_STYLES = (("list", "A list"), ("grid", "Covers (a picture for each book)"))
EXTRAS_STYLES = (("show", "Show"), ("hide", "Hide"))


def settings_page(ctx: Ctx, themes: list, saved: bool = False, stations: bool = False, streams: bool = False) -> str:
    opts = "".join("<option value='%s'%s>%s</option>"
                   % (escape(t), " selected" if t == ctx.theme else "", escape(t)) for t in themes)
    icon_opts = "".join("<option value='%s'%s>%s</option>" % (k, " selected" if k == ctx.icons else "", escape(label))
                        for k, label in ICON_STYLES)
    view_opts = "".join("<option value='%s'%s>%s</option>" % (k, " selected" if k == ctx.view else "", escape(label))
                        for k, label in VIEW_STYLES)
    extras_opts = "".join("<option value='%s'%s>%s</option>" % (k, " selected" if (k == "show") == ctx.extras else "", label)
                           for k, label in EXTRAS_STYLES)
    note = "<p class='pl-muted'>Saved.</p>" if saved else ""
    body = ("<main class='pl-main'><h1>Settings</h1>%s<form class='pl-card' method='post' action='%s'>"
            "<label class='pl-field'><span>Theme</span><select name='theme'>%s</select></label>"
            "<label class='pl-field'><span>File icons</span><select name='icons'>%s</select></label>"
            "%s"
            "<label class='pl-field'><span>Speed and sleep controls in the music player</span><select name='extras'>%s</select></label>"
            "<label class='pl-field'><span>Live TV: when I leave the channel page</span><select name='tv_sound'>%s</select></label>"
            "<label class='pl-field'><span>Play all and Shuffle on a folder</span><select name='queue_subfolders'>"
            "<option value='yes'%s>Include the folders below it</option><option value='no'%s>Only the files in that folder</option></select></label>"
            "<button class='pl-btn pl-btn-primary' type='submit'>Save</button></form>%s"
            "<p><a href='%s'>&larr; Back to the library</a></p></main>"
            % (note, escape(ctx.urls.settings()), opts, icon_opts,
               ("<label class='pl-field'><span>Folders are shown as</span><select name='view'>%s</select></label>"
                "<label class='pl-field'><span>A List or Covers choice on a folder page holds for</span><select name='view_scope'>"
                "<option value='all'%s>Every folder</option><option value='folder'%s>That folder only (the choice above is the start)</option></select></label>"
                "<p><button class='pl-btn' type='submit' name='reset_views' value='1' data-confirm='Forget every List or Covers choice you made for a single folder?'>"
                "Forget my choices for single folders</button></p>"
                % (view_opts, " selected" if ctx.view_scope == "all" else "", " selected" if ctx.view_scope == "folder" else "")) if ctx.thumbs else "",
               extras_opts,
               "".join("<option value='%s'%s>%s</option>" % (k, " selected" if (k == "keep") == ctx.tv_sound else "", label)
                       for k, label in (("keep", "Keep the sound playing in the player bar"), ("stop", "Stop it"))),
               " selected" if ctx.queue_subfolders else "", "" if ctx.queue_subfolders else " selected",
               ("<section class='pl-card'><h2>Radio and TV</h2><p class='pl-muted'>Find stations and channels in public catalogues and save them "
                "into your upload folder, where pluteus plays them (and shows them to music apps as radio).</p>"
                "<p><a class='pl-btn' href='%s'>Find radio stations</a> <a class='pl-btn' href='%s'>Find TV channels</a>%s</p></section>"
                % (escape(ctx.urls.stations("radio")), escape(ctx.urls.stations("tv")),
                   (" <a class='pl-btn' href='%s'>My streams</a>" % escape(ctx.urls.my_streams())) if streams else "")) if stations else "",
               escape(ctx.urls.home())))
    body = body.replace("<p><a href='%s'>&larr; Back to the library</a></p></main>" % escape(ctx.urls.home()),
                        "<section class='pl-card'><h2>TV mode</h2><p class='pl-muted'>For a browser on a TV: larger buttons, and the arrow keys of the remote "
                        "move between them (Enter presses, Back goes back). It is kept in this browser only, so your phone is not changed.</p>"
                        "<p><a class='pl-btn' target='_top' href='%s?tv=1'>Turn on for this device</a> "
                        "<a class='pl-btn' target='_top' href='%s?tv=0'>Turn off</a></p></section>"
                        "<p><a href='%s'>&larr; Back to the library</a></p><p class='pl-version pl-muted'>pluteus %s</p></main>"
                        % (escape(ctx.urls.shell()), escape(ctx.urls.shell()), escape(ctx.urls.home()), escape(__version__)), 1)
    return layout("Settings", body, ctx)

"""HTML pages. Every page shares one layout that links base.css, the default theme and the user's theme."""
from __future__ import annotations

import time
from dataclasses import dataclass
from html import escape

from . import __version__, kinds, mediainfo
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


def layout(title: str, body: str, ctx: Ctx, *, head: str = "", scripts: tuple = (), bar: bool = True,
           body_class: str = "", module_scripts: tuple = ()) -> str:
    u = ctx.urls
    css = ["<link rel='stylesheet' href='%s'>" % escape(u.static("base.css")),
           "<link rel='stylesheet' href='%s'>" % escape(u.theme(DEFAULT, "theme.css"))]
    if ctx.theme != DEFAULT:
        css.append("<link rel='stylesheet' href='%s'>" % escape(u.theme(ctx.theme, "theme.css")))
    chrome = ""
    if bar:
        who = nav = ""
        if ctx.user:
            # Laid out like the admin page's bar: brand, version and the page links on the left; contrast button, the link
            # to the admin page (an administrator on the local network only) and Sign out on the right.
            nav = "<nav class='pl-admin-nav'><a class='pl-admin-tab' href='%s'>Settings</a></nav>" % escape(u.settings())
            admin = "<a href='%s'>Admin</a>" % escape(ctx.admin_link) if ctx.admin_link else ""
            who = (admin + "<form method='post' action='%s'><button class='pl-btn' type='submit'>Sign out<span class='pl-who'> (%s)</span></button></form>"
                   % (escape(u.logout()), escape(ctx.user)))
        chrome = ("<header class='pl-bar'><a class='pl-brand' href='%s'>Pluteus</a> <span class='pl-muted pl-ver'>%s</span>%s"
                  "<span class='pl-spacer'></span>%s%s</header>"
                  % (escape(u.home()), escape(__version__), nav, scheme_button(), who))
    js = "".join("<script src='%s' defer></script>" % escape(s) for s in scripts)
    js += "".join("<script type='module' src='%s'></script>" % escape(s) for s in module_scripts)
    # scheme.js is NOT deferred: it must run before the first paint so a saved dark/light choice never flashes.
    return ("<!doctype html><html lang='en' data-icons='%s'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>%s</title>%s%s<script src='%s'></script>%s%s</head>"
            "<body class='%s'>%s%s%s</body></html>"
            % (escape(ctx.icons), escape(title), icon_links(u), "".join(css), escape(u.static("scheme.js")), head, js,
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


def roots_page(roots: list, ctx: Ctx, recent: list | None = None) -> str:
    items = "".join("<li class='pl-row'><a class='pl-root-link' href='%s'><span class='pl-kind'>%s</span>%s</a></li>"
                    % (escape(ctx.urls.browse(r.name)), UPLOADS_ICON if r.name == "uploads" else ROOT_ICON, escape(r.name))
                    for r in roots)
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
    body = ("<main class='pl-main'><h1>File Library</h1>%s"
            "<section class='pl-card pl-home-box' aria-label='Browse'><h2>Browse</h2>"
            "<ul class='pl-list pl-roots'>%s</ul></section>"
            "<p class='pl-muted pl-foot'><a href='%s'>OPDS feed</a> for reader apps &middot; pluteus %s</p></main>"
            % (cont, items, escape(ctx.urls.opds()), escape(__version__)))
    return layout("File Library", body, ctx)


def _book_label(entry, meta: dict) -> str:
    if meta.get("title"):
        by = " &ndash; " + escape(", ".join(meta["authors"])) if meta.get("authors") else ""
        series = (" <span class='pl-muted'>(%s%s)</span>" % (
            escape(meta["series"]), " #" + escape(meta["series_index"]) if meta.get("series_index") else "")
            if meta.get("series") else "")
        number = "<span class='pl-muted'>%02d</span> " % meta["track"] if meta.get("track") else ""
        return number + escape(meta["title"]) + by + series
    return escape(entry.name)


UPLOAD_MESSAGES = {
    "toobig": "is larger than the size limit", "quota": "does not fit in your space", "disk": "the disk is full",
    "name": "has no usable name", "exists": "that name is already taken", "toomany": "too many files at once",
    "write": "could not be written", "notempty": "the folder is not empty", "missing": "it no longer exists",
    "format": "the upload was not understood",
}
OK_MESSAGES = {"folder": "Folder created.", "renamed": "Renamed.", "deleted": "Deleted."}


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


def _query(sort: str, desc: bool, page: int = 1) -> str:
    """?sort=..&dir=..&page=.. with only the non-default parts (the default listing keeps its plain URL)."""
    pairs = []
    if sort != "name" or desc:
        pairs += ["sort=" + sort] + (["dir=desc"] if desc else [])
    if page > 1:
        pairs.append("page=%d" % page)
    return "?" + "&".join(pairs) if pairs else ""


def _sort_bar(here: str, sort: str, desc: bool) -> str:
    items = []
    for key, label in SORT_LABELS:
        if key == sort:                                       # the current column: its link flips the direction
            arrow = " &#9660;" if desc else " &#9650;"
            href, cls = here + _query(key, not desc), " class='pl-sort-on'"
        else:                                                 # a new column: newest first for dates, else ascending
            arrow, cls, href = "", "", here + _query(key, key == "date")
        items.append("<a%s href='%s'>%s%s</a>" % (cls, escape(href), label, arrow))
    return "<nav class='pl-sortbar' aria-label='Sort this folder'><span class='pl-muted'>Sort:</span> %s</nav>" % " ".join(items)


def listing_page(root: str, parts: tuple, entries: list, page: int, pages: int, total: int, ctx: Ctx,
                 metas: dict | None = None, progress: dict | None = None,
                 sort: str = "name", desc: bool = False, writable: bool = False, flash: str = "", cover_url: str = "") -> str:
    metas = metas or {}
    progress = progress or {}
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
            rows.append("<li class='pl-row pl-folder'><a class='pl-title' href='%s'><span class='pl-kind'>%s</span>%s</a>%s</li>"
                        % (escape(urls.browse(root, e.parts)), ROOT_ICON, escape(e.display),
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
                    "<span class='pl-meta pl-muted'>%s%s &middot; %s</span>%s</li>"
                    % (escape(urls.download(root, e.parts)), escape(e.name), escape(open_url),
                       kind_icon(kinds.kind_of(e.name)), label, badge,
                       _size(e.size), time.strftime("%Y-%m-%d", time.localtime(e.mtime)),
                       _row_actions(urls, root, e.parts, e.name) if writable else ""))
    here = urls.browse(root, parts)
    pager = ""
    if pages > 1:
        prev = ("<a href='%s'>&larr; Previous</a>" % escape(here + _query(sort, desc, page - 1))) if page > 1 else ""
        nxt = ("<a href='%s'>Next &rarr;</a>" % escape(here + _query(sort, desc, page + 1))) if page < pages else ""
        pager = "<div class='pl-pager'>%s<span class='pl-muted'>Page %d of %d</span>%s</div>" % (prev, page, pages, nxt)
    title = parts[-1] if parts else root
    bar = _sort_bar(here, sort, desc) if total > 1 else ""
    note = "<p class='pl-flash' role='status'>%s</p>" % escape(flash) if flash else ""
    forms = _upload_forms(urls, root, parts) if writable else ""
    picture = ("<img class='pl-folder-cover' src='%s' alt='Cover of this folder' loading='lazy'>" % escape(cover_url)
               if cover_url else "")
    body = ("<main class='pl-main'><div class='pl-crumbs'>%s</div>%s<h1>%s</h1><div class='pl-muted'>%d items &middot; "
            "<a href='%s'>OPDS</a></div>%s%s<ul class='pl-list'>%s</ul>%s</main>"
            % (" / ".join(crumbs), picture, escape(title), total, escape(urls.opds(root, parts)),
               note + forms, bar + pager, "".join(rows), pager))
    return layout(title, body, ctx, scripts=(urls.static("uploads.js"),) if writable else ())


def _icon_button(ident: str, icon: str, label: str, extra: str = "") -> str:
    return ("<button class='pl-reader-btn %s' id='%s' type='button' title='%s' aria-label='%s'>"
            "<span class='pl-icon pl-i-%s'></span></button>" % (extra, ident, escape(label), escape(label), icon))


def epub_reader_page(ctx: Ctx, title: str, subtitle: str, *, book_url: str, progress_url: str, back_url: str,
                     content_css_url: str, key: str) -> str:
    """The EPUB reader shell. The page is styled by the theme; epub.js and reader-epub.js do the rest."""
    u = ctx.urls
    data = " ".join("data-%s='%s'" % (k, escape(v)) for k, v in (
        ("book", book_url), ("progress", progress_url), ("content-css", content_css_url), ("key", key)))
    body = (
        "<div class='pl-reader' id='reader' %s>"
        "<header class='pl-reader-bar'>"
        "<a class='pl-reader-btn' id='back' href='%s' title='Back to the folder' aria-label='Back to the folder'>"
        "<span class='pl-icon pl-i-back'></span></a>"
        "%s<div class='pl-reader-title' title='%s'>%s</div>%s%s%s</header>"
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
        % (data, escape(back_url),
           _icon_button("toc-btn", "menu", "Contents"), escape(subtitle), escape(title),
           "<button class='pl-reader-btn pl-scheme' type='button' title='Toggle dark / light' aria-label='Toggle dark / light'>"
           "<span class='pl-icon pl-i-contrast'></span></button>",
           _icon_button("settings-btn", "settings", "Reading settings"),
           _icon_button("fs-btn", "fullscreen", "Full screen"),
           _icon_button("prev", "prev", "Previous page"), _icon_button("next", "next", "Next page")))
    return layout(title, body, ctx, bar=False, body_class="pl-reader-page",
                  scripts=(u.static("vendor/jszip.min.js"), u.static("vendor/epub.min.js"), u.static("reader-epub.js"),
                           u.static("menus-back.js")))


def comic_reader_page(ctx: Ctx, title: str, *, pages_url: str, count: int, progress_url: str, back_url: str,
                      key: str) -> str:
    """The single-page comic reader shell; reader-comic.js fetches the pages one by one."""
    data = " ".join("data-%s='%s'" % (k, escape(str(v))) for k, v in (
        ("pages", pages_url), ("count", count), ("progress", progress_url), ("key", key)))
    body = (
        "<div class='pl-reader pl-comic' id='reader' %s>"
        "<header class='pl-reader-bar'>"
        "<a class='pl-reader-btn' id='back' href='%s' title='Back to the folder' aria-label='Back to the folder'>"
        "<span class='pl-icon pl-i-back'></span></a>"
        "<div class='pl-reader-title' title='%s'>%s</div>%s%s%s</header>"
        "<div class='pl-reader-stage pl-comic-stage pl-fit-page' id='stage'>"
        "<img class='pl-page' id='page' alt='' draggable='false'>"
        "<div class='pl-tap pl-tap-left' id='tap-prev'></div><div class='pl-tap pl-tap-right' id='tap-next'></div>"
        "<div class='pl-toast' id='status'>Loading&hellip;</div></div>"
        "<footer class='pl-reader-foot'>%s<div class='pl-progress' id='seek' title='Jump to a page'><i id='bar'></i></div>"
        "<span id='pct'>&ndash;</span>%s</footer></div>"
        % (data, escape(back_url), escape(title), escape(title),
           "<button class='pl-reader-btn pl-scheme' type='button' title='Toggle dark / light' aria-label='Toggle dark / light'>"
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
            "<a class='pl-reader-btn' id='back' href='%s' title='Back to the folder' aria-label='Back to the folder'>"
            "<span class='pl-icon pl-i-back'></span></a>"
            "<div class='pl-reader-title' title='%s'>%s</div>%s</header>"
            "<div class='pl-reader-stage %s'>%s</div><footer class='pl-reader-foot'>%s</footer></div>"
            % (classes, attrs, escape(back_url), escape(title), escape(title), tools, stage_class, stage, foot))
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
    stage = "<pre class='pl-text' id='text' tabindex='0'>%s</pre>" % escape(text)
    foot = "<span>%s &middot; %s &middot; %d lines%s</span>" % (escape(encoding), _size(size), lines, note)
    return _viewer(ctx, title, back_url, tools=tools, stage=stage, foot=foot, classes="pl-textview",
                   scripts=(ctx.urls.static("viewer-text.js"),))


def _lyrics_html(synced: list, plain: str) -> str:
    """The lyrics panel: timed lines (each knows its second, the script highlights the current one) or the plain text."""
    if synced:
        lines = "".join("<p data-t='%s'>%s</p>" % (t, escape(words) or "&nbsp;") for t, words in synced)
        return "<div class='pl-lyrics pl-lyrics-synced' id='lyrics' hidden>%s</div>" % lines
    if plain:
        return "<div class='pl-lyrics pl-lyrics-plain' id='lyrics' hidden>%s</div>" % escape(plain)
    return ""


def media_viewer_page(ctx: Ctx, title: str, *, kind: str, raw_url: str, mime: str, download_url: str, back_url: str,
                      progress_url: str, prev_url, next_url, position: int, total: int, album: str = "",
                      tags: dict | None = None, cover_url: str = "", synced: list | None = None) -> str:
    """The audio/video viewer. Audio gets a cover, the tags (title, artist, album, year, genre, track), a scrubber, big transport
    buttons and lyrics; video keeps the browser's own controls under the same information. Tags are untrusted text: escaped.
    viewer-media.js adds resume, auto-advance to the next file, the lyrics highlight and the lock-screen controls."""
    tags, synced = tags or {}, synced or []
    tag = "video" if kind == "video" else "audio"
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
    skip_back = button("back30", "Back 30 seconds", "back30", "30 s")
    skip_fwd = button("fwd30", "Forward 30 seconds", "fwd30", "30 s", after=True)
    speed = button("speed", "Playback speed", "speed", "1&times;")
    sleep = button("sleep", "Sleep timer", "moon", "Sleep")
    words_button = ("<button class='pl-btn' id='lyrics-btn' type='button' aria-label='Show the lyrics' aria-expanded='false'>"
                    "<span class='pl-btn-text'>Lyrics</span></button>" if lyrics else "")
    info = ("<div class='pl-track'><h1 class='pl-track-title'>%s</h1>%s%s</div>"
            % (escape(heading), "<div class='pl-track-by'>%s</div>" % by if by else "",
               "<div class='pl-chips'>%s</div>" % chips if chips else ""))
    if kind == "video":
        stage = ("<div class='pl-media-box'>%s<video id='media' controls preload='metadata' playsinline src='%s'>"
                 "<p>Your browser cannot play this file; <a href='%s'>download it</a>.</p></video>"
                 "<div class='pl-media-controls'>%s%s%s%s</div>%s<div class='pl-toast' id='status' hidden></div></div>"
                 % (info, escape(raw_url), escape(download_url), skip_back, speed, sleep, skip_fwd, lyrics))
    else:
        picture = ("<img class='pl-cover-img' src='%s' alt='Cover'>" % escape(cover_url) if cover_url
                   else "<div class='pl-cover-blank'>%s</div>" % ico("audio"))
        backdrop = "<img class='pl-bg' src='%s' alt=''>" % escape(cover_url) if cover_url else ""
        stage = ("<div class='pl-media-box pl-player'>%s<div class='pl-cover'>%s</div>%s"
                 "<audio id='media' controls preload='metadata' src='%s'><p>Your browser cannot play this file; "
                 "<a href='%s'>download it</a>.</p></audio>"
                 "<div class='pl-scrub'><span id='cur'>0:00</span><input type='range' id='scrub' min='0' max='1000' value='0' "
                 "aria-label='Position'><span id='dur'>&ndash;:&ndash;&ndash;</span></div>"
                 "<div class='pl-transport'>%s%s%s</div><div class='pl-media-controls'>%s%s%s</div>%s"
                 "<div class='pl-toast' id='status' hidden></div></div>"
                 % (backdrop, picture, info, escape(raw_url), escape(download_url), skip_back,
                    button("play", "Play", "play", extra="pl-play"), skip_fwd, speed, sleep, words_button, lyrics))
    foot = ("%s<span style='flex:1'></span><span>%d / %d</span>%s"
            % (_nav_link("prev", "prev", "Previous file", prev_url), position, total,
               _nav_link("next", "next", "Next file", next_url)))
    tools = _download_link(download_url) + _reader_scheme()
    bar_title = " – ".join(x for x in (heading, artist, album_name) if x)
    return _viewer(ctx, bar_title, back_url, tools=tools, stage=stage, foot=foot, classes="pl-mediaview pl-" + tag,
                   stage_class="pl-media-stage",
                   data=(("progress", progress_url), ("kind", kind), ("prev", prev_url or ""), ("next", next_url or ""),
                         ("title", heading), ("artist", artist), ("album", album_name),
                         ("artwork", cover_url or ctx.urls.static("icon/icon-512.png"))),
                   scripts=(ctx.urls.static("viewer-media.js"),))


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
                  back_url: str, source_url: str) -> str:
    """A Markdown file rendered as a page. `html` comes from markdown.render (raw HTML already escaped)."""
    tools = _download_link(download_url) + _reader_scheme() + _source_button(source_url)
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


def settings_page(ctx: Ctx, themes: list, saved: bool = False) -> str:
    opts = "".join("<option value='%s'%s>%s</option>"
                   % (escape(t), " selected" if t == ctx.theme else "", escape(t)) for t in themes)
    icon_opts = "".join("<option value='%s'%s>%s</option>" % (k, " selected" if k == ctx.icons else "", escape(label))
                        for k, label in ICON_STYLES)
    note = "<p class='pl-muted'>Saved.</p>" if saved else ""
    body = ("<main class='pl-main'><h1>Settings</h1>%s<form class='pl-card' method='post' action='%s'>"
            "<label class='pl-field'><span>Theme</span><select name='theme'>%s</select></label>"
            "<label class='pl-field'><span>File icons</span><select name='icons'>%s</select></label>"
            "<button class='pl-btn pl-btn-primary' type='submit'>Save</button></form>"
            "<p><a href='%s'>&larr; Back to the library</a></p></main>"
            % (note, escape(ctx.urls.settings()), opts, icon_opts, escape(ctx.urls.home())))
    return layout("Settings", body, ctx)

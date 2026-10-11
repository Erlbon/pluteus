"""HTML pages. Every page shares one layout that links base.css, the default theme and the user's theme."""
from __future__ import annotations

from dataclasses import dataclass
from html import escape

from .themes import DEFAULT
from .urls import Urls


@dataclass
class Ctx:
    urls: Urls
    user: str = ""
    theme: str = DEFAULT
    readers: bool = False        # show "Read" links (the in-browser readers)


def layout(title: str, body: str, ctx: Ctx, *, head: str = "", scripts: tuple = (), bar: bool = True,
           body_class: str = "", module_scripts: tuple = ()) -> str:
    u = ctx.urls
    css = ["<link rel='stylesheet' href='%s'>" % escape(u.static("base.css")),
           "<link rel='stylesheet' href='%s'>" % escape(u.theme(DEFAULT, "theme.css"))]
    if ctx.theme != DEFAULT:
        css.append("<link rel='stylesheet' href='%s'>" % escape(u.theme(ctx.theme, "theme.css")))
    chrome = ""
    if bar:
        who = ""
        if ctx.user:
            who = ("<a href='%s'>Settings</a>"
                   "<form method='post' action='%s'><button class='pl-btn' type='submit'>Sign out (%s)</button></form>"
                   % (escape(u.settings()), escape(u.logout()), escape(ctx.user)))
        chrome = ("<header class='pl-bar'><a class='pl-brand' href='%s'>pluteus</a><span class='pl-spacer'></span>%s%s</header>"
                  % (escape(u.home()), scheme_button(), who))
    js = "".join("<script src='%s' defer></script>" % escape(s) for s in scripts)
    js += "".join("<script type='module' src='%s'></script>" % escape(s) for s in module_scripts)
    # scheme.js is NOT deferred: it must run before the first paint so a saved dark/light choice never flashes.
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>%s</title>%s%s<script src='%s'></script>%s%s</head>"
            "<body class='%s'>%s%s</body></html>"
            % (escape(title), icon_links(u), "".join(css), escape(u.static("scheme.js")), head, js,
               escape(body_class), chrome, body))


def icon_links(u: Urls) -> str:
    """Browser tab icon, home-screen icon and web manifest (Android 'add to home screen')."""
    return ("<link rel='icon' href='%s' sizes='any'><link rel='icon' type='image/png' sizes='32x32' href='%s'>"
            "<link rel='apple-touch-icon' href='%s'><link rel='manifest' href='%s'><meta name='theme-color' content='#14171f'>"
            % (escape(u.static("icon/favicon.ico")), escape(u.static("icon/favicon-32.png")),
               escape(u.static("icon/apple-touch-icon.png")), escape(u.static("manifest.webmanifest"))))


def scheme_button() -> str:
    return ("<button class='pl-btn pl-scheme' type='button' title='Toggle dark / light' "
            "aria-label='Toggle dark / light'><span class='pl-icon pl-i-contrast'></span></button>")


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%d %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024


def login_page(ctx: Ctx, next_url: str = "", error: str = "", username: str = "") -> str:
    err = "<p class='pl-error' role='alert'>%s</p>" % escape(error) if error else ""
    body = ("<main class='pl-main'><form class='pl-card pl-login' method='post' action='%s'>"
            "<div class='pl-login-tools'>%s</div><div class='pl-logo'></div>"
            "<h1>Sign in</h1>%s"
            "<label class='pl-field'><span>User name</span>"
            "<input type='text' name='username' value='%s' autocomplete='username' autocapitalize='off' autofocus required></label>"
            "<label class='pl-field'><span>Password</span>"
            "<input type='password' name='password' autocomplete='current-password' required></label>"
            "<input type='hidden' name='next' value='%s'>"
            "<button class='pl-btn pl-btn-primary' type='submit'>Sign in</button></form></main>"
            % (escape(ctx.urls.login()), scheme_button(), err, escape(username), escape(next_url)))
    return layout("Sign in", body, ctx, bar=False, body_class="pl-login-page")


def roots_page(roots: list, ctx: Ctx, recent: list | None = None) -> str:
    items = "".join("<li class='pl-row'><a href='%s'>%s</a></li>"
                    % (escape(ctx.urls.browse(r.name)), escape(r.name)) for r in roots)
    # (the two lists below carry .pl-roots / .pl-recent so a theme can style them as cards)
    cont = ""
    if recent:
        rows = "".join("<li class='pl-row'><a href='%s'>%s</a><span class='pl-badge'>%d%%</span></li>"
                       % (escape(href), escape(label), round(pct * 100)) for href, label, pct in recent)
        cont = "<h2>Continue reading</h2><ul class='pl-list pl-recent'>%s</ul>" % rows
    body = ("<main class='pl-main'><h1>Library</h1>"
            "<div class='pl-muted'><a href='%s'>OPDS catalogue</a> for reader apps</div>%s"
            "<h2>Browse</h2><ul class='pl-list pl-roots'>%s</ul></main>"
            % (escape(ctx.urls.opds()), cont, items))
    return layout("Library", body, ctx)


def _book_label(entry, meta: dict) -> str:
    if meta.get("title"):
        by = " &ndash; " + escape(", ".join(meta["authors"])) if meta.get("authors") else ""
        series = (" <span class='pl-muted'>(%s%s)</span>" % (
            escape(meta["series"]), " #" + escape(meta["series_index"]) if meta.get("series_index") else "")
            if meta.get("series") else "")
        return escape(meta["title"]) + by + series
    return escape(entry.name)


def listing_page(root: str, parts: tuple, entries: list, page: int, pages: int, total: int, ctx: Ctx,
                 metas: dict | None = None, progress: dict | None = None) -> str:
    metas = metas or {}
    progress = progress or {}
    urls = ctx.urls
    crumbs = ["<a href='%s'>Library</a>" % escape(urls.home()),
              "<a href='%s'>%s</a>" % (escape(urls.browse(root)), escape(root))]
    for i, seg in enumerate(parts):
        crumbs.append("<a href='%s'>%s</a>" % (escape(urls.browse(root, parts[:i + 1])), escape(seg)))
    rows = []
    for e in entries:
        if e.is_dir:
            rows.append("<li class='pl-row'><a class='pl-title' href='%s'>&#128193; %s</a></li>"
                        % (escape(urls.browse(root, e.parts)), escape(e.display)))
            continue
        label = _book_label(e, metas.get(e.path, {}))
        pct = progress.get("/".join(e.parts))
        badge = "<span class='pl-badge'>%d%%</span> " % round(pct * 100) if pct else ""
        read = ""
        if ctx.readers:
            if e.name.lower().endswith((".epub", ".cbz")):
                read = "<a href='%s'>Read</a> &middot; " % escape(urls.read(root, e.parts))
            else:
                read = "<a href='%s'>View</a> &middot; " % escape(urls.view(root, e.parts))
        rows.append("<li class='pl-row'><span class='pl-title'>%s</span><span class='pl-muted'>%s%s"
                    "<a href='%s'>Download</a> &middot; %s</span></li>"
                    % (label, badge, read, escape(urls.download(root, e.parts)), _size(e.size)))
    here = urls.browse(root, parts)
    pager = ""
    if pages > 1:
        prev = "<a href='%s?page=%d'>&larr; Previous</a>" % (escape(here), page - 1) if page > 1 else ""
        nxt = "<a href='%s?page=%d'>Next &rarr;</a>" % (escape(here), page + 1) if page < pages else ""
        pager = "<div class='pl-pager'>%s<span class='pl-muted'>Page %d of %d</span>%s</div>" % (prev, page, pages, nxt)
    title = parts[-1] if parts else root
    body = ("<main class='pl-main'><div class='pl-crumbs'>%s</div><h1>%s</h1><div class='pl-muted'>%d items &middot; "
            "<a href='%s'>OPDS</a></div>%s<ul class='pl-list'>%s</ul>%s</main>"
            % (" / ".join(crumbs), escape(title), total, escape(urls.opds(root, parts)),
               pager, "".join(rows), pager))
    return layout(title, body, ctx)


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
        "<option value='scrolled-doc'>Scrolling</option></select></label></aside>"
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
                  scripts=(u.static("vendor/jszip.min.js"), u.static("vendor/epub.min.js"), u.static("reader-epub.js")))


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
           _icon_button("fit-btn", "fit", "Fit to width / fit to page"),
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


def media_viewer_page(ctx: Ctx, title: str, *, kind: str, raw_url: str, mime: str, download_url: str, back_url: str,
                      progress_url: str, prev_url, next_url, position: int, total: int) -> str:
    tag = "video" if kind == "video" else "audio"
    stage = ("<div class='pl-media-box'><div class='pl-media-title'>%s</div>"
             "<%s id='media' controls preload='metadata' %s src='%s'><p>Your browser cannot play this file; "
             "<a href='%s'>download it</a>.</p></%s></div>"
             % (escape(title), tag, "playsinline" if kind == "video" else "", escape(raw_url), escape(download_url), tag))
    foot = ("%s<span style='flex:1'></span><span>%d / %d</span>%s"
            % (_nav_link("prev", "prev", "Previous file", prev_url), position, total,
               _nav_link("next", "next", "Next file", next_url)))
    tools = _download_link(download_url) + _reader_scheme()
    return _viewer(ctx, title, back_url, tools=tools, stage=stage, foot=foot, classes="pl-mediaview",
                   stage_class="pl-media-stage", data=(("progress", progress_url), ("kind", kind)),
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


def settings_page(ctx: Ctx, themes: list, saved: bool = False) -> str:
    opts = "".join("<option value='%s'%s>%s</option>"
                   % (escape(t), " selected" if t == ctx.theme else "", escape(t)) for t in themes)
    note = "<p class='pl-muted'>Saved.</p>" if saved else ""
    body = ("<main class='pl-main'><h1>Settings</h1>%s<form class='pl-card' method='post' action='%s'>"
            "<label class='pl-field'><span>Theme</span><select name='theme'>%s</select></label>"
            "<button class='pl-btn pl-btn-primary' type='submit'>Save</button></form>"
            "<p><a href='%s'>&larr; Back to the library</a></p></main>"
            % (note, escape(ctx.urls.settings()), opts, escape(ctx.urls.home())))
    return layout("Settings", body, ctx)

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
           body_class: str = "") -> str:
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
        if ctx.readers and e.name.lower().endswith((".epub", ".cbz")):
            read = "<a href='%s'>Read</a> &middot; " % escape(urls.read(root, e.parts))
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

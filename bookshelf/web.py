"""Plain HTML pages for browsing in a normal web browser."""
from __future__ import annotations

from html import escape

_STYLE = """
:root { color-scheme: light dark; --dim:#6b7385; --line:#8884; }
body { font:15px/1.5 system-ui, sans-serif; margin:0 auto; padding:16px; max-width:900px; }
h1 { font-size:20px; margin:0 0 4px; }
.crumbs, .meta { color:var(--dim); font-size:13px; }
ul { list-style:none; padding:0; margin:12px 0; }
li { padding:6px 0; border-bottom:1px solid var(--line); display:flex; gap:10px; justify-content:space-between; }
a { text-decoration:none; } a:hover { text-decoration:underline; }
.pager { display:flex; gap:16px; margin:12px 0; }
"""


def _page(title: str, body: str) -> str:
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>%s</title><style>%s</style></head><body>%s</body></html>"
            % (escape(title), _STYLE, body))


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%d %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024


def roots_page(roots: list, urls, user_name: str) -> str:
    items = "".join("<li><a href='%s'>%s</a></li>" % (escape(urls.browse(r.name)), escape(r.name))
                    for r in roots)
    body = ("<h1>Library</h1><div class='meta'>Signed in as %s &middot; "
            "<a href='%s'>OPDS catalogue</a></div><ul>%s</ul>"
            % (escape(user_name), escape(urls.opds()), items))
    return _page("Library", body)


def listing_page(root: str, parts: tuple, entries: list, page: int, pages: int, total: int, urls) -> str:
    crumbs = ["<a href='%s'>Library</a>" % escape(urls.home()),
              "<a href='%s'>%s</a>" % (escape(urls.browse(root)), escape(root))]
    for i, seg in enumerate(parts):
        crumbs.append("<a href='%s'>%s</a>" % (escape(urls.browse(root, parts[:i + 1])), escape(seg)))
    rows = []
    for e in entries:
        if e.is_dir:
            rows.append("<li><a href='%s'>&#128193; %s</a></li>"
                        % (escape(urls.browse(root, e.parts)), escape(e.display)))
        else:
            rows.append("<li><a href='%s'>%s</a><span class='meta'>%s</span></li>"
                        % (escape(urls.download(root, e.parts)), escape(e.name), _size(e.size)))
    here = urls.browse(root, parts)
    pager = ""
    if pages > 1:
        prev = "<a href='%s?page=%d'>&larr; Previous</a>" % (escape(here), page - 1) if page > 1 else ""
        nxt = "<a href='%s?page=%d'>Next &rarr;</a>" % (escape(here), page + 1) if page < pages else ""
        pager = "<div class='pager'>%s<span class='meta'>Page %d of %d</span>%s</div>" % (prev, page, pages, nxt)
    title = parts[-1] if parts else root
    body = ("<div class='crumbs'>%s</div><h1>%s</h1><div class='meta'>%d items &middot; "
            "<a href='%s'>OPDS</a></div>%s<ul>%s</ul>%s"
            % (" / ".join(crumbs), escape(title), total, escape(urls.opds(root, parts)),
               pager, "".join(rows), pager))
    return _page(title, body)

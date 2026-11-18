"""The e-reader page (/ereader): the library as plain pages for the built-in web browser of an e-reader (a Kobo: Settings, Beta features,
Web browser). Folders open like a drive; tapping a book downloads it, and the Kobo puts it in its library. No scripts, no pictures, big
tap targets, black on white, a few entries per page: e-ink screens refresh slowly and the browser is old.

Signing in is the normal login form, once: the browser keeps the cookie. Downloads are the ordinary /dl/ addresses, with the file name at
the end, which is what makes the Kobo browser offer to download a book.
"""
from __future__ import annotations

from html import escape

PER_PAGE = 30
KOBO_READS = (".epub", ".pdf", ".cbz", ".cbr", ".mobi", ".txt", ".html", ".htm", ".rtf")

STYLE = """
body{margin:0;background:#fff;color:#000;font-family:Georgia,serif;font-size:1.35em;line-height:1.3}
header{border-bottom:3px solid #000;padding:.5em .6em}
header h1{font-size:1.25em;margin:0}
header .where{font-size:.8em;margin-top:.2em}
h2{font-size:1em;margin:.9em .6em .3em;text-transform:uppercase;letter-spacing:.05em}
ul{list-style:none;margin:0;padding:0}
li{border-bottom:1px solid #000}
a{color:#000;text-decoration:none;display:block;padding:.7em .6em}
.dir a:before{content:"\\25B8  "}
.size{float:right;font-size:.75em;padding-left:.5em}
.no{color:#555}
.nav{display:table;width:100%;border-top:3px solid #000;margin-top:.5em}
.nav a{display:table-cell;text-align:center;border-right:1px solid #000;font-weight:bold}
.nav a:last-child{border-right:0}
.empty{padding:.7em .6em}
"""


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return ""


def readable(name: str) -> bool:
    return name.lower().endswith(KOBO_READS)


def page(title: str, where: str, sections: list, nav: list) -> str:
    """sections: [(heading or "", [(kind 'dir'|'book'|'other', label, href, size or None)])]; nav: [(label, href)]."""
    body = []
    for heading, rows in sections:
        if heading:
            body.append("<h2>%s</h2>" % escape(heading))
        if not rows:
            body.append("<p class='empty'>Nothing here.</p>")
            continue
        body.append("<ul>")
        for kind, label, href, size in rows:
            extra = "<span class='size'>%s</span>" % _size(size) if size is not None else ""
            cls = "dir" if kind == "dir" else ("book" if kind == "book" else "no")
            body.append("<li class='%s'><a href='%s'>%s%s</a></li>" % (cls, escape(href, True), extra, escape(label)))
        body.append("</ul>")
    links = "".join("<a href='%s'>%s</a>" % (escape(h, True), escape(t)) for t, h in nav)
    return ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>%s</title><style>%s</style></head><body><header><h1>%s</h1><div class='where'>%s</div></header>%s"
            "%s</body></html>") % (escape(title), STYLE, escape(title), escape(where), "".join(body),
                                   "<div class='nav'>%s</div>" % links if links else "")

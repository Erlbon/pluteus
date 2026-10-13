"""The file-type and folder icons: one small SVG sprite, drawn here, included once in every page and used with <use href="#ico-NAME">.

Three looks share the same shapes (the style is picked with html[data-icons=...], see base.css):
  gold     thin outlines in the theme's accent colour with a soft glow (the default: navy and gold)
  aurora   the same thin outlines, one luminous colour per file type
  color    filled, rounded colour tiles with a white symbol (colour is fixed in the sprite)
The outline sprite draws with currentColor, so a theme restyles it with plain CSS (colour, glow).
"""
from __future__ import annotations

# ---- outline glyphs (24x24, stroke = currentColor), drawn inside a faint rounded tile ------------------------------------
_OUT = {
    "epub": '<path d="M12 8.2c-1.4-1-3-1.5-5-1.5v9.3c2 0 3.6.5 5 1.5 1.4-1 3-1.5 5-1.5V6.7c-2 0-3.6.5-5 1.5zM12 8.2v9.3"/>',
    "comic": '<path d="M6.9 7.4A1.4 1.4 0 0 1 8.3 6h7.4a1.4 1.4 0 0 1 1.4 1.4v5.1a1.4 1.4 0 0 1-1.4 1.4h-4.2l-2.9 2.8v-2.8h-.3a1.4 1.4 0 0 1-1.4-1.4z"/>'
             '<path d="M9.4 9.9h.01M12 9.9h.01M14.6 9.9h.01" stroke-width="2"/>',
    "pdf": '<path d="M8.4 5.6h4.8l3.5 3.5v8.3a.9.9 0 0 1-.9.9H8.4a.9.9 0 0 1-.9-.9V6.5a.9.9 0 0 1 .9-.9zM13.2 5.6v3.5h3.5M9.6 12.5h4.8M9.6 15h3.2"/>',
    "audio": '<path d="M10.3 16.3V8.6l6-1.4v7.6"/><circle cx="8.5" cy="16.4" r="1.8"/><circle cx="14.5" cy="14.8" r="1.8"/>',
    "video": '<path d="M9.7 7.7v8.6l7-4.3z"/>',
    "image": '<circle cx="9.1" cy="9.7" r="1.4"/><path d="M5.9 17.3l4-5 3 3.3 2.2-2.5 3 4.2z"/>',
    "text": '<path d="M7.6 8h8.8M7.6 11.3h8.8M7.6 14.6h8.8M7.6 17.9h5"/>',
    "table": '<rect x="6.3" y="6.8" width="11.4" height="10.6" rx="1.5"/><path d="M6.3 10.3h11.4M6.3 13.9h11.4M11.9 6.8v10.6"/>',
    "archive": '<path d="M6 7.4h12v2.5H6zM7 9.9v7.2a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V9.9M10.3 12.8h3.4"/>',
    "file": '<path d="M8.4 5.6h4.8l3.5 3.5v8.3a.9.9 0 0 1-.9.9H8.4a.9.9 0 0 1-.9-.9V6.5a.9.9 0 0 1 .9-.9zM13.2 5.6v3.5h3.5"/>',
    "upload": '<path d="M12 16.8V8.2M8.7 11.4 12 8.1l3.3 3.3M7.2 17.9h9.6"/>',
}
_TILE = '<rect x="1.8" y="1.8" width="20.4" height="20.4" rx="6.2" fill="currentColor" fill-opacity=".08" stroke-width="1.3"/>'
_OUT_FOLDER = ('<path d="M2.9 7.1a2.2 2.2 0 0 1 2.2-2.2h3.8c.6 0 1.2.3 1.6.7l1.1 1.1h7.3a2.2 2.2 0 0 1 2.2 2.2v8.9a2.2 2.2 0 0 1-2.2 2.2H5.1a2.2 2.2 0 0 1-2.2-2.2z" '
               'fill="currentColor" fill-opacity=".1" stroke-width="1.5"/><path d="M3 9.8h18" stroke-width="1" stroke-opacity=".6"/>')


def _outline_symbol(name: str, glyph: str) -> str:
    return ('<symbol id="ico-%s" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
            '%s%s</symbol>' % (name, _TILE, glyph))


OUTLINE_SPRITE = ('<svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false"><defs>'
                  + "".join(_outline_symbol(n, g) for n, g in _OUT.items())
                  + '<symbol id="ico-folder" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">%s</symbol>' % _OUT_FOLDER
                  + "</defs></svg>")

# ---- filled colour tiles ----------------------------------------------------------------------------------------------------
_TILES = [
    ("epub", "#5b9bff", "#2f5fe0",
     '<path d="M12 7.6C10.5 6.6 8.7 6.1 6.4 6.1v9.6c2.3 0 4.1.5 5.6 1.5 1.5-1 3.3-1.5 5.6-1.5V6.1c-2.3 0-4.1.5-5.6 1.5z" fill="#fff"/>'
     '<path d="M12 7.8v9.2" stroke="T" stroke-width="1.1"/>'),
    ("comic", "#b57bff", "#7a35d6",
     '<path d="M6 7.7A1.7 1.7 0 0 1 7.7 6h8.6A1.7 1.7 0 0 1 18 7.7v5.6a1.7 1.7 0 0 1-1.7 1.7h-4.9l-3.2 3v-3H7.7A1.7 1.7 0 0 1 6 13.3z" fill="#fff"/>'
     '<circle cx="9.3" cy="10.5" r=".95" fill="T"/><circle cx="12" cy="10.5" r=".95" fill="T"/><circle cx="14.7" cy="10.5" r=".95" fill="T"/>'),
    ("pdf", "#ff7a6b", "#d63a3a",
     '<path d="M8.4 5.4h5.2L17 8.8v8.9a.9.9 0 0 1-.9.9H8.4a.9.9 0 0 1-.9-.9V6.3a.9.9 0 0 1 .9-.9z" fill="#fff"/>'
     '<path d="M13.6 5.4v3.4H17" fill="none" stroke="T" stroke-width="1.1" stroke-linejoin="round"/>'
     '<path d="M9.5 12.1h5M9.5 14.4h5" stroke="T" stroke-width="1.2" stroke-linecap="round"/>'),
    ("audio", "#35d0bd", "#0f8f86",
     '<path d="M10.2 16V8.2l6.6-1.5v8" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
     '<circle cx="8.4" cy="16.2" r="2" fill="#fff"/><circle cx="15" cy="14.7" r="2" fill="#fff"/>'),
    ("video", "#7c83ff", "#4a45d6",
     '<path d="M9.4 7.4v9.2a.6.6 0 0 0 .9.5l7.3-4.6a.6.6 0 0 0 0-1L10.3 6.9a.6.6 0 0 0-.9.5z" fill="#fff"/>'),
    ("image", "#4fdc7e", "#1a9a4f",
     '<circle cx="9" cy="9.4" r="1.7" fill="#fff"/>'
     '<path d="M5.6 17.6l4.2-5.3 3.2 3.5 2.2-2.6 3.2 4.4z" fill="#fff" stroke="#fff" stroke-width=".6" stroke-linejoin="round"/>'),
    ("text", "#a3b1c6", "#64748d",
     '<path d="M7.2 8h9.6M7.2 11.2h9.6M7.2 14.4h9.6M7.2 17.2h5.6" stroke="#fff" stroke-width="1.7" stroke-linecap="round"/>'),
    ("table", "#36d3a2", "#0d8f6b",
     '<rect x="6" y="6.4" width="12" height="11.4" rx="1.6" fill="none" stroke="#fff" stroke-width="1.4"/>'
     '<path d="M6 10.2h12M6 14h12M11.3 6.6v11" stroke="#fff" stroke-width="1.2"/>'),
    ("archive", "#ffb547", "#d9780f",
     '<path d="M5.4 5.9h13.2v3H5.4z" fill="#fff"/><path d="M6.4 10h11.2v7.4a1 1 0 0 1-1 1H7.4a1 1 0 0 1-1-1z" fill="#fff"/>'
     '<path d="M10.2 12.4h3.6" stroke="T" stroke-width="1.6" stroke-linecap="round"/>'),
    ("file", "#c3cddb", "#8392a8",
     '<path d="M8.4 5.4h5.2L17 8.8v8.9a.9.9 0 0 1-.9.9H8.4a.9.9 0 0 1-.9-.9V6.3a.9.9 0 0 1 .9-.9z" fill="#fff"/>'
     '<path d="M13.6 5.4v3.4H17" fill="none" stroke="T" stroke-width="1.1" stroke-linejoin="round"/>'),
    ("upload", "#58b8ff", "#1f7ae0",
     '<path d="M12 16.6V8.2M8.4 11.6 12 8l3.6 3.6M7 18.2h10" fill="none" stroke="#fff" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>'),
]


def _tile_symbol(name: str, light: str, dark: str, glyph: str) -> str:
    return ('<linearGradient id="ig-%s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="%s"/><stop offset="1" stop-color="%s"/></linearGradient>'
            '<symbol id="ico-%s" viewBox="0 0 24 24"><rect x="1.2" y="1.2" width="21.6" height="21.6" rx="6.4" fill="url(#ig-%s)"/>'
            '<path d="M7.6 1.2h8.8a6.4 6.4 0 0 1 6.4 6.4v1.6C20 8 4 8 1.2 9.2V7.6A6.4 6.4 0 0 1 7.6 1.2z" fill="#fff" fill-opacity=".16"/>'
            '%s</symbol>' % (name, light, dark, name, name, glyph.replace('"T"', '"%s"' % dark)))


_FOLDER = ('<linearGradient id="ig-folder-back" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f0a93a"/><stop offset="1" stop-color="#d6841c"/></linearGradient>'
           '<linearGradient id="ig-folder-front" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffe08a"/><stop offset="1" stop-color="#f4b640"/></linearGradient>'
           '<symbol id="ico-folder" viewBox="0 0 24 24">'
           '<path d="M2.2 6.9A2.4 2.4 0 0 1 4.6 4.5h4.2c.7 0 1.3.3 1.8.7l1.2 1.1h7.6a2.4 2.4 0 0 1 2.4 2.4v9.4a2.4 2.4 0 0 1-2.4 2.4H4.6a2.4 2.4 0 0 1-2.4-2.4z" fill="url(#ig-folder-back)"/>'
           '<path d="M2.2 10.3a2.2 2.2 0 0 1 2.2-2.2h15.2a2.2 2.2 0 0 1 2.2 2.2v7.5a2.4 2.4 0 0 1-2.4 2.4H4.6a2.4 2.4 0 0 1-2.4-2.4z" fill="url(#ig-folder-front)"/>'
           '<path d="M3.4 9.3h17.2" stroke="#fff" stroke-opacity=".55" stroke-width="1" stroke-linecap="round"/></symbol>')

COLOR_SPRITE = ('<svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false"><defs>'
                + "".join(_tile_symbol(*t) for t in _TILES) + _FOLDER + "</defs></svg>")

KNOWN = set(_OUT) | {"folder"}


def ico(name: str) -> str:
    """The icon `name` (a file type, 'file', 'upload' or 'folder') as an inline picture."""
    if name not in KNOWN:
        name = "file"
    return "<svg class='pl-ico pl-ico-%s' aria-hidden='true' focusable='false'><use href='#ico-%s'/></svg>" % (name, name)

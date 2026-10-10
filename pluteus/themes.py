"""Themes: a folder with theme.css and any replacement assets (icons, fonts, images).

Lookup order for every file: the user's themes folder (themes_dir/<theme>/<file>), then the built-in
copy of the same theme, then the built-in 'default' theme. So a theme can override as little as one
icon and inherit the rest. Files are served with no-cache + ETag, so editing a theme file and reloading
the page is enough: there is no restart and no hour-long browser cache.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
ALLOWED = {".css", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".otf"}
BUILTIN = Path(__file__).resolve().parent / "static" / "themes"
DEFAULT = "default"


class Themes:
    def __init__(self, user_dir: str = "", builtin_dir: Path = BUILTIN):
        self.user_dir = Path(user_dir) if user_dir else None
        self.builtin = Path(builtin_dir)

    def names(self) -> list:
        found = set()
        for base in (self.user_dir, self.builtin):
            if base and base.is_dir():
                found.update(p.name for p in base.iterdir() if p.is_dir() and NAME_RE.match(p.name))
        return sorted(found, key=lambda n: (n != DEFAULT, n))

    def exists(self, name: str) -> bool:
        return bool(NAME_RE.match(name or "")) and name in self.names()

    def resolve(self, name: str, rel: str):
        """Real path of a theme file, or None. Never leaves the theme folders."""
        if not NAME_RE.match(name or ""):
            return None
        parts = rel.split("/")
        if not parts or any(p in ("", ".", "..") or "\\" in p or ":" in p or "\x00" in p for p in parts):
            return None
        if os.path.splitext(parts[-1])[1].lower() not in ALLOWED:
            return None
        candidates = []
        if self.user_dir:
            candidates.append(self.user_dir / name)
        candidates += [self.builtin / name, self.builtin / DEFAULT]
        for base in candidates:
            path = base.joinpath(*parts)
            try:
                real = path.resolve(strict=True)
                if real.is_file() and base.resolve() in real.parents:
                    return real
            except (OSError, RuntimeError):
                continue
        return None

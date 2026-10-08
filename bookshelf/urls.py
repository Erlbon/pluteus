"""Builds the server's own URLs (percent-encoded, honouring a reverse-proxy base path)."""
from __future__ import annotations

from urllib.parse import quote


class Urls:
    def __init__(self, base_path: str = ""):
        self.base = base_path

    def _build(self, kind: str, root: str | None = None, parts=()) -> str:
        segs = [kind]
        if root:
            segs.append(root)
        segs.extend(parts)
        return self.base + "/" + "/".join(quote(s, safe="") for s in segs)

    def home(self) -> str:
        return self.base + "/"

    def browse(self, root: str | None = None, parts=()) -> str:
        return self._build("browse", root, parts)

    def opds(self, root: str | None = None, parts=()) -> str:
        return self._build("opds", root, parts)

    def download(self, root: str, parts) -> str:
        return self._build("dl", root, parts)


def with_page(href: str, page: int) -> str:
    return href if page <= 1 else "%s?page=%d" % (href, page)

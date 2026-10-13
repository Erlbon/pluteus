"""Builds the server's own URLs (percent-encoded, honouring a reverse-proxy base path)."""
from __future__ import annotations

from urllib.parse import quote, urlencode


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

    def read(self, root: str, parts) -> str:
        return self._build("read", root, parts)

    def view(self, root: str, parts) -> str:
        return self._build("view", root, parts)

    def raw(self, root: str, parts) -> str:
        return self._build("raw", root, parts)

    def shelf(self, shelf: str, root: str, parts) -> str:
        return self.base + "/api/shelf/" + shelf + "/" + "/".join(quote(s, safe="") for s in (root, *parts))

    def thumb(self, root: str, parts) -> str:
        return self._build("thumb", root, parts)

    def cover(self, root: str, parts) -> str:
        return self._build("cover", root, parts)

    def api(self, kind: str, root: str, parts) -> str:
        return self.base + "/api/" + kind + "/" + "/".join(quote(s, safe="") for s in (root, *parts))

    def cbz(self, root: str, parts, tail: str = "") -> str:
        url = self.base + "/cbz/" + "/".join(quote(s, safe="") for s in (root, *parts))
        return url + ("/" + tail if tail else "")

    def zip_entry(self, root: str, parts, index: int, mode: str) -> str:
        """An entry inside a zip file: mode is dl (download), raw (an image, inline) or text (the text viewer)."""
        return "%s/zip/%s?i=%d&as=%s" % (self.base, "/".join(quote(s, safe="") for s in (root, *parts)), index, mode)

    def static(self, path: str) -> str:
        return self.base + "/static/" + path

    def theme(self, name: str, path: str) -> str:
        return "%s/theme/%s/%s" % (self.base, quote(name, safe=""), path)

    def login(self, next_url: str = "") -> str:
        return self.base + "/login" + ("?" + urlencode({"next": next_url}) if next_url else "")

    def logout(self) -> str:
        return self.base + "/logout"

    def upload(self, root: str, parts=()) -> str:
        return self._build("upload", root, parts)

    def files(self, op: str, root: str, parts=()) -> str:
        """POST target for file operations in the upload folder: op is rename, delete or mkdir."""
        return self.base + "/files/" + op + "/" + "/".join(quote(s, safe="") for s in (root, *parts))

    def dismiss(self, root: str, parts) -> str:
        """POST target that takes a file off the home page list."""
        return self.base + "/dismiss/" + "/".join(quote(s, safe="") for s in (root, *parts))

    def share(self) -> str:
        return self.base + "/share"

    def manifest(self) -> str:
        return self.base + "/manifest.webmanifest"

    def settings(self) -> str:
        return self.base + "/settings"


def with_page(href: str, page: int) -> str:
    return href if page <= 1 else "%s?page=%d" % (href, page)

"""What kind of file is this, which viewer opens it, and what may safely be shown inline.

Only the types in INLINE_MIME are ever served for inline display, always with an explicit type that we chose (never one
the file claims). Everything else is a download (Content-Disposition: attachment) or is shown as escaped text.
"""
from __future__ import annotations

import os

IMAGE = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
         ".webp": "image/webp", ".avif": "image/avif", ".bmp": "image/bmp", ".ico": "image/x-icon",
         ".svg": "image/svg+xml"}
AUDIO = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".m4b": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg", ".oga": "audio/ogg",
         ".opus": "audio/ogg", ".wav": "audio/wav", ".flac": "audio/flac"}
VIDEO = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".ogv": "video/ogg",
         ".mov": "video/quicktime", ".mkv": "video/x-matroska"}
PDF = {".pdf": "application/pdf"}
BOOKS = {".epub": "epub", ".cbz": "comic"}
MARKDOWN = {".md", ".markdown"}
TABLE = {".csv", ".tsv"}
ARCHIVE = {".zip"}
TEXT = {".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".yaml", ".yml", ".ini",
        ".cfg", ".conf", ".toml", ".nfo", ".diz", ".srt", ".vtt", ".sub", ".ass", ".url", ".m3u", ".m3u8", ".pls",
        ".reg", ".tex", ".bib", ".py", ".js", ".mjs", ".ts", ".css", ".html", ".htm", ".xhtml", ".sh", ".bat", ".cmd",
        ".ps1", ".sql", ".c", ".h", ".cpp", ".hpp", ".java", ".cs", ".go", ".rs", ".rb", ".php", ".lua", ".pl"}

# The ONLY types served inline (by /raw), each with the type we state ourselves.
INLINE_MIME = {**IMAGE, **AUDIO, **VIDEO, **PDF}
# Scriptable image formats get a sandbox CSP as well, so even opened directly they cannot touch the site.
SANDBOXED = {".svg"}

KINDS_WITH_PROGRESS = ("epub", "comic", "pdf", "audio", "video")


def extension(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def kind_of(name: str) -> str:
    """'epub' | 'comic' | 'pdf' | 'image' | 'audio' | 'video' | 'markdown' | 'table' | 'archive' | 'text' | 'other'."""
    ext = extension(name)
    if ext in BOOKS:
        return BOOKS[ext]
    if ext in PDF:
        return "pdf"
    if ext in IMAGE:
        return "image"
    if ext in AUDIO:
        return "audio"
    if ext in VIDEO:
        return "video"
    if ext in MARKDOWN:
        return "markdown"
    if ext in TABLE:
        return "table"
    if ext in ARCHIVE:
        return "archive"
    if ext in TEXT:
        return "text"
    return "other"

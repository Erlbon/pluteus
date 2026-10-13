"""Reading a request body with a hard size limit.

Starlette's `request.body()` buffers the WHOLE stream before anyone can look at its size, and a chunked upload has no
Content-Length to check, so a client could make the server hold gigabytes. `read_limited` stops reading as soon as the limit is
passed, so the cost of a refused body is bounded by the limit.
"""
from __future__ import annotations


async def read_limited(request, limit: int):
    """The body as bytes, or None when it is larger than `limit` (nothing more is read after that)."""
    try:
        if int(request.headers.get("content-length", "0") or 0) > limit:
            return None
    except ValueError:
        return None
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)

"""One password check at a time per name, and only a few at once in all.

The throttle (ratelimit.py) counts a wrong password AFTER it has been hashed, and hashing takes a worker thread and 16 MB for about
a tenth of a second. Without a gate, sixty guesses sent at the same moment are all let through before the first failure is counted,
and they fill the worker pool while they are at it. With the gate each caller waits its turn for the name it is trying, then looks
at the throttle (and at what the callers before it found out) again before it hashes anything:

    async with gate.turn(name):
        ... check the throttle and the remembered logins again, then hash ...

So the sixth guess for a name sees the five failures before it, and eight copies of one stale password from a reader app cost one
hash and one strike. The names share a fixed set of locks (a guesser inventing names cannot make the table grow).
"""
from __future__ import annotations

import asyncio
import contextlib
import zlib

STRIPES = 64                  # locks shared by all names
AT_ONCE = 4                   # password hashes running at the same time, for every name together


class LoginGate:
    def __init__(self, at_once: int = AT_ONCE):
        self._names = [asyncio.Lock() for _ in range(STRIPES)]
        self._hashing = asyncio.Semaphore(at_once)

    @contextlib.asynccontextmanager
    async def turn(self, name: str):
        stripe = zlib.crc32((name or "").strip().lower().encode("utf-8", "replace")) % STRIPES
        async with self._names[stripe]:
            async with self._hashing:
                yield

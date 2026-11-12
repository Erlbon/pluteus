"""Names and tag text from disk, made safe to put in an XML or JSON answer.

A file name on a Linux file system can hold bytes that are not valid UTF-8 (Python hands them over as lone surrogates) and control
characters; XML 1.0 allows neither, and a lone surrogate cannot be encoded as UTF-8 at all. One such name must not break a whole list.
"""
from __future__ import annotations

import re

_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")


def clean(text: str) -> str:
    """`text` with every character XML 1.0 refuses (and every lone surrogate) replaced by U+FFFD. For display only: never for a path."""
    return _BAD.sub("�", text) if text else text

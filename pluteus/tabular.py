"""CSV and TSV files as a table: guess the separator, read a bounded number of rows and columns, report what was cut.

Standard library only. Everything is shown escaped by the page builder; nothing here touches HTML.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

MAX_ROWS = 5000
MAX_COLS = 100
MAX_CELL = 1000
_NUMBER = re.compile(r"^[-+]?[\d\s.,']+%?$")


class TableError(Exception):
    """The text is not something that can be shown as a table."""


@dataclass
class Table:
    rows: list = field(default_factory=list)          # every row padded to the same width; rows[0] is the header
    delimiter: str = ","
    numeric: list = field(default_factory=list)       # per column: right-align and sort as numbers
    rows_cut: bool = False
    cols_cut: bool = False
    cells_cut: int = 0


def _delimiter(sample: str, ext: str) -> str:
    if ext == ".tsv":
        return "\t"
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ";" if sample.count(";") > sample.count(",") else ","


def parse(text: str, ext: str = ".csv") -> Table:
    """Read the text as a table. Raises TableError when it cannot be (binary-ish text, a giant cell, no rows)."""
    if "\x00" in text:
        raise TableError("the file contains NUL characters")
    table = Table(delimiter=_delimiter(text[:8192], ext))
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=table.delimiter)
    width = 0
    try:
        for row in reader:
            if not row:
                continue
            if len(table.rows) >= MAX_ROWS:
                table.rows_cut = True
                break
            if len(row) > MAX_COLS:
                table.cols_cut = True
                row = row[:MAX_COLS]
            for i, cell in enumerate(row):
                if len(cell) > MAX_CELL:
                    row[i] = cell[:MAX_CELL] + "…"
                    table.cells_cut += 1
            width = max(width, len(row))
            table.rows.append(row)
    except csv.Error as e:
        raise TableError(str(e))
    if not table.rows:
        raise TableError("there are no rows")
    table.rows = [r + [""] * (width - len(r)) for r in table.rows]
    body = table.rows[1:201]
    for col in range(width):
        cells = [r[col].strip() for r in body if r[col].strip()]
        table.numeric.append(bool(cells) and all(_NUMBER.match(c) for c in cells))
    return table

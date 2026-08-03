"""CSV export for the owner's reports.

Spreadsheets treat a leading ``=``, ``+``, ``-`` or ``@`` as a formula, and both
product names and shop names in these exports are written by sellers. A shop
called ``=cmd|'/c calc'!A1`` would otherwise run on the accountant's machine
when they opened the file. Every field is neutralised on the way out.
"""

from __future__ import annotations

import csv
import io

# Tab and carriage return are here because Excel strips them and then sees the
# formula character underneath.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def safe_cell(value) -> str:
    """A value a spreadsheet will show rather than evaluate."""
    text = "" if value is None else str(value)
    # Negative numbers start with '-' and are not formulas, so leave them alone.
    if text[:1] in FORMULA_PREFIXES and not _is_number(text):
        return "'" + text
    return text


def to_csv(header, rows) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow([safe_cell(column) for column in header])
    for row in rows:
        writer.writerow([safe_cell(cell) for cell in row])
    return buffer.getvalue()


def money(cents: int) -> str:
    """Plain decimal, no currency symbol or thousands separator - it is a number."""
    return f"{cents / 100:.2f}"

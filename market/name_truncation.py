"""Detect list-view item names cut off by the market UI."""

from __future__ import annotations

import re
from typing import Any

# Item name in raw OCR ends with "..." before On market / Vendor.
_RAW_TRUNC = re.compile(
    r"\.{2,}\s+(?:On\s*market|Onmarket|In\s*stock|Vendor)\b",
    re.IGNORECASE,
)


def is_truncated_display_name(name: str | None) -> bool:
    """Visible item string still contains trailing ellipsis."""
    if not name:
        return False
    return bool(re.search(r"\.{2,}\s*$", name.rstrip()))


def is_truncated_market_row(row: dict[str, Any]) -> bool:
    """
    True when the list row name was truncated in the market UI.

    OCR often strips ``...`` from the parsed ``item`` field; ``raw_text`` keeps it.
    """
    if is_truncated_display_name(row.get("item")):
        return True
    raw = row.get("raw_text") or ""
    return bool(_RAW_TRUNC.search(raw))

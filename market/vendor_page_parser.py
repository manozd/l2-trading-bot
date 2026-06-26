"""Parse BOHPTS vendor listing screen (In stock / Vendor / Price per unit layout)."""

from __future__ import annotations

import re
from typing import Any

from market.full_list_parser import parse_page_rows
from market.row_fields import PriceConfidence, sanitize_vendor_nickname

_CONFIDENCE_SCORE: dict[PriceConfidence, int] = {
    "high": 95,
    "medium": 80,
    "low": 55,
    "none": 0,
}


def is_garbage_vendor_item(name: str | None) -> bool:
    """True when OCR picked up stock/vendor/price text as the item name."""
    if not name or not name.strip():
        return False
    low = name.casefold().strip()
    if re.match(r"^(in stock|on market)\s*:", low):
        return True
    if "price per unit" in low or "min. price per" in low:
        return True
    if re.match(r"^vendor\s*:", low):
        return True
    if re.fullmatch(r"[\d,\s.]+", low):
        return True
    return False


def is_plausible_vendor_listing(row: dict[str, Any]) -> bool:
    price = row.get("price_adena")
    if price is None:
        return False
    try:
        if int(price) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    return True


def _normalize_vendor_row(row: dict[str, Any]) -> dict[str, Any] | None:
    price = row.get("price_adena")
    if price is None:
        return None
    try:
        price_i = int(price)
    except (TypeError, ValueError):
        return None
    if price_i <= 0:
        return None
    item = row.get("item")
    if is_garbage_vendor_item(item):
        item = None
    vendor_raw = row.get("vendor")
    vendor_norm = sanitize_vendor_nickname(vendor_raw) if vendor_raw else ""
    conf = row.get("price_confidence") or "none"
    if conf not in _CONFIDENCE_SCORE:
        conf = "none"
    return {
        "row": row.get("row"),
        "item": item,
        "vendor": vendor_norm or None,
        "vendor_ocr": vendor_raw,
        "price_adena": price_i,
        "units": row.get("units"),
        "price_confidence": conf,
        "price_confidence_score": _CONFIDENCE_SCORE[conf],
        "raw_text": row.get("raw_text") or "",
    }


def parse_vendor_page_rows(
    bgr,
    *,
    page: int = 1,
    ocr=None,
) -> list[dict[str, Any]]:
    """
    OCR vendor listings on the current screen.

    Uses row-band geometry similar to the list view but validates fields for the
    vendor layout (In stock / Vendor / Price per unit). Item name may be null.
    """
    rows = parse_page_rows(bgr, page=page, ocr=ocr, row_fallback=False)
    out: list[dict[str, Any]] = []
    for row in rows:
        record = _normalize_vendor_row(row.to_dict())
        if record is not None and is_plausible_vendor_listing(record):
            out.append(record)
    return out

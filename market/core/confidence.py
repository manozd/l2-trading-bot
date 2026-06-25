"""Row confidence scoring for search results."""

from __future__ import annotations

from typing import Literal

PriceConfidence = Literal["high", "medium", "low", "none"]


def score_search_row(
    row: dict | None,
    *,
    db_name: str,
    expected_enchant: int | None = None,
) -> tuple[int, PriceConfidence]:
    """Return (row_confidence 0–100, price_confidence)."""
    if not row:
        return 0, "none"

    score = 15  # identity from DB search query
    raw = (row.get("raw_text") or "").lower()
    price = row.get("price_adena")
    ocr_enchant = row.get("enchant")

    if expected_enchant is not None:
        if ocr_enchant is None:
            score -= 45
        elif ocr_enchant != expected_enchant:
            score -= 55
        else:
            score += 20

    parser_conf = row.get("price_confidence")
    if parser_conf in ("high", "medium", "low", "none"):
        price_conf: PriceConfidence = parser_conf
    elif price is not None:
        if "price" in raw or "adena" in raw:
            price_conf = "high"
        else:
            price_conf = "medium"
    else:
        price_conf = "none"

    if price is not None:
        if price_conf == "high":
            score += 40
        elif price_conf == "medium":
            score += 30
        else:
            score += 20
    else:
        score -= 30

    if row.get("vendor"):
        score += 20
    else:
        score -= 10

    if row.get("units") is not None:
        score += 15

    if raw and ("vendor" in raw and "price" in raw and len(raw) > 80):
        score -= 15  # glued text

    return max(0, min(100, score)), price_conf

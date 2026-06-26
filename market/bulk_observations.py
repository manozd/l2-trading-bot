"""Bulk crawl observation records — listings with list context, identity deferred."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from market.full_list_parser import MarketRow

from market.identity_status import is_trusted_identity


def make_scan_run_id(scanned_at: str, category: str) -> str:
    digest = hashlib.md5(f"{scanned_at}:{category}".encode()).hexdigest()[:8]
    day = scanned_at[:10].replace("-", "")
    return f"{day}-{digest}"


def bulk_context_id(scan_run_id: str, list_page: int, list_row: int) -> str:
    return f"{scan_run_id}:p{list_page}:r{list_row}"


def can_aggregate_bulk_price(observation: dict[str, Any]) -> bool:
    """Bulk observations are discovery data — not trusted for min-price buckets."""
    identity = observation.get("identity") or {}
    status = identity.get("status")
    if not is_trusted_identity(status):
        return False
    for row in observation.get("vendor_rows") or []:
        score = row.get("price_confidence_score") or 0
        price = row.get("price")
        if price is not None and score >= 80 and int(price) > 0:
            return True
    return False


def build_bulk_observation(
    *,
    scan_run_id: str,
    category: str,
    list_page: int,
    list_row: int,
    list_icon_hash: str,
    ocr_row: MarketRow | None,
    vendor_listings: list[dict[str, Any]],
    list_page_total_hint: int | None = None,
) -> dict[str, Any]:
    visible_name = ocr_row.item if ocr_row else None
    vendor_rows: list[dict[str, Any]] = []
    for listing in vendor_listings:
        vendor_rows.append(
            {
                "vendor_ocr": listing.get("vendor_ocr") or listing.get("vendor"),
                "vendor_normalized": listing.get("vendor"),
                "units": listing.get("units"),
                "price": listing.get("price_adena"),
                "price_confidence": listing.get("price_confidence"),
                "price_confidence_score": listing.get("price_confidence_score", 0),
                "vendor_list_row": listing.get("row"),
                "raw_text": listing.get("raw_text"),
            }
        )

    return {
        "type": "bulk_vendor_scan",
        "scan_run_id": scan_run_id,
        "bulk_context_id": bulk_context_id(scan_run_id, list_page, list_row),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "list_context": {
            "page": list_page,
            "row": list_row,
            "page_total_hint": list_page_total_hint,
            "visible_name_ocr": visible_name,
            "icon_hash": list_icon_hash,
            "list_hint_price": ocr_row.price_adena if ocr_row else None,
            "list_hint_vendor": ocr_row.vendor if ocr_row else None,
            "list_hint_units": ocr_row.units if ocr_row else None,
            "raw_list_ocr": ocr_row.raw_text if ocr_row else None,
        },
        "identity": {
            "status": "unresolved",
            "item_uid": None,
            "item_id": None,
            "item_name": None,
            "possible_item_uids": [],
            "possible_item_ids": [],
            "source": "bulk_list_context",
        },
        "vendor_rows": vendor_rows,
        "vendor_listing_count": len(vendor_rows),
    }

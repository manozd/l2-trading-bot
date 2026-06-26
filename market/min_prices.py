"""Aggregate market JSONL rows into minimum price per item."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from market.item_identity import apply_catalog, load_name_catalog
from market.row_fields import sanitize_vendor_nickname


@dataclass
class MinPriceEntry:
    item_key: str
    item: str | None
    item_full_name: str | None
    name_source: str
    min_price_adena: int
    listing_count: int
    vendors: list[str]
    sample_page: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


from market.identity_status import TRUSTED_IDENTITY_STATUSES, is_trusted_identity


def can_aggregate_price(row: dict[str, Any]) -> bool:
    """True when a flat listing row is safe to bucket into trusted min-price tables."""
    if row.get("type") == "bulk_vendor_scan":
        return False
    price = row.get("price_adena") or row.get("price")
    if price is None:
        return False
    try:
        if int(price) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    status = (row.get("identity") or {}).get("status") or row.get("identity_status")
    if status is not None and not is_trusted_identity(status):
        return False
    conf_name = row.get("price_confidence")
    if conf_name in ("low", "none"):
        return False
    return True


def aggregate_min_prices(
    rows: list[dict[str, Any]],
    *,
    catalog: dict[str, str] | None = None,
) -> list[MinPriceEntry]:
    catalog = catalog or {}
    buckets: dict[str, dict[str, Any]] = {}

    for row in rows:
        if row.get("type") == "bulk_vendor_scan":
            continue
        row = apply_catalog(dict(row), catalog)
        price = row.get("price_adena")
        if price is None:
            continue
        try:
            price_i = int(price)
        except (TypeError, ValueError):
            continue

        key = row.get("item_key") or row.get("item_icon_hash") or row.get("item")
        if not key:
            continue

        vendor_raw = row.get("vendor")
        vendor = sanitize_vendor_nickname(vendor_raw) if vendor_raw else ""
        vendor = vendor or None

        if key not in buckets:
            buckets[key] = {
                "item_key": str(key),
                "item": row.get("item"),
                "item_full_name": row.get("item_full_name"),
                "name_source": row.get("name_source", "list_truncated"),
                "min_price_adena": price_i,
                "listing_count": 1,
                "vendors": [vendor] if vendor else [],
                "sample_page": row.get("page"),
            }
            continue

        b = buckets[key]
        b["listing_count"] += 1
        if price_i < b["min_price_adena"]:
            b["min_price_adena"] = price_i
            b["item"] = row.get("item") or b["item"]
            b["item_full_name"] = row.get("item_full_name") or b["item_full_name"]
            b["name_source"] = row.get("name_source", b["name_source"])
            b["sample_page"] = row.get("page")
        if vendor and vendor not in b["vendors"]:
            b["vendors"].append(vendor)

    out = [MinPriceEntry(**b) for b in buckets.values()]
    out.sort(key=lambda e: (e.item_full_name or e.item or e.item_key).lower())
    return out


def write_min_prices_json(path: Path, entries: list[MinPriceEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [e.to_dict() for e in entries]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_min_prices_csv(path: Path, entries: list[MinPriceEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["item_key,item,item_full_name,name_source,min_price_adena,listing_count,vendors,sample_page"]
    for e in entries:
        vendors = ";".join(e.vendors)
        item = (e.item or "").replace('"', '""')
        full = (e.item_full_name or "").replace('"', '""')
        lines.append(
            f'{e.item_key},"{item}","{full}",{e.name_source},{e.min_price_adena},'
            f'{e.listing_count},"{vendors}",{e.sample_page or ""}'
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

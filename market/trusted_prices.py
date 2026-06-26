"""Trusted min/median prices aggregated by item_uid."""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from market.identity_status import is_trusted_identity
from market.min_prices import load_jsonl_rows
from market.resolve_bulk import load_bulk_jsonl
from market.variant_catalog import (
    VariantCatalog,
    is_fungible_category,
    is_fungible_entry,
)

DEFAULT_TRUSTED_JSONL = Path("logs/trusted_market_prices.jsonl")
DEFAULT_TRUSTED_CSV = Path("logs/trusted_min_prices.csv")
DEFAULT_TRUSTED_GROUPED_CSV = Path("logs/trusted_min_prices_grouped.csv")


@dataclass
class TrustedPriceRow:
    item_uid: str
    display_name: str | None
    variant_group: str | None
    icon_hash: str | None
    min_price: int
    median_price: int | None
    vendor: str | None
    units: int | None
    source: str
    identity_status: str
    last_seen_at: str
    confidence: str
    observation_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GroupedTrustedPriceRow:
    """Trading view — fungible items rolled up by ``variant_group``."""

    group_key: str
    variant_group: str | None
    display_name: str | None
    fungible: bool
    min_price: int
    median_price: int | None
    vendor: str | None
    units: int | None
    source: str
    identity_status: str
    last_seen_at: str
    confidence: str
    observation_count: int = 1
    item_uid_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _bulk_trusted_prices(obs: dict[str, Any]) -> list[dict[str, Any]]:
    identity = obs.get("identity") or {}
    status = identity.get("status")
    if not is_trusted_identity(status):
        return []

    item_uid = identity.get("item_uid")
    if not item_uid:
        return []

    lc = obs.get("list_context") or {}
    ts = obs.get("timestamp") or ""
    out: list[dict[str, Any]] = []

    for vr in obs.get("vendor_rows") or []:
        price = vr.get("price")
        conf = vr.get("price_confidence")
        score = int(vr.get("price_confidence_score") or 0)
        if price is None:
            continue
        try:
            price_i = int(price)
        except (TypeError, ValueError):
            continue
        if price_i <= 0:
            continue
        if conf in ("low", "none") or score < 80:
            continue
        out.append(
            {
                "item_uid": item_uid,
                "display_name": identity.get("item_name"),
                "variant_group": identity.get("item_id"),
                "category": None,
                "icon_hash": lc.get("icon_hash"),
                "price": price_i,
                "vendor": vr.get("vendor_normalized") or vr.get("vendor_ocr"),
                "units": vr.get("units"),
                "source": "bulk_resolved",
                "identity_status": status,
                "last_seen_at": ts,
                "confidence": conf or "high",
            }
        )
    return out


def _search_trusted_prices(row: dict[str, Any]) -> dict[str, Any] | None:
    status = row.get("identity_status") or "search_confirmed"
    if not is_trusted_identity(status):
        return None
    price = row.get("price_adena") or row.get("min_price_adena")
    if price is None:
        return None
    try:
        price_i = int(price)
    except (TypeError, ValueError):
        return None
    if price_i <= 0:
        return None

    item_uid = row.get("item_uid")
    if not item_uid:
        item_id = row.get("item_id")
        icon = row.get("item_icon_hash") or row.get("item_key")
        if item_id and icon:
            from market.variant_catalog import make_item_uid

            item_uid = make_item_uid(base_id=str(item_id), icon_hash=str(icon))
        else:
            item_uid = str(item_id or row.get("search_query") or row.get("item_name") or "unknown")

    return {
        "item_uid": item_uid,
        "display_name": row.get("item") or row.get("item_name") or row.get("item_full_name"),
        "variant_group": row.get("item_id"),
        "category": row.get("category"),
        "icon_hash": row.get("item_icon_hash"),
        "price": price_i,
        "vendor": row.get("vendor"),
        "units": row.get("units"),
        "source": row.get("price_source") or "search_m2",
        "identity_status": status,
        "last_seen_at": row.get("scanned_at") or "",
        "confidence": row.get("price_confidence") or "medium",
    }


def collect_trusted_price_points(
    *,
    resolved_bulk_path: Path | None = None,
    search_prices_path: Path | None = None,
    resolved_bulk_rows: list[dict[str, Any]] | None = None,
    search_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []

    bulk = resolved_bulk_rows
    if bulk is None and resolved_bulk_path and resolved_bulk_path.is_file():
        bulk = load_bulk_jsonl(resolved_bulk_path)
    for obs in bulk or []:
        points.extend(_bulk_trusted_prices(obs))

    search = search_rows
    if search is None and search_prices_path and search_prices_path.is_file():
        search = load_jsonl_rows(search_prices_path)
    for row in search or []:
        if row.get("type") == "bulk_vendor_scan":
            continue
        pt = _search_trusted_prices(row)
        if pt:
            points.append(pt)

    return points


def aggregate_trusted_prices(points: list[dict[str, Any]]) -> list[TrustedPriceRow]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for pt in points:
        uid = str(pt["item_uid"])
        buckets.setdefault(uid, []).append(pt)

    out: list[TrustedPriceRow] = []
    for item_uid, rows in buckets.items():
        prices = sorted(int(r["price"]) for r in rows)
        min_row = min(rows, key=lambda r: int(r["price"]))
        median: int | None = None
        if len(prices) >= 2:
            median = int(statistics.median(prices))

        out.append(
            TrustedPriceRow(
                item_uid=item_uid,
                display_name=min_row.get("display_name"),
                variant_group=min_row.get("variant_group"),
                icon_hash=min_row.get("icon_hash"),
                min_price=prices[0],
                median_price=median,
                vendor=min_row.get("vendor"),
                units=min_row.get("units"),
                source=min_row.get("source") or "mixed",
                identity_status=str(min_row.get("identity_status") or ""),
                last_seen_at=max(str(r.get("last_seen_at") or "") for r in rows),
                confidence=str(min_row.get("confidence") or "medium"),
                observation_count=len(rows),
            )
        )

    out.sort(key=lambda r: (r.variant_group or "", r.item_uid))
    return out


def _variant_group_from_uid(item_uid: str) -> str | None:
    if "__icon_" in item_uid:
        return item_uid.split("__icon_", 1)[0]
    return None


def _resolve_point_meta(
    pt: dict[str, Any],
    catalog: VariantCatalog,
) -> tuple[str, str | None, str | None, bool]:
    """Return ``(group_key, variant_group, category, fungible)`` for a price point."""
    uid = str(pt.get("item_uid") or "")
    vg = pt.get("variant_group")
    category = pt.get("category")

    entry = catalog.get(uid) if uid else None
    if entry is None and vg:
        group = catalog.find_by_variant_group(str(vg))
        if len(group) == 1:
            entry = group[0]
        elif uid:
            prefix = _variant_group_from_uid(uid)
            if prefix:
                matches = [e for e in group if (e.variant_group or "") == prefix]
                if len(matches) == 1:
                    entry = matches[0]

    if entry:
        category = entry.category or category
        vg = entry.variant_group or vg

    if not vg and uid:
        vg = _variant_group_from_uid(uid) or uid

    fungible = is_fungible_category(category) or is_fungible_entry(entry)
    if fungible:
        group_key = str(vg or uid or "unknown")
    else:
        group_key = uid or str(vg or "unknown")

    return group_key, str(vg) if vg else None, category, fungible


def aggregate_trusted_prices_grouped(
    points: list[dict[str, Any]],
    catalog: VariantCatalog,
) -> list[GroupedTrustedPriceRow]:
    """Roll up fungible items by ``variant_group``; gear stays per ``item_uid``."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    meta: dict[str, tuple[str | None, str | None, bool]] = {}

    for pt in points:
        group_key, vg, category, fungible = _resolve_point_meta(pt, catalog)
        buckets.setdefault(group_key, []).append(pt)
        meta[group_key] = (vg, category, fungible)

    out: list[GroupedTrustedPriceRow] = []
    for group_key, rows in buckets.items():
        vg, _category, fungible = meta[group_key]
        prices = sorted(int(r["price"]) for r in rows)
        min_row = min(rows, key=lambda r: int(r["price"]))
        median: int | None = None
        if len(prices) >= 2:
            median = int(statistics.median(prices))

        uid_set = {str(r.get("item_uid") or "") for r in rows if r.get("item_uid")}

        out.append(
            GroupedTrustedPriceRow(
                group_key=group_key,
                variant_group=vg,
                display_name=min_row.get("display_name"),
                fungible=fungible,
                min_price=prices[0],
                median_price=median,
                vendor=min_row.get("vendor"),
                units=min_row.get("units"),
                source=min_row.get("source") or "mixed",
                identity_status=str(min_row.get("identity_status") or ""),
                last_seen_at=max(str(r.get("last_seen_at") or "") for r in rows),
                confidence=str(min_row.get("confidence") or "medium"),
                observation_count=len(rows),
                item_uid_count=len(uid_set),
            )
        )

    out.sort(key=lambda r: (not r.fungible, r.variant_group or "", r.group_key))
    return out


def write_trusted_grouped_csv(path: Path, rows: list[GroupedTrustedPriceRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "group_key",
        "variant_group",
        "display_name",
        "fungible",
        "min_price",
        "median_price",
        "vendor",
        "units",
        "source",
        "identity_status",
        "last_seen_at",
        "confidence",
        "observation_count",
        "item_uid_count",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_dict())


def write_trusted_jsonl(path: Path, rows: list[TrustedPriceRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row.to_dict(), ensure_ascii=False) + "\n")


def write_trusted_csv(path: Path, rows: list[TrustedPriceRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "item_uid",
        "display_name",
        "variant_group",
        "icon_hash",
        "min_price",
        "median_price",
        "vendor",
        "units",
        "source",
        "identity_status",
        "last_seen_at",
        "confidence",
        "observation_count",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_dict())

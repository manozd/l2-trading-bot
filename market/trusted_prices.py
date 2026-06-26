"""Trusted min/median prices aggregated by item_uid."""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from market.canonical_names import (
    CanonicalNameIndex,
    is_likely_ui_truncated,
)
from market.canonical_status import (
    CANONICAL_AMBIGUOUS_PREFIX,
    TRUSTED_HINT_WARNING,
    TRUSTED_HINT_YES,
    UNRESOLVED,
)
from market.craft.match import _MIN_ACCEPT_SCORE, _match_score
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

# Availability states for grouped trusted output
AVAILABILITY_AVAILABLE = "available"
AVAILABILITY_NOT_FOUND = "not_found"
AVAILABILITY_STALE = "stale"
AVAILABILITY_MISSING = "missing"

# Freshness windows (hours)
M2_FRESH_HOURS_FUNGIBLE = 6.0
M2_FRESH_HOURS_GEAR = 2.0
BULK_FRESH_HOURS = 24.0
BULK_STALE_WARN_HOURS = 12.0

SOURCE_M2 = "m2_search"
SOURCE_BULK = "bulk_resolved"


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
    availability: str = AVAILABILITY_AVAILABLE
    selected_source: str = ""
    suppressed_sources: str = ""
    is_stale: bool = False
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _age_hours(ts: str) -> float | None:
    dt = _parse_ts(ts)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0


def _is_m2_point(pt: dict[str, Any]) -> bool:
    src = str(pt.get("source") or "")
    return src in (SOURCE_M2, "search_m2", "search_results_matched_row")


def _is_bulk_point(pt: dict[str, Any]) -> bool:
    return str(pt.get("source") or "") == SOURCE_BULK


def _freshness_limit_hours(pt: dict[str, Any], *, fungible: bool) -> float:
    if pt.get("availability") == AVAILABILITY_NOT_FOUND and _is_m2_point(pt):
        return M2_FRESH_HOURS_GEAR if not fungible else M2_FRESH_HOURS_FUNGIBLE
    if _is_m2_point(pt):
        return M2_FRESH_HOURS_FUNGIBLE if fungible else M2_FRESH_HOURS_GEAR
    if _is_bulk_point(pt):
        return BULK_FRESH_HOURS
    return M2_FRESH_HOURS_FUNGIBLE if fungible else M2_FRESH_HOURS_GEAR


def _is_fresh_point(pt: dict[str, Any], *, fungible: bool) -> bool:
    age = _age_hours(str(pt.get("last_seen_at") or ""))
    if age is None:
        return False
    return age <= _freshness_limit_hours(pt, fungible=fungible)


def _is_truncated_bulk_name(name: str | None) -> bool:
    """Deprecated alias — use ``is_likely_ui_truncated`` + canonical resolve."""
    return is_likely_ui_truncated(name)


def _canonicalize_bulk_identity(
    identity: dict[str, Any],
    lc: dict[str, Any],
    *,
    canonical_index: CanonicalNameIndex | None,
) -> tuple[str | None, str | None, str | None]:
    """
    Return ``(display_name, item_id, warning)`` for a bulk observation.

    High-trust sources (alias, recipe, catalog, target) may override ``item_id``.
    Items-database prefix matches are name hints only and are excluded from trusted prices.
    """
    display = (
        identity.get("catalog_search_query")
        or identity.get("item_name")
        or lc.get("visible_name_ocr")
    )
    item_id = identity.get("item_id")
    if not display:
        return None, item_id, None

    text = str(display)
    truncated = is_likely_ui_truncated(text) or (
        canonical_index is not None and canonical_index.is_truncated_visible(text)
    )

    if canonical_index is not None:
        result = canonical_index.resolve_name(text)
        if result.status == CANONICAL_AMBIGUOUS_PREFIX:
            return None, None, f"ambiguous prefix: {text!r}"

        if result.status != UNRESOLVED and result.display_name:
            if result.trusted_hint == TRUSTED_HINT_WARNING:
                if truncated:
                    return (
                        None,
                        None,
                        f"db-only prefix hint (not trusted): {result.display_name!r}",
                    )
                return str(display), item_id, None

            if result.trusted_hint == TRUSTED_HINT_YES:
                note = f"canonicalized ({result.status}) from {text!r}"
                resolved_id = result.item_id if result.item_id else item_id
                return result.display_name, resolved_id, note

    if truncated:
        return None, None, f"truncated name unresolved: {text!r}"

    return text, item_id, None


def _not_found_label(pt: dict[str, Any]) -> str:
    return str(
        pt.get("display_name")
        or pt.get("search_query")
        or pt.get("item_id")
        or pt.get("item_uid")
        or ""
    )


def _not_found_suppresses_price(nf: dict[str, Any], price_pt: dict[str, Any]) -> bool:
    """Fresh M+2 not_found suppresses older bulk/search price for the same material."""
    if nf.get("availability") != AVAILABILITY_NOT_FOUND or not _is_m2_point(nf):
        return False
    nf_item = str(nf.get("item_id") or nf.get("variant_group") or "")
    price_item = str(price_pt.get("item_id") or price_pt.get("variant_group") or "")
    if nf_item and price_item and nf_item.casefold() == price_item.casefold():
        return True
    nf_uid = str(nf.get("item_uid") or "")
    price_uid = str(price_pt.get("item_uid") or "")
    if nf_uid and price_uid and nf_uid.casefold() == price_uid.casefold():
        return True

    nf_name = _not_found_label(nf)
    price_name = str(price_pt.get("display_name") or "")
    if not nf_name or not price_name:
        return False
    score = _match_score(price_name, nf_name)
    nf_l = nf_name.casefold()
    price_l = price_name.casefold()
    # Recipe not_found suppresses truncated/wrong bulk recipe rows only when names align.
    if nf_l.startswith("recipe:") and "recipe" in price_l and score >= _MIN_ACCEPT_SCORE:
        return True
    if nf_item.startswith("recipe_") and "recipe" in price_l and score >= _MIN_ACCEPT_SCORE:
        return True
    # Same gear name (high confidence) — avoid suppressing Draconic Bow vs Draconic Leather.
    return score >= 90


def _bulk_trusted_prices(
    obs: dict[str, Any],
    *,
    canonical_index: CanonicalNameIndex | None = None,
) -> list[dict[str, Any]]:
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

        display, canon_item_id, canon_note = _canonicalize_bulk_identity(
            identity, lc, canonical_index=canonical_index,
        )
        if display is None:
            continue

        out.append(
            {
                "item_uid": item_uid,
                "item_id": canon_item_id or identity.get("item_id"),
                "display_name": display,
                "variant_group": canon_item_id or identity.get("item_id"),
                "category": obs.get("category"),
                "icon_hash": lc.get("icon_hash"),
                "price": price_i,
                "vendor": vr.get("vendor_normalized") or vr.get("vendor_ocr"),
                "units": vr.get("units"),
                "source": SOURCE_BULK,
                "availability": AVAILABILITY_AVAILABLE,
                "identity_status": status,
                "last_seen_at": ts,
                "confidence": conf or "high",
                "canonical_note": canon_note,
            }
        )
    return out


def _search_trusted_point(row: dict[str, Any]) -> dict[str, Any] | None:
    """Convert M+2 search JSONL row to a trusted price point (price or not_found)."""
    if row.get("type") == "bulk_vendor_scan":
        return None

    item_id = str(row.get("item_id") or "")
    scanned_at = str(row.get("scanned_at") or "")
    availability = str(row.get("availability") or "")
    found = row.get("found")
    is_not_found = availability == AVAILABILITY_NOT_FOUND or found is False

    if is_not_found:
        if not item_id:
            return None
        return {
            "item_uid": row.get("item_uid") or item_id,
            "item_id": item_id,
            "display_name": row.get("item_name") or row.get("search_query"),
            "search_query": row.get("search_query"),
            "variant_group": item_id,
            "category": row.get("category"),
            "icon_hash": row.get("item_icon_hash"),
            "price": None,
            "vendor": None,
            "units": None,
            "source": SOURCE_M2,
            "availability": AVAILABILITY_NOT_FOUND,
            "identity_status": row.get("identity_status") or "search_confirmed",
            "last_seen_at": scanned_at,
            "confidence": "high",
        }

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
        icon = row.get("item_icon_hash") or row.get("item_key")
        if item_id and icon:
            from market.variant_catalog import make_item_uid

            item_uid = make_item_uid(base_id=str(item_id), icon_hash=str(icon))
        else:
            item_uid = str(item_id or row.get("search_query") or row.get("item_name") or "unknown")

    return {
        "item_uid": item_uid,
        "item_id": item_id or None,
        "display_name": row.get("item") or row.get("item_name") or row.get("item_full_name"),
        "search_query": row.get("search_query"),
        "variant_group": item_id or None,
        "category": row.get("category"),
        "icon_hash": row.get("item_icon_hash"),
        "price": price_i,
        "vendor": row.get("vendor"),
        "units": row.get("units"),
        "source": SOURCE_M2,
        "availability": AVAILABILITY_AVAILABLE,
        "identity_status": status,
        "last_seen_at": scanned_at,
        "confidence": row.get("price_confidence") or "medium",
    }


def collect_trusted_price_points(
    *,
    resolved_bulk_path: Path | None = None,
    search_prices_path: Path | None = None,
    resolved_bulk_rows: list[dict[str, Any]] | None = None,
    search_rows: list[dict[str, Any]] | None = None,
    canonical_index: CanonicalNameIndex | None = None,
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    canon = canonical_index
    if canon is None and (
        (resolved_bulk_path and resolved_bulk_path.is_file())
        or resolved_bulk_rows
    ):
        canon = CanonicalNameIndex.load()

    bulk = resolved_bulk_rows
    if bulk is None and resolved_bulk_path and resolved_bulk_path.is_file():
        bulk = load_bulk_jsonl(resolved_bulk_path)
    for obs in bulk or []:
        points.extend(_bulk_trusted_prices(obs, canonical_index=canon))

    search = search_rows
    if search is None and search_prices_path and search_prices_path.is_file():
        search = load_jsonl_rows(search_prices_path)
    for row in search or []:
        pt = _search_trusted_point(row)
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
        price_rows = [r for r in rows if r.get("price") is not None]
        if not price_rows:
            continue
        prices = sorted(int(r["price"]) for r in price_rows)
        min_row = min(price_rows, key=lambda r: int(r["price"]))
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

    item_id = str(pt.get("item_id") or "")
    display = str(pt.get("display_name") or "")
    if item_id.startswith("recipe_") or display.startswith("Recipe:"):
        fungible = False
    else:
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
    """
    Roll up price points with M+2 priority:

    fresh M+2 not_found > fresh M+2 price > bulk price > stale
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    meta: dict[str, tuple[str | None, str | None, bool]] = {}

    for pt in points:
        group_key, vg, category, fungible = _resolve_point_meta(pt, catalog)
        buckets.setdefault(group_key, []).append(pt)
        meta[group_key] = (vg, category, fungible)

    fresh_not_founds = [
        pt
        for pt in points
        if pt.get("availability") == AVAILABILITY_NOT_FOUND
        and _is_m2_point(pt)
        and _is_fresh_point(pt, fungible=False)
    ]

    out: list[GroupedTrustedPriceRow] = []
    emitted_nf_keys: set[str] = set()

    for group_key, rows in buckets.items():
        vg, _category, fungible = meta[group_key]
        price_rows = [r for r in rows if r.get("availability") != AVAILABILITY_NOT_FOUND]
        nf_rows = [r for r in rows if r.get("availability") == AVAILABILITY_NOT_FOUND]

        fresh_nf = [
            r for r in nf_rows if _is_m2_point(r) and _is_fresh_point(r, fungible=fungible)
        ]
        if fresh_nf:
            best_nf = max(fresh_nf, key=lambda r: str(r.get("last_seen_at") or ""))
            label = _not_found_label(best_nf)
            out.append(
                GroupedTrustedPriceRow(
                    group_key=group_key,
                    variant_group=vg,
                    display_name=label or best_nf.get("display_name"),
                    fungible=fungible,
                    min_price=0,
                    median_price=None,
                    vendor=None,
                    units=None,
                    source=SOURCE_M2,
                    identity_status=str(best_nf.get("identity_status") or "search_confirmed"),
                    last_seen_at=str(best_nf.get("last_seen_at") or ""),
                    confidence="high",
                    observation_count=len(fresh_nf),
                    item_uid_count=1,
                    availability=AVAILABILITY_NOT_FOUND,
                    selected_source=SOURCE_M2,
                    suppressed_sources=_suppressed_source_names(price_rows),
                    is_stale=False,
                    warning="M+2 search found no listings",
                )
            )
            emitted_nf_keys.add(group_key)
            continue

        eligible = [
            r
            for r in price_rows
            if not any(_not_found_suppresses_price(nf, r) for nf in fresh_not_founds)
        ]
        if not eligible:
            continue

        m2_fresh = [
            r
            for r in eligible
            if _is_m2_point(r) and _is_fresh_point(r, fungible=fungible)
        ]
        m2_stale = [r for r in eligible if _is_m2_point(r) and r not in m2_fresh]
        bulk_fresh = [
            r
            for r in eligible
            if _is_bulk_point(r) and _is_fresh_point(r, fungible=fungible)
        ]
        bulk_stale = [r for r in eligible if _is_bulk_point(r) and r not in bulk_fresh]
        other = [r for r in eligible if r not in m2_fresh + m2_stale + bulk_fresh + bulk_stale]

        selected_pool: list[dict[str, Any]]
        selected_source: str
        is_stale = False
        warning = ""
        suppressed_str = ""

        if m2_fresh:
            selected_pool = m2_fresh
            selected_source = SOURCE_M2
            suppressed_str = _suppressed_source_names(bulk_fresh + bulk_stale + m2_stale)
        elif m2_stale:
            selected_pool = m2_stale
            selected_source = SOURCE_M2
            is_stale = True
            warning = f"M+2 price older than {M2_FRESH_HOURS_GEAR if not fungible else M2_FRESH_HOURS_FUNGIBLE:g}h"
            suppressed_str = _suppressed_source_names(bulk_fresh + bulk_stale)
        elif bulk_fresh:
            selected_pool = bulk_fresh
            selected_source = SOURCE_BULK
            if not fungible:
                warning = "bulk-only; no fresh M+2 price"
        elif bulk_stale:
            selected_pool = bulk_stale
            selected_source = SOURCE_BULK
            is_stale = True
            warning = f"bulk price older than {BULK_STALE_WARN_HOURS:g}h"
        elif other:
            selected_pool = other
            selected_source = str(other[0].get("source") or "mixed")
            is_stale = True
            warning = "legacy/mixed source"
        else:
            continue

        canon_notes = [str(r.get("canonical_note") or "") for r in selected_pool if r.get("canonical_note")]
        if canon_notes:
            canon_warn = canon_notes[0]
            warning = f"{warning}; {canon_warn}" if warning else canon_warn

        prices = sorted(int(r["price"]) for r in selected_pool if r.get("price") is not None)
        if not prices:
            continue
        min_row = min(selected_pool, key=lambda r: int(r["price"]))
        median: int | None = None
        if len(prices) >= 2:
            median = int(statistics.median(prices))
        uid_set = {str(r.get("item_uid") or "") for r in selected_pool if r.get("item_uid")}

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
                source=selected_source,
                identity_status=str(min_row.get("identity_status") or ""),
                last_seen_at=max(str(r.get("last_seen_at") or "") for r in selected_pool),
                confidence=str(min_row.get("confidence") or "medium"),
                observation_count=len(selected_pool),
                item_uid_count=len(uid_set),
                availability=AVAILABILITY_STALE if is_stale else AVAILABILITY_AVAILABLE,
                selected_source=selected_source,
                suppressed_sources=suppressed_str,
                is_stale=is_stale,
                warning=warning,
            )
        )

    # Explicit not_found rows for M+2 targets that only suppressed cross-group bulk rows
    for nf in fresh_not_founds:
        nf_key = str(nf.get("item_id") or nf.get("item_uid") or "")
        if not nf_key or nf_key in emitted_nf_keys:
            continue
        if any(r.group_key == nf_key for r in out):
            continue
        label = _not_found_label(nf)
        out.append(
            GroupedTrustedPriceRow(
                group_key=nf_key,
                variant_group=str(nf.get("item_id") or nf_key),
                display_name=label,
                fungible=False,
                min_price=0,
                median_price=None,
                vendor=None,
                units=None,
                source=SOURCE_M2,
                identity_status=str(nf.get("identity_status") or "search_confirmed"),
                last_seen_at=str(nf.get("last_seen_at") or ""),
                confidence="high",
                observation_count=1,
                item_uid_count=1,
                availability=AVAILABILITY_NOT_FOUND,
                selected_source=SOURCE_M2,
                suppressed_sources="bulk_resolved",
                is_stale=False,
                warning="M+2 search found no listings; older bulk price suppressed",
            )
        )

    out.sort(key=lambda r: (not r.fungible, r.variant_group or "", r.group_key))
    return out


def _suppressed_source_names(rows: list[dict[str, Any]]) -> str:
    names: list[str] = []
    seen: set[str] = set()
    for r in rows:
        src = str(r.get("source") or "unknown")
        if src not in seen:
            seen.add(src)
            names.append(src)
    return ",".join(names)


def write_trusted_grouped_csv(path: Path, rows: list[GroupedTrustedPriceRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "group_key",
        "variant_group",
        "display_name",
        "fungible",
        "availability",
        "min_price",
        "median_price",
        "vendor",
        "units",
        "source",
        "selected_source",
        "suppressed_sources",
        "is_stale",
        "warning",
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

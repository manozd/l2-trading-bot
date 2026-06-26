"""M+2 priority scan phases — catalog + matched-row price (no vendor depth)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from market.core.confidence import PriceConfidence, score_search_row
from market.core.models import ItemRef, SearchResult
from market.craft.match import find_search_result_price_row, pick_result_row
from market.full_list_parser import MarketRow
from market.run_control import RunControl, check_stop, sleep_checked
from market.scanner import collect_search_page_rows
from market.variant_catalog import VariantCatalog, make_item_uid

PRICE_SOURCE_MATCHED_ROW = "search_results_matched_row"
_SEARCH_RETRY_S = 0.35


def fallback_search_queries(search_name: str) -> list[str]:
    """
    Queries to try in order when the in-game search bar is picky or OCR is slow.

    Example: ``Giant's Codex - Mastery`` → also try ``Giant's Codex``.
    """
    seen: set[str] = set()
    out: list[str] = []

    def add(query: str) -> None:
        q = query.strip()
        if not q:
            return
        key = q.casefold()
        if key in seen:
            return
        seen.add(key)
        out.append(q)

    add(search_name)
    if " - " in search_name:
        add(search_name.split(" - ", 1)[0].strip())
    if ": " in search_name:
        add(search_name.split(": ", 1)[0].strip())
    # Long armor names — try dropping the last word (Breastplate / Armor piece).
    parts = search_name.split()
    if len(parts) >= 3:
        add(" ".join(parts[:-1]))
    return out


def collect_search_rows_with_retry(
    *,
    roi_path: Path,
    category: str,
    scanned_at: str,
    run_control: RunControl | None = None,
) -> list[dict[str, Any]]:
    """OCR search results; one short retry when the list is still loading."""
    check_stop(run_control)
    rows = collect_search_page_rows(
        roi_path=roi_path,
        category=category,
        scanned_at=scanned_at,
        run_control=run_control,
    )
    if rows:
        return rows
    sleep_checked(_SEARCH_RETRY_S, run_control=run_control)
    print("[search] retry OCR — results list was empty", flush=True)
    return collect_search_page_rows(
        roi_path=roi_path,
        category=category,
        scanned_at=scanned_at,
        run_control=run_control,
    )


def dict_to_market_row(row: dict[str, Any]) -> MarketRow:
    return MarketRow(
        page=int(row.get("page") or 1),
        row=int(row.get("row") or 0),
        item=row.get("item"),
        vendor=row.get("vendor"),
        price_adena=row.get("price_adena"),
        units=row.get("units"),
        item_icon_hash=row.get("item_icon_hash"),
        raw_text=str(row.get("raw_text") or ""),
        price_confidence=row.get("price_confidence") or "none",
        enchant=row.get("enchant"),
        item_base=row.get("item_base"),
        item_display=row.get("item_display"),
    )


def catalog_scan_phase(
    catalog: VariantCatalog,
    *,
    raw_rows: list[dict[str, Any]],
    search_query: str,
    display_name: str,
    item_id: str,
    category: str | None,
    scanned_at: str,
) -> list[str]:
    """Upsert every visible search-result variant; return new/updated item_uids."""
    entries = catalog.upsert_from_search_rows(
        rows=raw_rows,
        search_query=search_query,
        display_name=display_name,
        item_id=item_id,
        category=category,
        scanned_at=scanned_at,
    )
    return [e.item_uid for e in entries]


def pick_matched_search_row(
    raw_rows: list[dict[str, Any]],
    *,
    search_name: str,
    search_query: str | None = None,
) -> dict[str, Any] | None:
    """Pick best matching search-result row — never blind row 1."""
    if not raw_rows:
        return None

    query = search_query or search_name
    market_rows = [dict_to_market_row(r) for r in raw_rows]
    picked = find_search_result_price_row(
        market_rows, search_name, search_query=query,
    )
    if picked is None:
        picked = pick_result_row(market_rows, search_name, search_query=query)
    if picked is None:
        return None

    for row in raw_rows:
        if int(row.get("row") or 0) == picked.row:
            return dict(row)
    return picked.to_dict()


def priority_price_snapshot(
    item: ItemRef,
    matched_row: dict[str, Any] | None,
    *,
    category: str,
    scanned_at: str | None = None,
    catalog: VariantCatalog | None = None,
) -> SearchResult:
    """Build SearchResult from matched search-result row visible min price."""
    ts = scanned_at or datetime.now(timezone.utc).isoformat()
    row_conf, price_conf = score_search_row(
        matched_row,
        db_name=item.search_name,
        expected_enchant=item.enchant,
    )

    if matched_row:
        matched_row = dict(matched_row)
        matched_row["item_full_name"] = item.display_name
        matched_row["name_source"] = "db_search_query"
        matched_row["search_query"] = item.search_name
        matched_row["item_id"] = item.item_id
        matched_row["price_source"] = PRICE_SOURCE_MATCHED_ROW
        matched_row["identity_status"] = "search_confirmed"
        icon = matched_row.get("item_icon_hash")
        if icon:
            if catalog is not None:
                matched_row["item_uid"] = catalog.resolve_item_uid(
                    item_id=item.item_id,
                    icon_hash=icon,
                    category=category,
                    search_query=item.search_name,
                )
            else:
                matched_row["item_uid"] = make_item_uid(base_id=item.item_id, icon_hash=icon)

    if matched_row and matched_row.get("price_adena") is not None:
        if _price_rank(price_conf) < _price_rank("medium"):
            price_conf = "medium"
        if row_conf >= 70:
            price_conf = "high"

    result = SearchResult.from_db_row(
        item,
        matched_row,
        scanned_at=ts,
        category=category,
        row_confidence=row_conf,
        price_confidence=price_conf,
    )
    return result


def _price_rank(conf: PriceConfidence) -> int:
    return {"none": 0, "low": 1, "medium": 2, "high": 3}.get(conf, 0)

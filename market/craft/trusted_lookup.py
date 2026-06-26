"""Look up recipe materials against grouped trusted market prices."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from market.core.item_id import item_id_from_name
from market.core.models import DEFAULT_VARIANT_CATALOG_PATH
from market.craft.match import _MIN_ACCEPT_SCORE, _match_score
from market.craft.models import AVAILABILITY_AVAILABLE, AVAILABILITY_INSUFFICIENT_QTY, MaterialPrice
from market.trusted_prices import (
    DEFAULT_TRUSTED_GROUPED_CSV,
    GroupedTrustedPriceRow,
    aggregate_trusted_prices_grouped,
    collect_trusted_price_points,
)
from market.variant_catalog import VariantCatalog

_FINISHED_EXCLUDE = frozenset(
    {"shaft", "recipe", "focus", "destruct", "mastery", "discipl", "sealed"}
)


@dataclass(frozen=True)
class TrustedPriceHit:
    group_key: str
    min_price: int
    vendor: str | None
    units: int | None
    last_seen_at: str
    display_name: str | None
    identity_status: str
    match_method: str
    observation_count: int = 1

    @property
    def age_hours(self) -> float | None:
        if not self.last_seen_at:
            return None
        try:
            seen = datetime.fromisoformat(self.last_seen_at.replace("Z", "+00:00"))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - seen.astimezone(timezone.utc)
            return delta.total_seconds() / 3600.0
        except ValueError:
            return None


def _recipe_lookup_keys(item_id: str, search_name: str) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()

    def add(key: str) -> None:
        k = key.strip().casefold()
        if k and k not in seen:
            seen.add(k)
            keys.append(k)

    add(item_id)
    add(item_id_from_name(search_name))
    if item_id.endswith("_grade"):
        add(item_id.removesuffix("_grade"))
    if item_id.startswith("recipe_"):
        add(re.sub(r"^recipe_", "", item_id))
    return keys


def load_grouped_trusted_rows(
    *,
    grouped_csv: Path | None = None,
    catalog: VariantCatalog | None = None,
    rebuild: bool = False,
    resolved_bulk_path: Path | None = None,
    search_prices_path: Path | None = None,
) -> list[GroupedTrustedPriceRow]:
    """Load grouped trusted prices from CSV, or rebuild from resolver inputs."""
    if rebuild or grouped_csv is None or not grouped_csv.is_file():
        cat = catalog or VariantCatalog.load()
        points = collect_trusted_price_points(
            resolved_bulk_path=resolved_bulk_path,
            search_prices_path=search_prices_path,
        )
        return aggregate_trusted_prices_grouped(points, cat)

    rows: list[GroupedTrustedPriceRow] = []
    with grouped_csv.open(encoding="utf-8", newline="") as fh:
        for raw in csv.DictReader(fh):
            rows.append(
                GroupedTrustedPriceRow(
                    group_key=str(raw.get("group_key") or ""),
                    variant_group=raw.get("variant_group") or None,
                    display_name=raw.get("display_name") or None,
                    fungible=str(raw.get("fungible", "")).lower() in ("true", "1", "yes"),
                    min_price=int(raw.get("min_price") or 0),
                    median_price=int(raw["median_price"]) if raw.get("median_price") else None,
                    vendor=raw.get("vendor") or None,
                    units=int(raw["units"]) if raw.get("units") not in (None, "") else None,
                    source=str(raw.get("source") or ""),
                    identity_status=str(raw.get("identity_status") or ""),
                    last_seen_at=str(raw.get("last_seen_at") or ""),
                    confidence=str(raw.get("confidence") or "medium"),
                    observation_count=int(raw.get("observation_count") or 1),
                    item_uid_count=int(raw.get("item_uid_count") or 1),
                )
            )
    return [r for r in rows if r.min_price > 0]


def _name_match_pool(rows: list[GroupedTrustedPriceRow], search_name: str) -> list[GroupedTrustedPriceRow]:
    sn = search_name.casefold()
    if sn.startswith("recipe"):
        filtered = [r for r in rows if "recipe" in (r.display_name or "").casefold()]
        return filtered or rows
    if "shaft" in sn:
        filtered = [r for r in rows if "shaft" in (r.display_name or "").casefold()]
        return filtered or rows
    if "gemstone" in sn:
        filtered = [r for r in rows if "gemstone" in (r.display_name or "").casefold()]
        return filtered or rows
    if sn.startswith("crystal"):
        filtered = [r for r in rows if "crystal" in (r.display_name or "").casefold()]
        return filtered or rows
    return rows


class TrustedPriceLookup:
    def __init__(self, grouped_rows: list[GroupedTrustedPriceRow]) -> None:
        self._rows = grouped_rows
        self._by_key: dict[str, GroupedTrustedPriceRow] = {}
        for row in grouped_rows:
            self._by_key[row.group_key.casefold()] = row
            if row.variant_group and row.fungible:
                self._by_key[row.variant_group.casefold()] = row

    @classmethod
    def load(
        cls,
        *,
        grouped_csv: Path = DEFAULT_TRUSTED_GROUPED_CSV,
        catalog_path: Path = DEFAULT_VARIANT_CATALOG_PATH,
        rebuild: bool = False,
    ) -> TrustedPriceLookup:
        catalog = VariantCatalog.load(catalog_path) if rebuild else None
        rows = load_grouped_trusted_rows(grouped_csv=grouped_csv, catalog=catalog, rebuild=rebuild)
        return cls(rows)

    def __len__(self) -> int:
        return len(self._rows)

    def is_stale(self, hit: TrustedPriceHit, *, max_age_hours: float) -> bool:
        age = hit.age_hours
        return age is None or age > max_age_hours

    def lookup_material(
        self,
        *,
        item_id: str,
        search_name: str,
        qty_needed: int = 1,
    ) -> TrustedPriceHit | None:
        for key in _recipe_lookup_keys(item_id, search_name):
            row = self._by_key.get(key.casefold())
            if row is not None:
                return self._hit(row, "trusted_key", qty_needed=qty_needed)

        best_row: GroupedTrustedPriceRow | None = None
        best_score = 0
        for row in _name_match_pool(self._rows, search_name):
            label = row.display_name or row.variant_group or row.group_key
            score = _match_score(label, search_name)
            if score > best_score:
                best_score = score
                best_row = row
        if best_row is not None and best_score >= _MIN_ACCEPT_SCORE:
            return self._hit(best_row, "trusted_name", qty_needed=qty_needed)
        return None

    def lookup_finished_item(
        self,
        *,
        recipe_id: str,
        search_name: str,
    ) -> TrustedPriceHit | None:
        matches: list[GroupedTrustedPriceRow] = []
        for row in self._rows:
            vg = (row.variant_group or "").casefold()
            if vg and vg != recipe_id.casefold():
                continue
            display = (row.display_name or "").casefold()
            if any(token in display for token in _FINISHED_EXCLUDE):
                continue
            score = _match_score(row.display_name or "", search_name)
            if score >= _MIN_ACCEPT_SCORE:
                matches.append(row)
        if not matches:
            return None
        row = min(matches, key=lambda r: r.min_price)
        return self._hit(row, "trusted_finished")

    def _hit(
        self,
        row: GroupedTrustedPriceRow,
        method: str,
        *,
        qty_needed: int = 1,
    ) -> TrustedPriceHit:
        return TrustedPriceHit(
            group_key=row.group_key,
            min_price=row.min_price,
            vendor=row.vendor,
            units=row.units,
            last_seen_at=row.last_seen_at,
            display_name=row.display_name,
            identity_status=row.identity_status,
            match_method=method,
            observation_count=row.observation_count,
        )


def trusted_hit_to_material_price(
    hit: TrustedPriceHit,
    *,
    item_id: str,
    search_name: str,
    qty_needed: int,
) -> MaterialPrice:
    units = hit.units
    availability = AVAILABILITY_AVAILABLE
    note = f"via trusted ({hit.match_method})"
    if units is not None and qty_needed > 0 and units < qty_needed:
        availability = AVAILABILITY_INSUFFICIENT_QTY
        note = f"trusted listing {units} < need {qty_needed}"
    return MaterialPrice(
        item_id=item_id,
        search_name=search_name,
        unit_price_adena=hit.min_price,
        vendor=hit.vendor,
        units_available=units,
        listing_count=hit.observation_count,
        source="trusted_grouped",
        scanned_at=hit.last_seen_at,
        availability=availability,
        availability_note=note,
        cached_unit_price_adena=hit.min_price,
    )


def seed_prices_from_trusted(
    lookup: TrustedPriceLookup,
    *,
    materials: list[Any],
    qty_map: dict[str, int],
    prices: dict[str, MaterialPrice],
    max_age_hours: float,
    force_live_if_insufficient: bool = True,
) -> tuple[int, list[str]]:
    """
    Fill ``prices`` from trusted grouped CSV.

    Returns ``(hits, need_live_item_ids)`` — materials that still need vendor crawl.
    """
    hits = 0
    need_live: list[str] = []
    for mat in materials:
        qty = qty_map.get(mat.item_id, mat.qty)
        hit = lookup.lookup_material(
            item_id=mat.item_id,
            search_name=mat.search_name,
            qty_needed=qty,
        )
        if hit is None:
            need_live.append(mat.item_id)
            continue
        if lookup.is_stale(hit, max_age_hours=max_age_hours):
            need_live.append(mat.item_id)
            continue

        mp = trusted_hit_to_material_price(
            hit,
            item_id=mat.item_id,
            search_name=mat.search_name,
            qty_needed=qty,
        )
        prices[mat.item_id] = mp
        hits += 1
        if force_live_if_insufficient and mp.availability == AVAILABILITY_INSUFFICIENT_QTY:
            need_live.append(mat.item_id)
    return hits, need_live

"""Recursive min(buy, craft) cost for recipe trees."""

from __future__ import annotations

from typing import Literal

from market.craft.models import (
    AVAILABILITY_INSUFFICIENT_QTY,
    AVAILABILITY_NOT_ON_MARKET,
    CostLine,
    CraftCostReport,
    MaterialPrice,
    Recipe,
    RecipeComponent,
)

CostMode = Literal["min", "premium"]

# Prefer buying an intermediate on market when buy ≤ craft × (1 + premium).
DEFAULT_BUY_PREMIUM = 0.30


def _price_entry(prices: dict[str, MaterialPrice], item_id: str) -> MaterialPrice | None:
    return prices.get(item_id)


def _price_for(prices: dict[str, MaterialPrice], item_id: str) -> int | None:
    entry = _price_entry(prices, item_id)
    if entry is None:
        return None
    if entry.availability == AVAILABILITY_NOT_ON_MARKET:
        return None
    return entry.unit_price_adena


def _line_availability(entry: MaterialPrice | None) -> tuple[str | None, bool, str]:
    if entry is None:
        return None, False, ""
    return entry.availability, entry.price_is_stale, entry.availability_note


def _unit_cost(
    component: RecipeComponent,
    prices: dict[str, MaterialPrice],
    *,
    missing: set[str],
    mode: CostMode = "min",
    buy_premium: float = DEFAULT_BUY_PREMIUM,
) -> CostLine:
    buy = _price_for(prices, component.item_id)
    entry = _price_entry(prices, component.item_id)
    avail, stale, avail_note = _line_availability(entry)
    craft_total: int | None = None
    children: list[CostLine] = []

    if component.craft:
        child_sum = 0
        ok = True
        for child in component.craft.components:
            line = _unit_cost(
                child,
                prices,
                missing=missing,
                mode=mode,
                buy_premium=buy_premium,
            )
            children.append(line)
            if line.unit_cost < 0:
                ok = False
            else:
                child_sum += child.qty * line.unit_cost
        if ok:
            craft_total = child_sum

    if buy is None and craft_total is None:
        missing.add(component.item_id)
        unit = -1
        method = "missing"
    elif buy is None:
        unit = craft_total or 0
        method = "craft"
    elif craft_total is None:
        unit = buy
        method = "buy"
    elif mode == "premium" and buy <= int(craft_total * (1 + buy_premium)):
        unit = buy
        method = "buy"
    elif craft_total <= buy:
        unit = craft_total
        method = "craft"
    else:
        unit = buy
        method = "buy"

    return CostLine(
        item_id=component.item_id,
        search_name=component.search_name,
        qty=component.qty,
        unit_cost=max(unit, 0) if unit >= 0 else 0,
        total_cost=max(unit, 0) * component.qty if unit >= 0 else 0,
        method=method,
        buy_price=buy,
        craft_cost=craft_total,
        children=children,
        availability=avail if method in ("buy", "missing") else None,
        price_is_stale=stale and method == "buy",
        availability_note=avail_note if method in ("buy", "missing") else "",
    )


def _build_lines(
    recipe: Recipe,
    prices: dict[str, MaterialPrice],
    *,
    mode: CostMode,
    buy_premium: float,
) -> tuple[list[CostLine], set[str], int]:
    missing: set[str] = set()
    lines: list[CostLine] = []
    material_cost = 0

    for component in recipe.components:
        line = _unit_cost(
            component,
            prices,
            missing=missing,
            mode=mode,
            buy_premium=buy_premium,
        )
        lines.append(line)
        if line.unit_cost >= 0:
            material_cost += line.total_cost

    return lines, missing, material_cost


def _premium_pct_vs(base: int, other: int) -> float:
    if base <= 0 or other == base:
        return 0.0
    return round(100 * (other - base) / base, 1)


def _collect_price_issues(
    recipe: Recipe,
    prices: dict[str, MaterialPrice],
    lines: list[CostLine],
) -> tuple[list[dict], list[dict], bool]:
    unavailable: list[dict] = []
    stale: list[dict] = []
    complete = True

    for line in lines:
        entry = _price_entry(prices, line.item_id)
        if line.method == "missing" or (
            line.method == "buy"
            and entry is not None
            and entry.availability == AVAILABILITY_NOT_ON_MARKET
        ):
            complete = False
            unavailable.append(
                {
                    "item_id": line.item_id,
                    "search_name": line.search_name,
                    "qty": line.qty,
                    "availability": entry.availability if entry else "scan_uncertain",
                    "note": (entry.availability_note if entry else "") or "no price",
                    "cached_unit_price_adena": entry.cached_unit_price_adena if entry else None,
                }
            )
        elif line.price_is_stale and line.buy_price is not None:
            stale.append(
                {
                    "item_id": line.item_id,
                    "search_name": line.search_name,
                    "qty": line.qty,
                    "unit_price_adena": line.buy_price,
                    "note": line.availability_note,
                    "scanned_at": entry.scanned_at if entry else "",
                }
            )
        elif line.method == "buy" and entry is not None and entry.availability == AVAILABILITY_INSUFFICIENT_QTY:
            complete = False
            stale.append(
                {
                    "item_id": line.item_id,
                    "search_name": line.search_name,
                    "qty": line.qty,
                    "unit_price_adena": line.buy_price,
                    "note": entry.availability_note,
                    "scanned_at": entry.scanned_at,
                }
            )

    return unavailable, stale, complete


def compute_craft_cost(
    recipe: Recipe,
    prices: dict[str, MaterialPrice],
    *,
    finished_bow_buy_price: int | None = None,
    buy_premium: float = DEFAULT_BUY_PREMIUM,
) -> CraftCostReport:
    lines, missing_min, material_cost = _build_lines(
        recipe, prices, mode="min", buy_premium=buy_premium
    )
    conv_lines, missing_conv, conv_material = _build_lines(
        recipe, prices, mode="premium", buy_premium=buy_premium
    )
    missing = sorted(missing_min | missing_conv)
    unavailable, stale_items, materials_complete = _collect_price_issues(recipe, prices, lines)

    cost_per_attempt = recipe.adena_fee + material_cost
    conv_cost_per_attempt = recipe.adena_fee + conv_material
    rate = recipe.success_rate if recipe.success_rate > 0 else 1.0
    expected = int(cost_per_attempt / rate) if cost_per_attempt > 0 else 0
    conv_expected = int(conv_cost_per_attempt / rate) if conv_cost_per_attempt > 0 else 0

    return CraftCostReport(
        recipe_id=recipe.recipe_id,
        recipe_name=recipe.search_name,
        success_rate=recipe.success_rate,
        adena_fee=recipe.adena_fee,
        material_cost=material_cost,
        cost_per_attempt=cost_per_attempt,
        expected_cost_per_success=expected,
        lines=lines,
        missing_prices=missing,
        unavailable_items=unavailable,
        stale_price_items=stale_items,
        materials_complete=materials_complete,
        finished_bow_buy_price=finished_bow_buy_price,
        convenience_lines=conv_lines,
        convenience_material_cost=conv_material,
        convenience_cost_per_attempt=conv_cost_per_attempt,
        convenience_expected_cost_per_success=conv_expected,
        convenience_premium_pct=_premium_pct_vs(material_cost, conv_material),
        buy_premium_threshold=buy_premium,
    )

"""Recursive min(buy, craft) cost for recipe trees."""

from __future__ import annotations

from market.craft.models import CostLine, CraftCostReport, MaterialPrice, Recipe, RecipeComponent


def _price_for(prices: dict[str, MaterialPrice], item_id: str) -> int | None:
    entry = prices.get(item_id)
    if entry is None:
        return None
    return entry.unit_price_adena


def _unit_cost(
    component: RecipeComponent,
    prices: dict[str, MaterialPrice],
    *,
    missing: set[str],
) -> CostLine:
    buy = _price_for(prices, component.item_id)
    craft_total: int | None = None
    children: list[CostLine] = []

    if component.craft:
        child_sum = 0
        ok = True
        for child in component.craft.components:
            line = _unit_cost(child, prices, missing=missing)
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
    )


def compute_craft_cost(
    recipe: Recipe,
    prices: dict[str, MaterialPrice],
    *,
    finished_bow_buy_price: int | None = None,
) -> CraftCostReport:
    missing: set[str] = set()
    lines: list[CostLine] = []
    material_cost = 0

    for component in recipe.components:
        line = _unit_cost(component, prices, missing=missing)
        lines.append(line)
        if line.unit_cost >= 0:
            material_cost += line.total_cost

    cost_per_attempt = recipe.adena_fee + material_cost
    rate = recipe.success_rate if recipe.success_rate > 0 else 1.0
    expected = int(cost_per_attempt / rate) if cost_per_attempt > 0 else 0

    return CraftCostReport(
        recipe_id=recipe.recipe_id,
        recipe_name=recipe.search_name,
        success_rate=recipe.success_rate,
        adena_fee=recipe.adena_fee,
        material_cost=material_cost,
        cost_per_attempt=cost_per_attempt,
        expected_cost_per_success=expected,
        lines=lines,
        missing_prices=sorted(missing),
        finished_bow_buy_price=finished_bow_buy_price,
    )

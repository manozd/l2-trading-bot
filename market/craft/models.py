"""Craft recipe domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RecipeComponent:
    item_id: str
    search_name: str
    qty: int
    craft: RecipeComponentTree | None = None
    search_queries: tuple[str, ...] = ()

    def effective_search_queries(self) -> tuple[str, ...]:
        if self.search_queries:
            return self.search_queries
        return (self.search_name,)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> RecipeComponent:
        craft_raw = data.get("craft")
        craft = RecipeComponentTree.from_dict(craft_raw) if craft_raw else None
        raw_queries = data.get("search_queries")
        queries: tuple[str, ...] = ()
        if isinstance(raw_queries, list) and raw_queries:
            queries = tuple(str(q) for q in raw_queries)
        return RecipeComponent(
            item_id=str(data["item_id"]),
            search_name=str(data["search_name"]),
            qty=int(data["qty"]),
            craft=craft,
            search_queries=queries,
        )


@dataclass(frozen=True)
class RecipeComponentTree:
    components: tuple[RecipeComponent, ...]

    @staticmethod
    def from_dict(data: dict[str, Any]) -> RecipeComponentTree:
        raw = data.get("components") or []
        return RecipeComponentTree(
            components=tuple(RecipeComponent.from_dict(c) for c in raw),
        )


@dataclass(frozen=True)
class Recipe:
    recipe_id: str
    search_name: str
    grade: str
    manufact_level: int
    mp_cost: int
    success_rate: float
    adena_fee: int
    components: tuple[RecipeComponent, ...]
    notes: str = ""
    search_queries: tuple[str, ...] = ()

    def effective_search_queries(self) -> tuple[str, ...]:
        if self.search_queries:
            return self.search_queries
        return (self.search_name,)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Recipe:
        raw_queries = data.get("search_queries")
        queries: tuple[str, ...] = ()
        if isinstance(raw_queries, list) and raw_queries:
            queries = tuple(str(q) for q in raw_queries)
        return Recipe(
            recipe_id=str(data["recipe_id"]),
            search_name=str(data["search_name"]),
            grade=str(data.get("grade", "")),
            manufact_level=int(data.get("manufact_level", 0)),
            mp_cost=int(data.get("mp_cost", 0)),
            success_rate=float(data.get("success_rate", 1.0)),
            adena_fee=int(data.get("adena_fee", 0)),
            components=tuple(
                RecipeComponent.from_dict(c) for c in (data.get("components") or [])
            ),
            notes=str(data.get("notes", "")),
            search_queries=queries,
        )


@dataclass
class MaterialPrice:
    item_id: str
    search_name: str
    unit_price_adena: int | None
    vendor: str | None = None
    units_available: int | None = None
    listing_count: int = 0
    source: str = "vendor_search"
    scanned_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "search_name": self.search_name,
            "unit_price_adena": self.unit_price_adena,
            "vendor": self.vendor,
            "units_available": self.units_available,
            "listing_count": self.listing_count,
            "source": self.source,
            "scanned_at": self.scanned_at,
        }


@dataclass
class CostLine:
    item_id: str
    search_name: str
    qty: int
    unit_cost: int
    total_cost: int
    method: str
    buy_price: int | None
    craft_cost: int | None
    children: list[CostLine] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "search_name": self.search_name,
            "qty": self.qty,
            "unit_cost": self.unit_cost,
            "total_cost": self.total_cost,
            "method": self.method,
            "buy_price": self.buy_price,
            "craft_cost": self.craft_cost,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class CraftCostReport:
    recipe_id: str
    recipe_name: str
    success_rate: float
    adena_fee: int
    material_cost: int
    cost_per_attempt: int
    expected_cost_per_success: int
    lines: list[CostLine]
    missing_prices: list[str]
    finished_bow_buy_price: int | None = None
    convenience_lines: list[CostLine] | None = None
    convenience_material_cost: int = 0
    convenience_cost_per_attempt: int = 0
    convenience_expected_cost_per_success: int = 0
    convenience_premium_pct: float = 0.0
    buy_premium_threshold: float = 0.30

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "recipe_id": self.recipe_id,
            "recipe_name": self.recipe_name,
            "success_rate": self.success_rate,
            "adena_fee": self.adena_fee,
            "material_cost": self.material_cost,
            "cost_per_attempt": self.cost_per_attempt,
            "expected_cost_per_success": self.expected_cost_per_success,
            "finished_bow_buy_price": self.finished_bow_buy_price,
            "missing_prices": self.missing_prices,
            "lines": [ln.to_dict() for ln in self.lines],
            "buy_premium_threshold": self.buy_premium_threshold,
        }
        if self.convenience_lines is not None:
            out["convenience"] = {
                "material_cost": self.convenience_material_cost,
                "cost_per_attempt": self.convenience_cost_per_attempt,
                "expected_cost_per_success": self.convenience_expected_cost_per_success,
                "premium_pct_vs_min": self.convenience_premium_pct,
                "lines": [ln.to_dict() for ln in self.convenience_lines],
            }
        return out

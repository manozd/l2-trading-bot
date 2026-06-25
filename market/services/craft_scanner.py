"""On-demand craft price fetch + cost report."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from market.capture_rois import REGION_BACK_BUTTON, REGION_SEARCH_BOX, load_market_roi_config
from market.constants import DEFAULT_PICO_COM
from market.countdown import wait_before_start
from market.craft.cost import compute_craft_cost
from market.craft.models import CostLine, CraftCostReport, MaterialPrice, Recipe, RecipeComponent
from market.craft.recipe_db import collect_material_qty_map, collect_unique_materials, load_recipe_by_id
from market.craft.vendor_search import (
    CRAFT_BACK_SETTLE_S,
    CRAFT_SEARCH_SETTLE_S,
    fetch_material_vendor_price,
)
from market.pico_hid import PicoHidSerial
from market.run_control import RunControl, StopRequested

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPES_DIR = PROJECT_ROOT / "config" / "recipes"
DEFAULT_CRAFT_PRICES_DIR = PROJECT_ROOT / "logs" / "craft_prices"


def craft_prices_path(recipe_id: str, *, out_dir: Path = DEFAULT_CRAFT_PRICES_DIR) -> Path:
    return out_dir / f"{recipe_id}.json"


def load_cached_prices(path: Path) -> dict[str, MaterialPrice]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    prices: dict[str, MaterialPrice] = {}
    for item_id, raw in (data.get("prices") or {}).items():
        if isinstance(raw, dict):
            prices[str(item_id)] = MaterialPrice(**raw)
    return prices


def save_prices_cache(
    path: Path,
    *,
    recipe_id: str,
    prices: dict[str, MaterialPrice],
    report: CraftCostReport | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "recipe_id": recipe_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "prices": {k: v.to_dict() for k, v in sorted(prices.items())},
    }
    if report is not None:
        payload["last_report"] = report.to_dict()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def format_adena(n: int | None) -> str:
    if n is None:
        return "-"
    return f"{n:,}"


def _print_buy_leaves(line: CostLine, *, indent: int, qty_mult: int) -> None:
    need = line.qty * qty_mult
    if line.method == "buy":
        unit = format_adena(line.buy_price)
        total = format_adena((line.buy_price or 0) * need)
        print(
            f"{'  ' * indent}BUY {need}x {line.search_name} @ {unit}/ea = {total}",
            flush=True,
        )
        return
    if line.method == "craft":
        for child in line.children:
            _print_buy_leaves(child, indent=indent, qty_mult=need)
        return
    print(f"{'  ' * indent}? {need}x {line.search_name} — no market price", flush=True)


def _print_component_plan(line: CostLine) -> None:
    if line.method == "buy":
        print(
            f"  {line.search_name} x{line.qty}: BUY @ {format_adena(line.buy_price)}/ea "
            f"→ {format_adena(line.total_cost)}",
            flush=True,
        )
        return
    if line.method == "craft":
        print(
            f"  {line.search_name} x{line.qty}: CRAFT @ {format_adena(line.craft_cost)}/ea "
            f"→ {format_adena(line.total_cost)} (buy materials below)",
            flush=True,
        )
        for child in line.children:
            _print_buy_leaves(child, indent=2, qty_mult=line.qty)
        return
    print(f"  {line.search_name} x{line.qty}: MISSING PRICE", flush=True)


def print_craft_report(report: CraftCostReport) -> None:
    print(f"\n=== Craft cost: {report.recipe_name} ===", flush=True)
    print(f"  Success rate:     {report.success_rate * 100:.0f}%", flush=True)
    print(f"  Adena fee:        {format_adena(report.adena_fee)}", flush=True)
    print(f"  Materials:        {format_adena(report.material_cost)}", flush=True)
    print(f"  Per attempt:      {format_adena(report.cost_per_attempt)}", flush=True)
    print(f"  Expected/success: {format_adena(report.expected_cost_per_success)}", flush=True)
    if report.finished_bow_buy_price is not None:
        print(f"  Buy finished item: {format_adena(report.finished_bow_buy_price)}", flush=True)
        if report.expected_cost_per_success > 0:
            if report.finished_bow_buy_price < report.expected_cost_per_success:
                print("  → Cheaper to BUY finished item than craft", flush=True)
            else:
                print("  → Cheaper to CRAFT than buy finished item", flush=True)
    else:
        print("  Buy finished item: not available (search failed or not scanned)", flush=True)
    if report.missing_prices:
        print(f"  Missing prices:   {len(report.missing_prices)} items", flush=True)
        for mid in report.missing_prices[:10]:
            print(f"    - {mid}", flush=True)
        if len(report.missing_prices) > 10:
            print(f"    ... +{len(report.missing_prices) - 10} more", flush=True)
    print("\n  --- Minimum cost plan (per successful craft) ---", flush=True)
    for line in report.lines:
        _print_component_plan(line)
    print("", flush=True)


class CraftPriceScanner:
    def __init__(
        self,
        *,
        recipe_id: str,
        roi_path: Path,
        pico_com: str = DEFAULT_PICO_COM,
        recipes_dir: Path = DEFAULT_RECIPES_DIR,
        prices_dir: Path = DEFAULT_CRAFT_PRICES_DIR,
        start_delay_s: float = 10.0,
        search_settle_s: float = CRAFT_SEARCH_SETTLE_S,
        back_settle_s: float = CRAFT_BACK_SETTLE_S,
        input_mode: str = "pico",
        limit: int = 0,
        dry_run: bool = False,
        fetch: bool = True,
        include_finished_bow: bool = True,
        run_control: RunControl | None = None,
    ) -> None:
        self.recipe_id = recipe_id
        self.roi_path = roi_path
        self.pico_com = pico_com
        self.recipes_dir = recipes_dir
        self.prices_path = craft_prices_path(recipe_id, out_dir=prices_dir)
        self.start_delay_s = start_delay_s
        self.search_settle_s = search_settle_s
        self.back_settle_s = back_settle_s
        self.input_mode = input_mode
        self.limit = limit
        self.dry_run = dry_run
        self.fetch = fetch
        self.include_finished_bow = include_finished_bow
        self._run_control = run_control

    def run(self) -> CraftCostReport:
        recipe = load_recipe_by_id(self.recipe_id, recipes_dir=self.recipes_dir)
        materials = collect_unique_materials(recipe)
        if self.limit:
            materials = materials[: self.limit]

        prices = load_cached_prices(self.prices_path)
        finished_price: int | None = None

        if self.fetch and self.dry_run:
            print("[craft-cost] dry-run — would fetch:", flush=True)
            for mat in materials:
                print(f"  - {mat.search_name!r} ({mat.item_id})", flush=True)
            if self.include_finished_bow:
                print(f"  - finished: {recipe.search_name!r}", flush=True)
        elif self.fetch:
            finished_price = self._fetch_live_prices(materials, prices, recipe)
            save_prices_cache(self.prices_path, recipe_id=self.recipe_id, prices=prices)
        elif not prices:
            print(
                f"[craft-cost] no cached prices at {self.prices_path}\n"
                "  Run with --fetch (game + Pico) to collect vendor prices.",
                flush=True,
            )

        report = compute_craft_cost(
            recipe,
            prices,
            finished_bow_buy_price=finished_price,
        )
        if finished_price is None and self.include_finished_bow:
            cached_finished = prices.get(f"{self.recipe_id}_finished")
            if cached_finished and cached_finished.unit_price_adena is not None:
                finished_price = cached_finished.unit_price_adena
                report = compute_craft_cost(
                    recipe,
                    prices,
                    finished_bow_buy_price=finished_price,
                )
        save_prices_cache(
            self.prices_path,
            recipe_id=self.recipe_id,
            prices=prices,
            report=report,
        )
        print_craft_report(report)
        print(f"[craft-cost] cache → {self.prices_path.resolve()}", flush=True)
        return report

    def _fetch_live_prices(
        self,
        materials: list[RecipeComponent],
        prices: dict[str, MaterialPrice],
        recipe: Recipe,
    ) -> int | None:
        if not self.pico_com:
            raise SystemExit(f"Pico port required (default: {DEFAULT_PICO_COM})")

        roi = load_market_roi_config(self.roi_path)
        search = roi.require(REGION_SEARCH_BOX)
        back = roi.require(REGION_BACK_BUTTON)

        if self.start_delay_s > 0:
            wait_before_start(self.start_delay_s, tag="craft-cost", run_control=self._run_control)
        finished_price: int | None = None
        qty_map = collect_material_qty_map(recipe)
        pico = PicoHidSerial(self.pico_com)
        try:
            for i, mat in enumerate(materials, start=1):
                if self._run_control and self._run_control.should_stop():
                    print("[craft-cost] stop requested — finishing after current item", flush=True)
                    break
                print(f"[craft-cost] ({i}/{len(materials)}) {mat.search_name!r}", flush=True)
                try:
                    price = fetch_material_vendor_price(
                        item_id=mat.item_id,
                        search_name=mat.search_name,
                        search_queries=list(mat.effective_search_queries()),
                        qty_needed=qty_map.get(mat.item_id, mat.qty),
                        roi_path=self.roi_path,
                        pico=pico,
                        search=search,
                        back=back,
                        search_settle_s=self.search_settle_s,
                        back_settle_s=self.back_settle_s,
                        input_mode=self.input_mode,
                        run_control=self._run_control,
                    )
                except StopRequested:
                    print("[craft-cost] stop requested — aborting scan", flush=True)
                    break
                except Exception as exc:
                    print(f"[craft-cost] skip {mat.search_name!r}: {exc}", flush=True)
                    continue
                if self._run_control and self._run_control.should_stop():
                    print("[craft-cost] stop requested — aborting scan", flush=True)
                    break
                prices[mat.item_id] = price

            if self.include_finished_bow and not (self._run_control and self._run_control.should_stop()):
                print(f"[craft-cost] finished item {recipe.search_name!r}", flush=True)
                try:
                    bow_price = fetch_material_vendor_price(
                        item_id=f"{self.recipe_id}_finished",
                        search_name=recipe.search_name,
                        search_queries=list(recipe.effective_search_queries()),
                        qty_needed=1,
                        roi_path=self.roi_path,
                        pico=pico,
                        search=search,
                        back=back,
                        search_settle_s=self.search_settle_s,
                        back_settle_s=self.back_settle_s,
                        input_mode=self.input_mode,
                        run_control=self._run_control,
                    )
                    if bow_price.unit_price_adena is not None:
                        finished_price = bow_price.unit_price_adena
                        prices[f"{self.recipe_id}_finished"] = bow_price
                        print(
                            f"[craft-cost] finished {recipe.search_name!r} buy price: "
                            f"{finished_price:,} adena",
                            flush=True,
                        )
                    else:
                        print(
                            f"[craft-cost] finished {recipe.search_name!r}: no vendor price found",
                            flush=True,
                        )
                except StopRequested:
                    print("[craft-cost] stop requested — aborting scan", flush=True)
                except Exception as exc:
                    print(f"[craft-cost] skip finished {recipe.search_name!r}: {exc}", flush=True)
        finally:
            pico.close()

        return finished_price

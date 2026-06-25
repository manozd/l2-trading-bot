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


def _buy_vs_craft_note(line: CostLine) -> str:
    """Premium/discount of buy vs craft unit cost, when both are known."""
    return _premium_note(line.buy_price, line.craft_cost)


def _premium_note(buy: int | None, craft: int | None) -> str:
    if buy is None or craft is None or craft <= 0:
        return ""
    delta = buy - craft
    pct = 100 * delta / craft
    if abs(pct) < 0.5:
        return " (buy ≈ craft)"
    if pct > 0:
        return f" (buy +{pct:.0f}% vs craft {format_adena(craft)}/ea)"
    return f" (buy {pct:.0f}% vs craft {format_adena(craft)}/ea)"


def _print_buy_leaves(
    line: CostLine,
    *,
    indent: int,
    qty_mult: int,
    show_diy_alt: bool = False,
) -> None:
    need = line.qty * qty_mult
    if line.method == "buy":
        unit = format_adena(line.buy_price)
        total = format_adena((line.buy_price or 0) * need)
        note = _premium_note(line.buy_price, line.craft_cost) if show_diy_alt else ""
        print(
            f"{'  ' * indent}BUY {need}x {line.search_name} @ {unit}/ea = {total}{note}",
            flush=True,
        )
        if show_diy_alt and line.children and line.craft_cost is not None:
            print(
                f"{'  ' * indent}  ↳ or craft from raw mats (~{format_adena(line.craft_cost)}/ea):",
                flush=True,
            )
            for child in line.children:
                _print_buy_leaves(child, indent=indent + 2, qty_mult=need, show_diy_alt=False)
        return
    if line.method == "craft":
        for child in line.children:
            _print_buy_leaves(child, indent=indent, qty_mult=need, show_diy_alt=show_diy_alt)
        return
    print(f"{'  ' * indent}? {need}x {line.search_name} — no market price", flush=True)


def _print_component_plan(
    line: CostLine,
    *,
    compare_line: CostLine | None = None,
    buy_premium_threshold: float | None = None,
) -> None:
    note = _buy_vs_craft_note(line)
    if line.method == "buy":
        alt = ""
        if compare_line is not None and compare_line.method != line.method:
            alt = f" [min plan: {compare_line.method.upper()}]"
        print(
            f"  {line.search_name} x{line.qty}: BUY @ {format_adena(line.buy_price)}/ea"
            f"{note} → {format_adena(line.total_cost)}{alt}",
            flush=True,
        )
        return
    if line.method == "craft":
        alt = ""
        if compare_line is not None and compare_line.method == "buy":
            alt = (
                f" [time-saver: BUY @ {format_adena(compare_line.buy_price)}/ea"
                f"{_buy_vs_craft_note(compare_line)}]"
            )
        elif (
            buy_premium_threshold is not None
            and line.buy_price is not None
            and line.craft_cost is not None
            and line.buy_price > int(line.craft_cost * (1 + buy_premium_threshold))
        ):
            alt = (
                f" [market buy {format_adena(line.buy_price)}/ea"
                f"{_premium_note(line.buy_price, line.craft_cost)}"
                f" — above +{buy_premium_threshold * 100:.0f}% time-saver limit]"
            )
        print(
            f"  {line.search_name} x{line.qty}: CRAFT @ {format_adena(line.craft_cost)}/ea "
            f"→ {format_adena(line.total_cost)} (buy materials below){alt}",
            flush=True,
        )
        for child in line.children:
            _print_buy_leaves(child, indent=2, qty_mult=line.qty, show_diy_alt=False)
        return
    print(f"  {line.search_name} x{line.qty}: MISSING PRICE", flush=True)


def _print_component_plan_conv(line: CostLine, *, compare_line: CostLine | None) -> None:
    """Time-saver plan line printer (shows DIY alternatives for bought intermediates)."""
    note = _buy_vs_craft_note(line)
    if line.method == "buy":
        alt = ""
        if compare_line is not None and compare_line.method != line.method:
            alt = f" [min plan: {compare_line.method.upper()}]"
        print(
            f"  {line.search_name} x{line.qty}: BUY @ {format_adena(line.buy_price)}/ea"
            f"{note} → {format_adena(line.total_cost)}{alt}",
            flush=True,
        )
        return
    if line.method == "craft":
        alt = ""
        if compare_line is not None and compare_line.method == "buy":
            alt = (
                f" [min plan: BUY finished @ {format_adena(compare_line.buy_price)}/ea"
                f"{_buy_vs_craft_note(compare_line)}]"
            )
        print(
            f"  {line.search_name} x{line.qty}: CRAFT @ {format_adena(line.craft_cost)}/ea "
            f"→ {format_adena(line.total_cost)} (buy/craft materials below){alt}",
            flush=True,
        )
        for child in line.children:
            _print_buy_leaves(child, indent=2, qty_mult=line.qty, show_diy_alt=True)
        return
    print(f"  {line.search_name} x{line.qty}: MISSING PRICE", flush=True)


def _collect_plan_diffs(
    min_ln: CostLine,
    conv_ln: CostLine,
    *,
    prefix: str = "",
) -> list[str]:
    path = f"{prefix}{min_ln.search_name}"
    rows: list[str] = []
    if min_ln.method != conv_ln.method:
        if conv_ln.method == "buy" and min_ln.method == "craft":
            note = _buy_vs_craft_note(conv_ln)
            extra_unit = conv_ln.unit_cost - min_ln.unit_cost
            rows.append(
                f"  {path}: CRAFT → BUY{note} "
                f"(+{format_adena(extra_unit)}/ea, skip nested crafting)"
            )
        elif conv_ln.method == "craft" and min_ln.method == "buy":
            rows.append(
                f"  {path}: BUY → CRAFT "
                f"(saves {format_adena(min_ln.total_cost - conv_ln.total_cost)})"
            )
    min_by_id = {c.item_id: c for c in min_ln.children}
    for cv_child in conv_ln.children:
        mn_child = min_by_id.get(cv_child.item_id)
        if mn_child is not None:
            rows.extend(
                _collect_plan_diffs(mn_child, cv_child, prefix=f"{path} → ")
            )
    return rows


def _print_plan_diff(min_lines: list[CostLine], conv_lines: list[CostLine]) -> None:
    switches: list[str] = []
    conv_by_id = {ln.item_id: ln for ln in conv_lines}
    for mn in min_lines:
        cv = conv_by_id.get(mn.item_id)
        if cv is not None:
            switches.extend(_collect_plan_diffs(mn, cv))
    if switches:
        print("\n  Changed vs minimum plan:", flush=True)
        for row in switches:
            print(row, flush=True)


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
    print("\n  --- Minimum adena plan (craft when cheaper) ---", flush=True)
    for line in report.lines:
        _print_component_plan(line, buy_premium_threshold=report.buy_premium_threshold)

    if report.convenience_lines is not None:
        pct = report.buy_premium_threshold * 100
        print(
            f"\n  --- Time-saver plan (buy intermediate if market ≤ +{pct:.0f}% vs craft) ---",
            flush=True,
        )
        premium_note = (
            f"+{report.convenience_premium_pct}% vs minimum"
            if report.convenience_premium_pct > 0
            else "same as minimum"
        )
        print(
            f"  Materials:        {format_adena(report.convenience_material_cost)} ({premium_note})",
            flush=True,
        )
        delta = report.convenience_expected_cost_per_success - report.expected_cost_per_success
        delta_note = f"+{format_adena(delta)} vs minimum" if delta > 0 else "same as minimum"
        print(
            f"  Expected/success: {format_adena(report.convenience_expected_cost_per_success)} "
            f"({delta_note})",
            flush=True,
        )
        conv_by_id = {ln.item_id: ln for ln in report.convenience_lines}
        min_by_id = {ln.item_id: ln for ln in report.lines}
        for line in report.convenience_lines:
            _print_component_plan_conv(line, compare_line=min_by_id.get(line.item_id))
        _print_plan_diff(report.lines, report.convenience_lines)

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
                        fast=True,
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
                        fast=True,
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

"""Hotkey-driven daemon: pause by default, calibrate and run via global keys."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from market.capture_rois import (
    DEFAULT_MARKET_ROI_PATH,
    REGION_MARKET_WINDOW,
    load_market_roi_config,
)
from market.constants import DEFAULT_PICO_COM
from market.core.models import BulkRunConfig, SearchRunConfig
from market.countdown import wait_before_start
from market.craft.recipe_db import (
    collect_unique_materials,
    find_recipes_by_query,
    load_all_recipes,
)
from market.daemon_prompt import prompt_recipe_name
from market.catalog import DEFAULT_TARGET_LISTS, load_target_list_refs
from market.roi_calibrate import run_region_calibration
from market.run_control import RunControl, StopRequested
from market.search_input import INPUT_PICO
from market.services.bulk_scanner import BulkScanner
from market.services.craft_scanner import CraftPriceScanner, DEFAULT_CRAFT_PRICES_DIR, DEFAULT_RECIPES_DIR
from market.services.search_scanner import SearchScanner

Mode = Literal["search", "bulk", "craft"]
State = Literal["paused", "running", "calibrating"]

_CALIB_BY_HOTKEY: dict[str, str] = {
    "calib:1": REGION_MARKET_WINDOW,
    "calib:2": REGION_MARKET_WINDOW,
    "calib:3": REGION_MARKET_WINDOW,
    "calib:4": REGION_MARKET_WINDOW,
}


@dataclass
class DaemonConfig:
    roi_path: Path = DEFAULT_MARKET_ROI_PATH
    pico_com: str = DEFAULT_PICO_COM
    start_delay_s: float = 10.0
    calibrate_delay_s: float = 2.0
    monitor: int | None = None
    live_alpha: float = 0.5
    bulk_category: str = "all_items"
    bulk_pages: int = 200
    bulk_page_delay_s: float = 0.45
    bulk_vendor_page_delay_s: float = 0.2
    bulk_max_vendor_pages: int = 1
    search_resume: bool = True
    search_targets: Path = DEFAULT_TARGET_LISTS
    search_category: str = ""
    craft_recipes_dir: Path = DEFAULT_RECIPES_DIR
    craft_prices_dir: Path = DEFAULT_CRAFT_PRICES_DIR


class MarketDaemon:
    def __init__(self, config: DaemonConfig) -> None:
        self.config = config
        self._queue: queue.Queue[str] = queue.Queue()
        self._mode: Mode | None = None
        self._state: State = "paused"
        self._run_control = RunControl()
        self._worker: threading.Thread | None = None
        self._hotkeys: list = []
        self._craft_recipe_id: str | None = None
        self._craft_recipe_name: str | None = None

    def run(self) -> None:
        try:
            import keyboard
        except ImportError as e:
            raise SystemExit(
                "Global hotkeys require the keyboard package.\n"
                "Install: pip install keyboard\n"
                "On Windows, run the terminal as Administrator if hotkeys do not fire in-game."
            ) from e

        self._register_hotkeys(keyboard)
        self._print_banner()
        try:
            while True:
                try:
                    action = self._queue.get(timeout=0.25)
                except queue.Empty:
                    self._check_worker_done()
                    continue
                self._handle(action)
        except KeyboardInterrupt:
            print("\n[daemon] Ctrl+C — shutting down", flush=True)
        finally:
            self._stop_worker(wait=True)
            keyboard.unhook_all()

    def _register_hotkeys(self, keyboard) -> None:
        bind = keyboard.add_hotkey
        self._hotkeys = [
            bind("c+1", lambda: self._enqueue("calib:1"), suppress=False),
            bind("c+2", lambda: self._enqueue("calib:2"), suppress=False),
            bind("c+3", lambda: self._enqueue("calib:3"), suppress=False),
            bind("c+4", lambda: self._enqueue("calib:4"), suppress=False),
            bind("m+1", lambda: self._enqueue("mode:bulk"), suppress=False),
            bind("m+2", lambda: self._enqueue("mode:search"), suppress=False),
            bind("m+3", lambda: self._enqueue("mode:craft"), suppress=False),
            bind("f12", lambda: self._on_f12(), suppress=False),
        ]

    def _on_f12(self) -> None:
        """F12: stop immediately when running (do not wait for queue poll)."""
        if self._state == "running":
            print("[daemon] F12 — stop requested (aborting run …)", flush=True)
            self._run_control.request_stop()
            return
        if self._state == "calibrating":
            print("[daemon] F12 — stop during calibration (finish overlay or Ctrl+C)", flush=True)
            self._run_control.request_stop()
            return
        self._enqueue("toggle")

    def _enqueue(self, action: str) -> None:
        self._queue.put(action)

    def _handle(self, action: str) -> None:
        if action == "toggle":
            self._toggle_run()
            return

        if action.startswith("calib:"):
            region = _CALIB_BY_HOTKEY.get(action)
            if region is None:
                return
            if self._state == "running":
                print("[daemon] stopping run for calibration …", flush=True)
                self._stop_worker(wait=True)
            self._run_calibration(region)
            return

        if action == "mode:search":
            if self._state != "paused":
                print("[daemon] mode change ignored while running", flush=True)
                return
            self._mode = "search"
            self._print_search_mode_hint()
            self._print_status()
            return

        if action == "mode:bulk":
            if self._state != "paused":
                print("[daemon] mode change ignored while running", flush=True)
                return
            self._mode = "bulk"
            print(
                "[daemon] M+1 bulk crawl — open full market list, then F12 "
                "(auto resolve-bulk + trusted-prices after crawl)",
                flush=True,
            )
            self._print_status()
            return

        if action == "mode:craft":
            if self._state != "paused":
                print("[daemon] mode change ignored while running", flush=True)
                return
            self._select_craft_mode()
            return

    def _toggle_run(self) -> None:
        if self._state == "running":
            self._run_control.request_stop()
            return

        if self._mode is None:
            print(
                "[daemon] F12 — select mode first: M+1 bulk, M+2 priority monitor, M+3 craft",
                flush=True,
            )
            return

        self._run_control.clear()
        self._state = "running"
        self._print_status(starting=True)
        self._worker = threading.Thread(target=self._worker_main, name=f"cli-{self._mode}", daemon=True)
        self._worker.start()

    def _worker_main(self) -> None:
        cfg = self.config
        try:
            if self._mode == "search":
                wait_before_start(cfg.start_delay_s, tag="search", run_control=self._run_control)
                targets_path = cfg.search_targets.resolve()
                load_target_list_refs(targets_path)  # fail fast before countdown if missing
                search_cfg = SearchRunConfig(
                    roi_path=cfg.roi_path,
                    target_lists=targets_path,
                    pico_com=cfg.pico_com,
                    input_mode=INPUT_PICO,
                    start_delay_s=0.0,
                    resume=cfg.search_resume,
                    category=cfg.search_category.strip() or "search",
                )
                SearchScanner(
                    search_cfg,
                    target_lists=targets_path,
                    run_control=self._run_control,
                ).run()
            elif self._mode == "bulk":
                wait_before_start(cfg.start_delay_s, tag="bulk", run_control=self._run_control)
                print(
                    "[daemon] bulk crawl — open the full market list in-game before countdown ends",
                    flush=True,
                )
                bulk_cfg = BulkRunConfig(
                    roi_path=cfg.roi_path,
                    pico_com=cfg.pico_com,
                    category=cfg.bulk_category,
                    pages=cfg.bulk_pages,
                    page_delay_s=cfg.bulk_page_delay_s,
                    vendor_page_delay_s=cfg.bulk_vendor_page_delay_s,
                    max_vendor_pages=cfg.bulk_max_vendor_pages,
                    start_delay_s=0.0,
                )
                BulkScanner(bulk_cfg, run_control=self._run_control).run()
            elif self._mode == "craft":
                recipe_id = self._craft_recipe_id
                if not recipe_id:
                    print("[daemon] craft recipe not selected — press M+3 first", flush=True)
                    return
                try:
                    wait_before_start(cfg.start_delay_s, tag="craft-cost", run_control=self._run_control)
                    CraftPriceScanner(
                        recipe_id=recipe_id,
                        roi_path=cfg.roi_path,
                        pico_com=cfg.pico_com,
                        recipes_dir=cfg.craft_recipes_dir,
                        prices_dir=cfg.craft_prices_dir,
                        start_delay_s=0.0,
                        fetch=True,
                        run_control=self._run_control,
                    ).run()
                except StopRequested:
                    print("[daemon] run stopped", flush=True)
        except StopRequested:
            print("[daemon] run stopped", flush=True)
        except Exception as exc:
            print(f"[daemon] run error: {exc}", flush=True)
        finally:
            self._run_control.clear()

    def _check_worker_done(self) -> None:
        if self._worker is None or self._worker.is_alive():
            return
        self._worker = None
        if self._state == "running":
            self._state = "paused"
            print("[daemon] run finished — PAUSED", flush=True)
            self._print_status()

    def _stop_worker(self, *, wait: bool) -> None:
        if self._worker is None:
            return
        self._run_control.request_stop()
        if wait and self._worker.is_alive():
            self._worker.join(timeout=600.0)
        if self._worker is not None and not self._worker.is_alive():
            self._worker = None
            self._state = "paused"

    def _print_search_mode_hint(self) -> None:
        cfg = self.config
        path = cfg.search_targets.resolve()
        cat = cfg.search_category.strip() or None
        try:
            items = load_target_list_refs(path, category=cat)
        except SystemExit as exc:
            print(f"[daemon] {exc}", flush=True)
            return
        cat_note = f" category={cat!r}" if cat else ""
        print(
            f"[daemon] M+2 priority monitor — {len(items)} items from {path}{cat_note}, "
            "then F12 (auto trusted-prices rollup after scan)",
            flush=True,
        )

    def _select_craft_mode(self) -> None:
        initial = self._craft_recipe_name or ""
        query = prompt_recipe_name(initial=initial)
        if not query:
            print("[daemon] craft selection cancelled", flush=True)
            return

        matches = find_recipes_by_query(query, recipes_dir=self.config.craft_recipes_dir)
        if not matches:
            print(f"[daemon] no recipe found for {query!r}", flush=True)
            self._print_available_recipes()
            return

        if len(matches) > 1:
            print(f"[daemon] multiple recipes match {query!r} — be more specific:", flush=True)
            for recipe in matches:
                print(f"  - {recipe.search_name} ({recipe.recipe_id})", flush=True)
            return

        recipe = matches[0]
        self._craft_recipe_id = recipe.recipe_id
        self._craft_recipe_name = recipe.search_name
        self._mode = "craft"

        materials = collect_unique_materials(recipe)
        print(f"[daemon] craft: {recipe.search_name} ({recipe.recipe_id})", flush=True)
        print(f"[daemon] {len(materials)} resources to price:", flush=True)
        for mat in materials:
            print(f"  - {mat.search_name} ({mat.item_id})", flush=True)
        self._print_status()

    def _print_available_recipes(self) -> None:
        recipes = load_all_recipes(recipes_dir=self.config.craft_recipes_dir)
        if not recipes:
            print(f"[daemon] no recipes in {self.config.craft_recipes_dir}", flush=True)
            return
        print("[daemon] available recipes:", flush=True)
        for recipe in recipes:
            print(f"  - {recipe.search_name} ({recipe.recipe_id})", flush=True)

    def _run_calibration(self, region_key: str) -> None:
        cfg = self.config
        mon = cfg.monitor
        if mon is None and cfg.roi_path.is_file():
            try:
                mon = int(load_market_roi_config(cfg.roi_path).monitor)
            except (ValueError, KeyError, OSError):
                mon = 1
        if mon is None:
            mon = 1

        label = "market window"
        print(f"[daemon] calibrate {label} — overlay in {cfg.calibrate_delay_s:.0f}s", flush=True)
        self._state = "calibrating"
        try:
            ok = run_region_calibration(
                region_key,
                monitor_index=mon,
                output_path=cfg.roi_path.resolve(),
                capture_delay_s=cfg.calibrate_delay_s,
                live_alpha=cfg.live_alpha,
            )
            if ok:
                print(
                    f"[daemon] saved market_window (+ derived search/next/back) → {cfg.roi_path}",
                    flush=True,
                )
        finally:
            self._state = "paused"
            self._print_status()

    def _print_banner(self) -> None:
        print(
            f"\n[daemon] PAUSED — Pico {self.config.pico_com}\n"
            "  Calibration (anytime):\n"
            "    C+1  market window (search / next / back derived automatically)\n"
            "    C+2..4  same as C+1\n"
            "  Mode (while paused):\n"
            "    M+1  full market bulk crawl (discovery — not user prices)\n"
            "    M+2  priority items (target_lists.yaml) — M+2 scan + catalog\n"
            "    M+3  craft cost — enter recipe name in dialog\n"
            "  After M+1/M+2: trusted rollup runs automatically; use: python -m cli prices\n"
            "  F12  start selected mode / stop run gracefully\n"
            "  Ctrl+C  quit\n",
            flush=True,
        )
        self._print_status()

    def _print_status(self, *, starting: bool = False) -> None:
        if self._mode == "craft" and self._craft_recipe_name:
            mode = f"CRAFT ({self._craft_recipe_name})"
        else:
            mode = self._mode.upper() if self._mode else "—"
        if starting:
            print(f"[daemon] RUNNING — mode {mode} (F12 to stop)", flush=True)
            return
        print(f"[daemon] PAUSED — mode {mode} (M+1/M+2/M+3 to select, F12 to start)", flush=True)


def run_daemon(config: DaemonConfig) -> None:
    MarketDaemon(config).run()

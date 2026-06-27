"""Bulk equipment scan — discovery mode, unresolved identity."""

from __future__ import annotations

import json
from pathlib import Path

from market.bulk_observations import can_aggregate_bulk_price
from market.constants import DEFAULT_PICO_COM
from market.core.models import BulkRunConfig
from market.countdown import wait_before_start
from market.bulk_crawler import crawl_market_vendors
from market.run_control import RunControl
from market.truncated_storage import (
    DEFAULT_TRUNCATED_ITEMS_PATH,
    load_truncated_store,
    prepare_bulk_row,
    save_truncated_store,
)
from market.post_run import run_post_m1_hooks


class BulkScanner:
    """Crawl full market list — open each row, collect vendor prices as unresolved observations."""

    def __init__(self, config: BulkRunConfig, *, run_control: RunControl | None = None) -> None:
        self.config = config
        self._run_control = run_control

    def run(self) -> int:
        cfg = self.config
        if not cfg.roi_path.is_file():
            raise SystemExit(
                f"Missing {cfg.roi_path}\nPress C+1 in daemon or: python -m cli calibrate"
            )
        if not cfg.dry_run and not cfg.pico_com:
            raise SystemExit(f"Pico port required (default: {DEFAULT_PICO_COM})")

        print(
            f"[bulk] category={cfg.category!r} — discovery crawl (unresolved observations)",
            flush=True,
        )
        print(
            "[bulk] open the full market list in-game (page 1 recommended) before the countdown ends",
            flush=True,
        )
        if not cfg.dry_run:
            wait_before_start(cfg.start_delay_s, tag="bulk")

        truncated_store = load_truncated_store(cfg.truncated_items_path.resolve())
        summary = truncated_store.identity_summary()
        if summary["item_keys"]:
            print(
                f"[bulk] truncated registry: {summary['item_keys']} keys "
                f"({summary['unique']} unique, {summary['ambiguous']} ambiguous)",
                flush=True,
            )

        registry_dirty = False
        ambiguous_registry_rows = 0

        def include_row(record: dict) -> bool:
            nonlocal registry_dirty, ambiguous_registry_rows
            before = len(truncated_store.items)
            include = prepare_bulk_row(
                record,
                truncated_store,
                include_all_truncated=cfg.include_truncated,
            )
            if len(truncated_store.items) > before:
                registry_dirty = True
            if not include:
                ambiguous_registry_rows += 1
            return True

        total = crawl_market_vendors(
            roi_path=cfg.roi_path.resolve(),
            pico_port=cfg.pico_com or "",
            out_jsonl=cfg.out_jsonl.resolve(),
            category=cfg.category,
            pages=cfg.pages,
            page_delay_s=cfg.page_delay_s,
            vendor_page_delay_s=cfg.vendor_page_delay_s,
            max_vendor_pages=cfg.max_vendor_pages,
            dry_run=cfg.dry_run,
            save_images=cfg.save_images,
            images_dir=cfg.images_dir.resolve() if cfg.save_images else None,
            run_control=self._run_control,
            include_row=include_row,
        )

        if registry_dirty:
            save_truncated_store(truncated_store, cfg.truncated_items_path.resolve())
            print("[bulk] updated truncated registry", flush=True)
        if ambiguous_registry_rows:
            print(
                f"[bulk] {ambiguous_registry_rows} list rows with ambiguous truncated prefix "
                f"(identity still unresolved)",
                flush=True,
            )

        self._write_summary(cfg.out_jsonl.resolve())

        if cfg.aggregate:
            print(
                "[bulk] warning: --aggregate on bulk output is deprecated; "
                "bulk observations are not trusted for min-price CSV. "
                "Use search/craft mode for trusted prices.",
                flush=True,
            )

        print(
            f"[bulk] observations: {total} rows → {cfg.out_jsonl.resolve()}",
            flush=True,
        )

        if cfg.post_run_rollup and not cfg.dry_run and total > 0:
            try:
                run_post_m1_hooks(
                    bulk_path=cfg.out_jsonl,
                    bulk_resolved_path=cfg.bulk_resolved_jsonl,
                    search_prices_path=cfg.search_prices_jsonl,
                    record_aliases=cfg.record_resolve_aliases,
                )
            except Exception as exc:
                print(f"[post-run] bulk rollup failed: {exc}", flush=True)

        return total

    @staticmethod
    def _write_summary(jsonl_path: Path) -> None:
        if not jsonl_path.is_file():
            return
        observations = 0
        vendor_rows = 0
        aggregatable = 0
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obs = json.loads(line)
            observations += 1
            vendor_rows += int(obs.get("vendor_listing_count") or 0)
            if can_aggregate_bulk_price(obs):
                aggregatable += 1
        print(
            f"[bulk] summary — {observations} observations, "
            f"{vendor_rows} vendor price rows, "
            f"{aggregatable} aggregatable (identity-confirmed)",
            flush=True,
        )

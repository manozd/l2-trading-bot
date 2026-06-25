"""Bulk equipment scan — discovery mode, unresolved identity."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from market.constants import DEFAULT_PICO_COM
from market.core.models import BulkRunConfig
from market.countdown import wait_before_start
from market.item_identity import DEFAULT_CATALOG_PATH, load_name_catalog
from market.min_prices import aggregate_min_prices, load_jsonl_rows, write_min_prices_csv, write_min_prices_json
from market.name_truncation import is_truncated_market_row
from market.run_control import RunControl
from market.scanner import scan_market_pages
from market.truncated_storage import (
    DEFAULT_TRUNCATED_ITEMS_PATH,
    load_truncated_store,
    prepare_bulk_row,
    save_truncated_store,
)


class BulkScanner:
    """Paginate market list; rows are hints only (identity_status=unresolved)."""

    def __init__(self, config: BulkRunConfig, *, run_control: RunControl | None = None) -> None:
        self.config = config
        self._run_control = run_control

    def run(self) -> int:
        cfg = self.config
        if not cfg.roi_path.is_file():
            raise SystemExit(
                f"Missing {cfg.roi_path}\nPress C+2/C+3 in daemon or: python -m cli calibrate market"
            )
        if not cfg.dry_run and not cfg.pico_com:
            raise SystemExit(f"Pico port required (default: {DEFAULT_PICO_COM})")

        print(
            f"[bulk] category={cfg.category!r} — full names + unique truncated "
            f"(ambiguous truncated → search list)",
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
        else:
            print(
                "[bulk] warning: truncated registry empty — run: python -m cli build-truncated-list",
                flush=True,
            )

        registry_dirty = False
        skipped_ambiguous = 0

        def include_row(record: dict) -> bool:
            nonlocal registry_dirty, skipped_ambiguous
            before = len(truncated_store.items)
            include = prepare_bulk_row(
                record,
                truncated_store,
                include_all_truncated=cfg.include_truncated,
            )
            if len(truncated_store.items) > before:
                registry_dirty = True
            if not include and is_truncated_market_row(record):
                skipped_ambiguous += 1
            return include

        total = scan_market_pages(
            roi_path=cfg.roi_path.resolve(),
            pico_port=cfg.pico_com or "",
            out_jsonl=cfg.out_jsonl.resolve(),
            category=cfg.category,
            pages=cfg.pages,
            page_delay_s=cfg.page_delay_s,
            dry_run=cfg.dry_run,
            save_images=cfg.save_images,
            images_dir=cfg.images_dir.resolve() if cfg.save_images else None,
            run_control=self._run_control,
            include_row=None if cfg.include_truncated else include_row,
        )

        if registry_dirty:
            save_truncated_store(truncated_store, cfg.truncated_items_path.resolve())
            print("[bulk] updated truncated registry", flush=True)
        if skipped_ambiguous:
            print(f"[bulk] skipped {skipped_ambiguous} ambiguous truncated rows", flush=True)

        self._tag_unresolved(cfg.out_jsonl.resolve())

        if not cfg.aggregate:
            return total

        rows = load_jsonl_rows(cfg.out_jsonl.resolve())
        catalog = load_name_catalog(DEFAULT_CATALOG_PATH)
        entries = aggregate_min_prices(rows, catalog=catalog)
        write_min_prices_json(cfg.min_json.resolve(), entries)
        write_min_prices_csv(cfg.min_csv.resolve(), entries)
        priced = sum(1 for r in rows if r.get("price_adena") is not None)
        print(
            f"[bulk] min prices (hints only) — {len(entries)} buckets from {priced} priced rows\n"
            f"  JSONL: {cfg.out_jsonl.resolve()}\n"
            f"  JSON:  {cfg.min_json.resolve()}\n"
            f"  CSV:   {cfg.min_csv.resolve()}",
            flush=True,
        )
        return total

    @staticmethod
    def _tag_unresolved(jsonl_path: Path) -> None:
        path = jsonl_path
        if not path.is_file():
            return
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return
        out_lines: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("identity_status") not in (
                "truncated_unique",
                "truncated_ambiguous",
            ):
                row.setdefault("identity_status", "unresolved")
            if row.get("name_source") == "list_full":
                row.setdefault("item_name_source", "ocr_list")
            else:
                row.setdefault("item_name_source", "ocr_truncated")
            page = row.get("page") or 0
            row_num = row.get("row") or 0
            icon = row.get("item_icon_hash") or ""
            raw = row.get("raw_text") or ""
            row.setdefault(
                "listing_id",
                hashlib.md5(f"{page}:{row_num}:{icon}:{raw[:80]}".encode()).hexdigest()[:16],
            )
            out_lines.append(json.dumps(row, ensure_ascii=False))
        path.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")

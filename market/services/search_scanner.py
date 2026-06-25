"""Production search-per-item scanner."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from market.capture_rois import REGION_BACK_BUTTON, REGION_SEARCH_BOX, load_market_roi_config
from market.catalog import load_item_refs
from market.constants import DEFAULT_PICO_COM
from market.core.confidence import score_search_row
from market.core.models import ItemRef, SearchResult, SearchRunConfig
from market.countdown import wait_before_start
from market.pico_hid import PicoHidSerial
from market.scanner import collect_search_first_row
from market.search import press_back_button, submit_search_query
from market.storage.progress import ProgressStore
from market.storage.search_sink import SearchResultSink
from market.run_control import RunControl

PROGRESS_NAME = "market_search_progress.json"


class SearchScanner:
    def __init__(
        self,
        config: SearchRunConfig,
        *,
        target_lists: Path | None = None,
        run_control: RunControl | None = None,
    ) -> None:
        self.config = config
        self._target_lists = target_lists
        self._run_control = run_control
        self._roi = load_market_roi_config(config.roi_path.resolve())
        self._validate_rois()
        self._search = self._roi.require(REGION_SEARCH_BOX)
        self._back = self._roi.require(REGION_BACK_BUTTON)
        progress_path = config.out_jsonl.parent / PROGRESS_NAME
        self._progress = ProgressStore(progress_path)
        self._sink = SearchResultSink(
            jsonl_path=config.out_jsonl.resolve(),
            validate_csv=config.validate_csv.resolve(),
            validate_log=config.validate_log.resolve(),
            min_json=config.min_json.resolve(),
            min_csv=config.min_csv.resolve(),
        )

    def _validate_rois(self) -> None:
        if REGION_MARKET_WINDOW not in self._roi.regions:
            raise SystemExit(
                f"Missing market_window in {self.config.roi_path}\n"
                "Press C+1 in daemon or run: python -m cli calibrate"
            )

    def load_items(self) -> list[ItemRef]:
        items = load_item_refs(
            items_db=self.config.items_db.resolve(),
            target_lists=self._target_lists,
            category=self.config.category if self._target_lists else None,
        )
        if self.config.name_filter:
            fl = self.config.name_filter.casefold()
            items = [
                i
                for i in items
                if fl in i.search_name.casefold() or fl in i.display_name.casefold()
            ]
        if self.config.start:
            items = items[self.config.start :]
        if self.config.limit:
            items = items[: self.config.limit]
        return items

    def run(self) -> list[SearchResult]:
        cfg = self.config
        if not cfg.dry_run and not cfg.pico_com:
            raise SystemExit(f"Pico port required (default: {DEFAULT_PICO_COM})")

        items = self.load_items()
        done = self._progress.load_done() if cfg.resume else set()

        if not cfg.resume:
            self._sink.reset()

        if not cfg.dry_run:
            wait_before_start(cfg.start_delay_s, tag="search")

        pico: PicoHidSerial | None = None
        if not cfg.dry_run:
            pico = PicoHidSerial(cfg.pico_com)
            print(f"[search] LIVE Pico={cfg.pico_com} items={len(items)}", flush=True)
        else:
            print(f"[search] dry-run items={len(items)}", flush=True)

        scanned_at = datetime.now(timezone.utc).isoformat()

        try:
            for i, item in enumerate(items, start=1):
                if self._run_control and self._run_control.should_stop():
                    print("[search] stopped — finishing after last item", flush=True)
                    break

                if self._is_done(item, done):
                    print(
                        f"[search] skip ({i}/{len(items)}) {item.display_name!r} — resume",
                        flush=True,
                    )
                    continue

                print(f"[search] ({i}/{len(items)}) {item.display_name!r}", flush=True)

                if not cfg.dry_run:
                    assert pico is not None
                    submit_search_query(
                        item.search_name,
                        search=self._search,
                        pico=pico,
                        settle_s=cfg.search_settle_s,
                        input_mode=cfg.input_mode,
                    )

                raw_rows = collect_search_first_row(
                    roi_path=cfg.roi_path.resolve(),
                    category=cfg.category,
                    scanned_at=scanned_at,
                )
                raw = raw_rows[0] if raw_rows else None
                if raw:
                    raw = dict(raw)
                    raw["item_full_name"] = item.display_name
                    raw["name_source"] = "db_search_query"
                    raw["search_query"] = item.search_name
                    raw["item_id"] = item.item_id

                row_conf, price_conf = score_search_row(
                    raw,
                    db_name=item.search_name,
                    expected_enchant=item.enchant,
                )
                result = SearchResult.from_db_row(
                    item,
                    raw,
                    scanned_at=scanned_at,
                    category=cfg.category,
                    row_confidence=row_conf,
                    price_confidence=price_conf,
                )

                self._sink.append(result)
                self._sink.log_collected(result)
                self._progress.mark_done(item.item_id, done)

                if self._run_control and self._run_control.should_stop():
                    print("[search] stopped — PAUSED", flush=True)
                    break

                if not cfg.dry_run and i < len(items):
                    assert pico is not None
                    press_back_button(back=self._back, pico=pico, settle_s=cfg.back_settle_s)
                    time.sleep(0.1)
        finally:
            if pico is not None:
                pico.close()

        self._sink.finalize()
        self._sink.print_done()
        return self._sink.results

    @staticmethod
    def _is_done(item: ItemRef, done: set[str]) -> bool:
        """Resume by item_id; also accept legacy search_name keys."""
        kid = item.item_id.casefold()
        base = item.search_name.casefold()
        return kid in done or base in done

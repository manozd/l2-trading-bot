"""Production search-per-item scanner — M+2 catalog + matched-row price monitor."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from market.capture_rois import REGION_BACK_BUTTON, REGION_MARKET_WINDOW, REGION_SEARCH_BOX, load_market_roi_config
from market.catalog import load_target_list_refs
from market.constants import DEFAULT_PICO_COM
from market.core.models import ItemRef, SearchResult, SearchRunConfig
from market.countdown import wait_before_start
from market.pico_hid import PicoHidSerial
from market.search import press_back_button, submit_search_query
from market.search_recovery import clear_search_filter, detect_search_list_state
from market.search_progress import M2_MODE_VERSION, SearchProgressStore, target_config_hash
from market.services.priority_scan import (
    catalog_scan_phase,
    collect_search_rows_with_retry,
    fallback_search_queries,
    pick_matched_search_row,
    priority_price_snapshot,
)
from market.storage.search_sink import SearchResultSink
from market.run_control import RunControl, StopRequested, check_stop, sleep_checked
from market.post_run import run_post_m1_hooks, run_post_m2_hooks
from market.variant_catalog import VariantCatalog

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
        self._target_lists = (target_lists or config.target_lists).resolve()
        self._run_control = run_control
        self._roi = load_market_roi_config(config.roi_path.resolve())
        self._validate_rois()
        self._search = self._roi.require(REGION_SEARCH_BOX)
        self._back = self._roi.require(REGION_BACK_BUTTON)
        self._category_filter = self._yaml_category_filter()
        self._config_hash = target_config_hash(
            self._target_lists,
            category_filter=self._category_filter,
        )
        progress_path = config.out_jsonl.parent / PROGRESS_NAME
        self._progress = SearchProgressStore(
            progress_path,
            mode_version=M2_MODE_VERSION,
            config_hash=self._config_hash,
        )
        self._sink = SearchResultSink(
            jsonl_path=config.out_jsonl.resolve(),
            validate_csv=config.validate_csv.resolve(),
            validate_log=config.validate_log.resolve(),
            min_json=config.min_json.resolve(),
            min_csv=config.min_csv.resolve(),
        )
        self._catalog = VariantCatalog.load(config.variant_catalog_path.resolve())
        self._catalog_dirty = False

    def _validate_rois(self) -> None:
        if REGION_MARKET_WINDOW not in self._roi.regions:
            raise SystemExit(
                f"Missing market_window in {self.config.roi_path}\n"
                "Press C+1 in daemon or run: python -m cli calibrate"
            )

    def load_items(self) -> list[ItemRef]:
        items = load_target_list_refs(self._target_lists, category=self._category_filter)
        cfg = self.config
        if cfg.name_filter:
            fl = cfg.name_filter.casefold()
            items = [
                i
                for i in items
                if fl in i.search_name.casefold() or fl in i.display_name.casefold()
            ]
        if cfg.start:
            items = items[cfg.start :]
        if cfg.limit:
            items = items[: cfg.limit]
        return items

    @staticmethod
    def _yaml_category_filter_for(config: SearchRunConfig) -> str | None:
        cat = (config.category or "").strip()
        if not cat or cat == "search":
            return None
        return cat

    def _yaml_category_filter(self) -> str | None:
        return self._yaml_category_filter_for(self.config)

    def run(self) -> list[SearchResult]:
        cfg = self.config
        if not cfg.dry_run and not cfg.pico_com:
            raise SystemExit(f"Pico port required (default: {DEFAULT_PICO_COM})")

        items = self.load_items()
        done = self._progress.load_done_item_ids() if cfg.resume else set()
        pending = [i for i in items if not self._is_done(i, done)]
        progress_path = cfg.out_jsonl.parent / PROGRESS_NAME

        if not cfg.resume:
            self._progress.clear()
            self._sink.reset()
            done = set()
            pending = items
        elif self._progress.is_legacy_stale():
            print(
                "[search] resume — ignoring stale progress "
                f"(mode {M2_MODE_VERSION}, config {self._config_hash}). "
                f"Delete {progress_path.resolve()} to clear the old file.",
                flush=True,
            )
            self._progress.clear()
            done = set()
            pending = items
        elif done and not pending:
            print(
                f"[search] previous run complete ({len(done)} item(s)) — refreshing all items. "
                "Resume still applies when a run stops early (F12 mid-scan).",
                flush=True,
            )
            self._progress.clear()
            done = set()
            pending = items
        elif done:
            print(
                f"[search] resume ON — {len(done)} checkpoint(s), "
                f"{len(pending)}/{len(items)} item(s) left "
                f"(mode {M2_MODE_VERSION}, config {self._config_hash})",
                flush=True,
            )

        if not cfg.dry_run:
            wait_before_start(cfg.start_delay_s, tag="search", run_control=self._run_control)

        pico: PicoHidSerial | None = None
        if not cfg.dry_run:
            pico = PicoHidSerial(cfg.pico_com)
            print(f"[search] LIVE Pico={cfg.pico_com} items={len(items)}", flush=True)
        else:
            print(f"[search] dry-run items={len(items)}", flush=True)

        print(
            f"[search] target list: {self._target_lists} ({len(items)} items)",
            flush=True,
        )
        print(
            f"[search] variant catalog: {cfg.variant_catalog_path.resolve()} "
            f"({len(self._catalog.entries)} entries loaded)",
            flush=True,
        )
        print(
            "[search] mode: catalog + matched-row min price (no vendor pages)",
            flush=True,
        )

        scanned_at = datetime.now(timezone.utc).isoformat()

        try:
            for i, item in enumerate(items, start=1):
                check_stop(self._run_control)

                if self._is_done(item, done):
                    print(
                        f"[search] skip ({i}/{len(items)}) {item.display_name!r} — resume "
                        f"(see {cfg.out_jsonl.parent / PROGRESS_NAME})",
                        flush=True,
                    )
                    continue

                print(f"[search] ({i}/{len(items)}) {item.display_name!r}", flush=True)

                queries = fallback_search_queries(item.search_name)
                raw_rows: list[dict] = []
                used_query = item.search_name

                if not cfg.dry_run:
                    assert pico is not None
                    for qi, query in enumerate(queries):
                        check_stop(self._run_control)
                        if qi > 0:
                            print(
                                f"[search] fallback search {query!r} "
                                f"(no rows for {item.search_name!r})",
                                flush=True,
                            )
                        submit_search_query(
                            query,
                            search=self._search,
                            pico=pico,
                            settle_s=cfg.search_settle_s,
                            input_mode=cfg.input_mode,
                            fast=True,
                            run_control=self._run_control,
                        )
                        raw_rows = collect_search_rows_with_retry(
                            roi_path=cfg.roi_path.resolve(),
                            category=item.category or cfg.category,
                            scanned_at=scanned_at,
                            run_control=self._run_control,
                        )
                        if raw_rows:
                            used_query = query
                            break
                        if qi == 0 and not raw_rows:
                            list_state = (
                                detect_search_list_state(roi_path=cfg.roi_path.resolve())
                                if not cfg.dry_run
                                else "empty_list"
                            )
                            if list_state == "empty_list" or item.search_name.casefold().startswith(
                                "recipe:"
                            ):
                                print(
                                    f"[search] sold out — exact search returned no listings "
                                    f"for {item.search_name!r} (skipping broader fallbacks)",
                                    flush=True,
                                )
                                break

                not_on_market = not raw_rows
                if not_on_market:
                    print(
                        f"[search] not on market — no listings for {item.search_name!r}",
                        flush=True,
                    )

                print(f"[search] catalog_scan — {len(raw_rows)} visible variant row(s)", flush=True)
                catalog_uids = catalog_scan_phase(
                    self._catalog,
                    raw_rows=raw_rows,
                    search_query=item.search_name,
                    display_name=item.display_name,
                    item_id=item.item_id,
                    category=item.category or cfg.category,
                    scanned_at=scanned_at,
                )
                if catalog_uids:
                    self._catalog_dirty = True
                    print(
                        f"[search] catalog +{len(catalog_uids)} variant(s): {', '.join(catalog_uids)}",
                        flush=True,
                    )

                matched_row = pick_matched_search_row(
                    raw_rows,
                    search_name=item.search_name,
                    search_query=used_query,
                )
                if matched_row:
                    label = matched_row.get("item") or item.search_name
                    price = matched_row.get("price_adena")
                    if price is not None:
                        print(
                            f"[search] priority_price — matched row {matched_row.get('row')}: "
                            f"{label!r} @ {int(price):,} adena",
                            flush=True,
                        )
                    else:
                        print(
                            f"[search] priority_price — matched row {matched_row.get('row')}: "
                            f"{label!r} (no price on row)",
                            flush=True,
                        )
                else:
                    print(
                        f"[search] priority_price — no confident row match for {item.search_name!r}",
                        flush=True,
                    )

                result = priority_price_snapshot(
                    item,
                    matched_row,
                    category=item.category or cfg.category,
                    scanned_at=scanned_at,
                    catalog=self._catalog,
                )

                self._sink.append(result)
                self._sink.log_collected(result)
                self._progress.mark_done(
                    item_id=item.item_id,
                    search_query=item.search_name,
                    done=done,
                )

                if not cfg.dry_run and i < len(items):
                    check_stop(self._run_control)
                    assert pico is not None
                    if not_on_market:
                        clear_search_filter(
                            search=self._search,
                            pico=pico,
                            run_control=self._run_control,
                        )
                    else:
                        press_back_button(
                            back=self._back,
                            pico=pico,
                            settle_s=cfg.back_settle_s,
                            fast=True,
                            run_control=self._run_control,
                        )
        except StopRequested:
            print("[search] stopped — PAUSED", flush=True)
        finally:
            if pico is not None:
                pico.close()
            if self._catalog_dirty:
                self._catalog.save()
                stats = self._catalog.stats()
                print(
                    f"[search] saved variant catalog — {stats['entries']} entries, "
                    f"{stats['groups_with_multiple_uids']} multi-variant groups → "
                    f"{cfg.variant_catalog_path.resolve()}",
                    flush=True,
                )
            if cfg.resume and all(self._is_done(item, done) for item in items):
                self._progress.clear()

        self._sink.finalize()
        self._sink.print_done()
        if cfg.post_run_rollup and not cfg.dry_run:
            try:
                run_post_m2_hooks(
                    search_prices_path=cfg.out_jsonl,
                    bulk_resolved_path=cfg.bulk_resolved_jsonl,
                    catalog_path=cfg.variant_catalog_path,
                )
            except Exception as exc:
                print(f"[post-run] trusted rollup failed: {exc}", flush=True)
        return self._sink.results

    @staticmethod
    def _is_done(item: ItemRef, done: set[str]) -> bool:
        return item.item_id.casefold() in done

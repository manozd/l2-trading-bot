"""Crawl full market list: open each item's vendors and collect all listings."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from market.bulk_observations import build_bulk_observation, make_scan_run_id
from market.capture import grab_screen_rect
from market.capture_rois import REGION_BACK_BUTTON, REGION_MARKET_WINDOW, REGION_NEXT_PAGE, load_market_roi_config
from market.constants import DEFAULT_PICO_COM
from market.craft.vendor_search import ocr_vendor_listings_once
from market.full_list_parser import (
    ROWS_PER_PAGE,
    MarketRow,
    icon_hash_for_list_row,
    parse_page_rows,
    rows_with_item_icons,
)
from market.input_ctl import smooth_move_to
from market.ocr_engine import get_ocr_engine
from market.page_fingerprint import (
    PageFingerprint,
    fingerprint_list_body,
    list_icon_fingerprint_body,
    page_unchanged,
)
from market.pagination import ListPageTracker, is_reliable_last_page, read_page_indicator_robust
from market.pico_hid import PicoHidSerial
from market.run_control import RunControl, StopRequested, check_stop, sleep_checked
from market.search import park_cursor_for_ocr, park_cursor_on_back, press_back_button
from market.ui_layout import search_results_row_click_xy

LIST_ROW_SETTLE_S = 0.3
VENDOR_OPEN_SETTLE_S = 0.25
BACK_TO_LIST_SETTLE_S = 0.35
EMPTY_PAGE_STOP = 2


def _format_rows_label(rows: list[int]) -> str:
    if not rows:
        return "none"
    return ",".join(str(r) for r in rows)


def _should_stop_bulk_list(
    *,
    empty_pages: int,
    rows_this_page: int,
) -> tuple[bool, str | None]:
    """Stop only on repeated empty pages — last page is detected after Next via fingerprint."""
    if rows_this_page > 0 and empty_pages >= EMPTY_PAGE_STOP:
        return True, f"{empty_pages} consecutive pages without prices"
    return False, None


def _click_list_row(
    *,
    market,
    row: int,
    pico: PicoHidSerial,
    run_control: RunControl | None = None,
) -> None:
    cx, cy = search_results_row_click_xy(market, row=row)
    check_stop(run_control)
    smooth_move_to(cx, cy, duration_s=0.1, steps=6, sync=True)
    sleep_checked(0.03, run_control=run_control)
    pico.click_left_prepare(hold_ms=100, ping=True)
    sleep_checked(LIST_ROW_SETTLE_S, run_control=run_control)


def _capture_list_fingerprint(*, market, back, next_btn) -> PageFingerprint:
    """Park cursor on Next (below list) and hash only the list body above pagination."""
    park_cursor_for_ocr(back=back, next_btn=next_btn, on_next=True, settle_s=0.08)
    time.sleep(0.05)
    frame = grab_screen_rect(market.left, market.top, market.width, market.height)
    return fingerprint_list_body(frame.bgr)


def _next_list_page(
    *,
    market,
    back,
    next_btn,
    pico: PicoHidSerial,
    before_fp: PageFingerprint,
    page_delay_s: float,
    run_control: RunControl | None = None,
) -> tuple[bool, PageFingerprint | None]:
    """
    Click list Next and verify the page advanced.

    Cursor stays on Next; fingerprints compare list body only (above pagination).
    Returns ``(advanced, fingerprint_after_click)``.
    """
    _click_list_next_page(
        next_btn=next_btn,
        pico=pico,
        page_delay_s=page_delay_s,
        run_control=run_control,
    )
    after_fp = _capture_list_fingerprint(market=market, back=back, next_btn=next_btn)
    if page_unchanged(before_fp, after_fp):
        return False, after_fp
    return True, after_fp


def _click_list_next_page(
    *,
    next_btn,
    pico: PicoHidSerial,
    page_delay_s: float,
    run_control: RunControl | None = None,
) -> None:
    """Click Next — cursor should already be parked there from the pre-click fingerprint."""
    cx, cy = next_btn.center_screen()
    smooth_move_to(cx, cy, duration_s=0.06, steps=3, sync=False)
    time.sleep(0.03)
    pico.click_left_prepare(hold_ms=100, ping=True)
    sleep_checked(page_delay_s, run_control=run_control)


def _rows_by_number(rows: list[MarketRow]) -> dict[int, MarketRow]:
    out: dict[int, MarketRow] = {}
    for row in rows:
        if 1 <= row.row <= ROWS_PER_PAGE:
            out[row.row] = row
    return out


def _list_row_metadata(
    *,
    row_num: int,
    list_page: int,
    category: str,
    scanned_at: str,
    list_bgr,
    ocr_row: MarketRow | None,
) -> dict:
    icon_hash = (
        ocr_row.item_icon_hash
        if ocr_row and ocr_row.item_icon_hash
        else icon_hash_for_list_row(list_bgr, row_num)
    )
    record: dict = {
        "page": list_page,
        "row": row_num,
        "category": category,
        "scanned_at": scanned_at,
        "list_page": list_page,
        "list_row": row_num,
        "item_icon_hash": icon_hash,
    }
    if ocr_row is not None:
        record.update(
            {
                "item": ocr_row.item,
                "vendor": ocr_row.vendor,
                "price_adena": ocr_row.price_adena,
                "units": ocr_row.units,
                "raw_text": ocr_row.raw_text,
                "item_key": ocr_row.item_key,
                "item_slug": ocr_row.item_slug,
            }
        )
    return record


def crawl_market_vendors(
    *,
    roi_path: Path,
    pico_port: str = DEFAULT_PICO_COM,
    out_jsonl: Path,
    category: str,
    pages: int = 200,
    page_delay_s: float = 0.45,
    vendor_page_delay_s: float = 0.2,
    max_vendor_pages: int = 1,
    dry_run: bool = False,
    save_images: bool = False,
    images_dir: Path | None = None,
    run_control: RunControl | None = None,
    include_row: Callable[[dict], bool] | None = None,
) -> int:
    """
    Crawl the full-item market list page-by-page.

    Navigation: fixed row clicks 1–7, counter-based page tracking.
    Output: one bulk_vendor_scan observation per opened list row.
    """
    del vendor_page_delay_s
    del max_vendor_pages

    cfg = load_market_roi_config(roi_path)
    market = cfg.require(REGION_MARKET_WINDOW)
    next_btn = cfg.require(REGION_NEXT_PAGE)
    back = cfg.require(REGION_BACK_BUTTON)

    if save_images and images_dir is not None:
        images_dir.mkdir(parents=True, exist_ok=True)

    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    if out_jsonl.exists():
        out_jsonl.unlink()
    out_jsonl.write_text("", encoding="utf-8")

    ocr = get_ocr_engine()
    pico: PicoHidSerial | None = None
    if not dry_run:
        pico = PicoHidSerial(pico_port)
        print(
            f"[bulk-crawl] LIVE — category={category!r} Pico={pico_port} "
            f"(list pages ≤{pages}, {ROWS_PER_PAGE} rows/page, vendor page 1 only)",
            flush=True,
        )
    else:
        print(f"[bulk-crawl] dry-run — category={category!r}, no clicks", flush=True)

    empty_pages = 0
    total_vendor_listings = 0
    observations_written = 0
    page_tracker = ListPageTracker()
    scanned_at = datetime.now(timezone.utc).isoformat()
    scan_run_id = make_scan_run_id(scanned_at, category)
    loop_i = 0
    prev_iter_start_icons: tuple[str, ...] | None = None

    print(f"[bulk-crawl] scan_run_id={scan_run_id}", flush=True)

    try:
        while loop_i < pages:
            if run_control and run_control.should_stop():
                print("[bulk-crawl] stopped — PAUSED", flush=True)
                break

            loop_i += 1
            park_cursor_on_back(back)
            frame = grab_screen_rect(market.left, market.top, market.width, market.height)
            start_icons = list_icon_fingerprint_body(frame.bgr)
            if prev_iter_start_icons is not None and start_icons == prev_iter_start_icons:
                print(
                    "[bulk-crawl] stopping list pagination — "
                    "same page icons as previous iteration (Next did not advance)",
                    flush=True,
                )
                break
            prev_iter_start_icons = start_icons

            indicator = read_page_indicator_robust(frame.bgr, ocr)
            list_page = page_tracker.resolve(indicator, loop_i=loop_i)
            list_page_total_hint = page_tracker.total_hint

            if save_images and images_dir is not None:
                frame.save_png(str(images_dir / f"{category}_list_{list_page:03d}.png"))

            rows = parse_page_rows(frame.bgr, page=list_page, ocr=ocr)
            rows_by_num = _rows_by_number(rows)
            rows_to_open = rows_with_item_icons(frame.bgr)
            priced = sum(1 for r in rows if r.price_adena is not None)
            if len(rows) > 0 and priced == 0:
                empty_pages += 1
            elif priced > 0:
                empty_pages = 0

            print(
                f"[bulk-crawl] list page — {page_tracker.ocr_log_suffix(indicator)} "
                f"— open rows {_format_rows_label(rows_to_open)} "
                f"({len(rows_to_open)}/{ROWS_PER_PAGE} icons)",
                flush=True,
            )

            for row_num in rows_to_open:
                if run_control and run_control.should_stop():
                    print("[bulk-crawl] stopped — PAUSED", flush=True)
                    break

                ocr_row = rows_by_num.get(row_num)
                list_record = _list_row_metadata(
                    row_num=row_num,
                    list_page=list_page,
                    category=category,
                    scanned_at=scanned_at,
                    list_bgr=frame.bgr,
                    ocr_row=ocr_row,
                )
                if include_row is not None:
                    include_row(list_record)

                label = (
                    (ocr_row.item if ocr_row else None)
                    or (ocr_row.raw_text[:60] if ocr_row and ocr_row.raw_text else None)
                    or f"row {row_num}"
                )
                if dry_run:
                    print(
                        f"[bulk-crawl] dry-run list {list_page} row {row_num}: {label!r}",
                        flush=True,
                    )
                    continue

                assert pico is not None
                print(
                    f"[bulk-crawl] open list {list_page} row {row_num}: {label!r}",
                    flush=True,
                )
                try:
                    _click_list_row(
                        market=market,
                        row=row_num,
                        pico=pico,
                        run_control=run_control,
                    )
                    sleep_checked(VENDOR_OPEN_SETTLE_S, run_control=run_control)

                    vendor_listings = ocr_vendor_listings_once(
                        roi_path=roi_path,
                        run_control=run_control,
                    )
                    observation = build_bulk_observation(
                        scan_run_id=scan_run_id,
                        category=category,
                        list_page=list_page,
                        list_row=row_num,
                        list_icon_hash=list_record["item_icon_hash"],
                        ocr_row=ocr_row,
                        vendor_listings=vendor_listings,
                        list_page_total_hint=list_page_total_hint,
                    )
                    with out_jsonl.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(observation, ensure_ascii=False) + "\n")
                    observations_written += 1
                    total_vendor_listings += observation["vendor_listing_count"]

                    print(
                        f"[bulk-crawl]   → {observation['vendor_listing_count']} vendor listings",
                        flush=True,
                    )

                    press_back_button(
                        back=back,
                        pico=pico,
                        settle_s=BACK_TO_LIST_SETTLE_S,
                        fast=True,
                        run_control=run_control,
                    )
                except StopRequested:
                    raise
                except Exception as exc:
                    print(
                        f"[bulk-crawl] list {list_page} row {row_num} {label!r} "
                        f"failed: {exc} — back + continue",
                        flush=True,
                    )
                    try:
                        press_back_button(
                            back=back,
                            pico=pico,
                            settle_s=BACK_TO_LIST_SETTLE_S,
                            fast=True,
                            run_control=run_control,
                        )
                    except Exception:
                        pass
                    continue

            if run_control and run_control.should_stop():
                break

            stop, reason = _should_stop_bulk_list(
                empty_pages=empty_pages,
                rows_this_page=len(rows),
            )
            if stop:
                print(f"[bulk-crawl] stopping list pagination — {reason}", flush=True)
                break

            if is_reliable_last_page(indicator) or (
                list_page_total_hint is not None
                and list_page_total_hint >= 5
                and list_page >= list_page_total_hint
            ):
                print(
                    f"[bulk-crawl] stopping list pagination — "
                    f"last page ({list_page}/{list_page_total_hint or '?'})",
                    flush=True,
                )
                break

            if dry_run:
                page_tracker.after_next_click()
                sleep_checked(page_delay_s, run_control=run_control)
                continue

            assert pico is not None
            before_next_fp = _capture_list_fingerprint(market=market, back=back, next_btn=next_btn)
            advanced, _after_next_fp = _next_list_page(
                market=market,
                back=back,
                next_btn=next_btn,
                pico=pico,
                before_fp=before_next_fp,
                page_delay_s=page_delay_s,
                run_control=run_control,
            )
            if not advanced:
                print(
                    "[bulk-crawl] stopping list pagination — "
                    "Next did not change page (icon fingerprint unchanged)",
                    flush=True,
                )
                break
            page_tracker.after_next_click()
    finally:
        if pico is not None:
            pico.close()

    print(
        f"[bulk-crawl] done — {observations_written} observations, "
        f"{total_vendor_listings} vendor listings → {out_jsonl}",
        flush=True,
    )
    return observations_written

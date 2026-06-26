"""Live market list scan: capture pages, OCR rows, optional Pico pagination."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from typing import Callable

from market.capture import grab_screen_rect
from market.capture_rois import REGION_BACK_BUTTON, REGION_MARKET_WINDOW, REGION_NEXT_PAGE, load_market_roi_config
from market.constants import DEFAULT_PICO_COM
from market.full_list_parser import parse_page_rows
from market.input_ctl import smooth_move_to
from market.ocr_engine import get_ocr_engine
from market.page_fingerprint import PageFingerprint, fingerprint_page, page_unchanged
from market.pagination import PageIndicator, read_page_indicator
from market.pico_hid import PicoHidSerial
from market.run_control import RunControl
from market.search import park_cursor_on_back

EMPTY_PAGE_STOP = 2


def _indicator_is_reliable_last(
    indicator: PageIndicator | None,
    *,
    last_reliable_page: int | None = None,
) -> bool:
    """True when pagination OCR convincingly shows the final list page."""
    if indicator is None or not indicator.is_last:
        return False
    # ``999/999`` is a frequent OCR false positive on this UI (not the real page count).
    if indicator.total >= 500:
        return False
    if last_reliable_page is not None and indicator.current > last_reliable_page + 5:
        return False
    return True


def _should_stop_pagination(
    *,
    loop_i: int,
    prev_fp: PageFingerprint | None,
    cur_fp: PageFingerprint,
    indicator,
    empty_pages: int,
    rows_this_page: int,
    last_reliable_page: int | None = None,
) -> tuple[bool, str | None]:
    if loop_i <= 1:
        return False, None
    if page_unchanged(prev_fp, cur_fp):
        return True, "duplicate page fingerprint"
    if _indicator_is_reliable_last(indicator, last_reliable_page=last_reliable_page):
        return True, f"pagination indicator ({indicator.current}/{indicator.total})"
    # Only treat as empty market when rows were parsed but none had a price.
    if rows_this_page > 0 and empty_pages >= EMPTY_PAGE_STOP:
        return True, f"{empty_pages} consecutive pages without prices"
    return False, None


def scan_market_pages(
    *,
    roi_path: Path,
    pico_port: str = DEFAULT_PICO_COM,
    out_jsonl: Path,
    category: str,
    pages: int = 200,
    page_delay_s: float = 0.45,
    dry_run: bool = False,
    save_images: bool = False,
    images_dir: Path | None = None,
    start_page: int = 1,
    run_control: RunControl | None = None,
    include_row: Callable[[dict], bool] | None = None,
) -> int:
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
        print(f"[scan] LIVE — category={category!r} Pico={pico_port}", flush=True)
    else:
        print(f"[scan] dry-run — category={category!r}, no clicks", flush=True)

    prev_fp: PageFingerprint | None = None
    empty_pages = 0
    total_rows = 0
    skipped_truncated = 0
    scanned_at = datetime.now(timezone.utc).isoformat()
    loop_i = 0

    try:
        while loop_i < pages:
            if run_control and run_control.should_stop():
                print("[scan] stopped — PAUSED", flush=True)
                break

            loop_i += 1
            park_cursor_on_back(back)
            frame = grab_screen_rect(market.left, market.top, market.width, market.height)
            cur_fp = fingerprint_page(frame.bgr)

            indicator = read_page_indicator(frame.bgr, ocr)
            page_num = indicator.current if indicator else start_page + loop_i - 1

            if save_images and images_dir is not None:
                frame.save_png(str(images_dir / f"{category}_page_{page_num:03d}.png"))

            rows = parse_page_rows(frame.bgr, page=page_num, ocr=ocr)
            priced = sum(1 for r in rows if r.price_adena is not None)
            if len(rows) > 0 and priced == 0:
                empty_pages += 1
            elif priced > 0:
                empty_pages = 0

            for row in rows:
                record = row.to_dict()
                record["category"] = category
                record["scanned_at"] = scanned_at
                if indicator:
                    record["page_total"] = indicator.total
                if include_row is not None and not include_row(record):
                    skipped_truncated += 1
                    continue
                with out_jsonl.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                total_rows += 1

            stop, reason = _should_stop_pagination(
                loop_i=loop_i,
                prev_fp=prev_fp,
                cur_fp=cur_fp,
                indicator=indicator,
                empty_pages=empty_pages,
                rows_this_page=len(rows),
            )

            if indicator:
                print(
                    f"[scan] page {indicator.current}/{indicator.total} → "
                    f"{len(rows)} rows ({priced} priced)",
                    flush=True,
                )
            else:
                print(
                    f"[scan] page {page_num} → {len(rows)} rows ({priced} priced) "
                    f"(pagination OCR failed)",
                    flush=True,
                )

            if len(rows) == 0:
                print("[scan] warning: 0 rows — check market_window ROI (C+2) or game focus", flush=True)

            if stop:
                print(f"[scan] stopping — {reason}", flush=True)
                break

            prev_fp = cur_fp

            if run_control and run_control.should_stop():
                print("[scan] stopped — PAUSED", flush=True)
                break

            if dry_run:
                time.sleep(page_delay_s)
                continue

            cx, cy = next_btn.center_screen()
            smooth_move_to(cx, cy, sync=True)
            time.sleep(0.06)
            assert pico is not None
            pico.click_left_prepare(hold_ms=120, ping=True)
            time.sleep(page_delay_s)
    finally:
        if pico is not None:
            pico.close()

    if skipped_truncated:
        print(f"[scan] skipped {skipped_truncated} truncated-name rows", flush=True)
    print(f"[scan] done — {total_rows} rows → {out_jsonl}", flush=True)
    return total_rows


def collect_search_page_rows(
    *,
    roi_path: Path,
    category: str,
    scanned_at: str | None = None,
) -> list[dict]:
    """After search+Enter: OCR all listing rows on page 1 (variants + row-1 price)."""
    cfg = load_market_roi_config(roi_path)
    market = cfg.require(REGION_MARKET_WINDOW)
    back = cfg.require(REGION_BACK_BUTTON)

    ocr = get_ocr_engine()
    if scanned_at is None:
        scanned_at = datetime.now(timezone.utc).isoformat()

    park_cursor_on_back(back)
    frame = grab_screen_rect(market.left, market.top, market.width, market.height)
    rows = parse_page_rows(frame.bgr, page=1, ocr=ocr)

    if not rows:
        print("[search] page 1 — no rows", flush=True)
        return []

    records: list[dict] = []
    for row in rows:
        if not row.item_icon_hash:
            continue
        label = row.item or row.raw_text[:60]
        print(f"[search] row {row.row}: {label!r} icon={row.item_icon_hash[:20]}...", flush=True)
        record = row.to_dict()
        record["category"] = category
        record["scanned_at"] = scanned_at
        records.append(record)

    if not records:
        print("[search] page 1 — rows present but none with item icons", flush=True)
    return records


def collect_search_first_row(
    *,
    roi_path: Path,
    category: str,
    scanned_at: str | None = None,
) -> list[dict]:
    """After search+Enter: OCR page 1 and keep only row 1 (legacy price pick)."""
    rows = collect_search_page_rows(
        roi_path=roi_path,
        category=category,
        scanned_at=scanned_at,
    )
    if not rows:
        return []

    first = next((r for r in rows if r.get("row") == 1), rows[0])
    label = first.get("item") or (first.get("raw_text") or "")[:60]
    print(f"[search] first row only — row {first.get('row')}: {label!r}", flush=True)
    first = dict(first)
    first["search_first_row_only"] = True
    return [first]

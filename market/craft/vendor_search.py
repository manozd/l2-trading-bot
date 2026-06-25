"""Search → pick result row → collect vendor listings with prices."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from market.capture import grab_screen_rect
from market.capture_rois import (
    REGION_BACK_BUTTON,
    REGION_MARKET_WINDOW,
    REGION_NEXT_PAGE,
    REGION_SEARCH_BOX,
    RoiRect,
    load_market_roi_config,
)
from market.craft.match import filter_search_result_rows, format_result_rows, pick_result_row
from market.craft.models import MaterialPrice
from market.full_list_parser import parse_page_rows, parse_search_result_rows, row_click_screen_xy
from market.input_ctl import smooth_move_to
from market.ocr_engine import get_ocr_engine
from market.page_fingerprint import PageFingerprint, fingerprint_page, page_unchanged
from market.pagination import read_page_indicator
from market.pico_hid import PicoHidSerial
from market.search import park_cursor_for_ocr, press_back_button, submit_search_query

MAX_VENDOR_PAGES = 15
MAX_SEARCH_RESULT_PAGES = 3

# Craft scan timings — search/back must stay reliable; pagination can be a bit faster.
CRAFT_SEARCH_SETTLE_S = 0.45
CRAFT_BACK_SETTLE_S = 0.45
CRAFT_PAGE_DELAY_S = 0.3
CRAFT_ROW_SETTLE_S = 0.45
CRAFT_VENDOR_SETTLE_S = 0.3
CRAFT_POST_SEARCH_WAIT_S = 0.25
CRAFT_OCR_RETRY_WAIT_S = 0.35


def _click_row(
    row_number: int,
    *,
    market: RoiRect,
    pico: PicoHidSerial,
    settle_s: float = CRAFT_ROW_SETTLE_S,
) -> None:
    frame = grab_screen_rect(market.left, market.top, market.width, market.height)
    cx, cy = row_click_screen_xy(
        crop_height=frame.bgr.shape[0],
        row=row_number,
        window_left=market.left,
        window_top=market.top,
        window_width=market.width,
    )
    smooth_move_to(cx, cy, duration_s=0.14, steps=10, sync=True)
    time.sleep(0.05)
    pico.click_left_prepare(hold_ms=120, ping=True)
    time.sleep(settle_s)


def collect_vendor_listings(
    *,
    roi_path: Path,
    pico: PicoHidSerial,
    max_pages: int = MAX_VENDOR_PAGES,
    page_delay_s: float = CRAFT_PAGE_DELAY_S,
) -> list[dict]:
    """OCR all vendor rows on the item detail view (Price per unit / In stock)."""
    cfg = load_market_roi_config(roi_path)
    market = cfg.require(REGION_MARKET_WINDOW)
    next_btn = cfg.require(REGION_NEXT_PAGE)
    back = cfg.require(REGION_BACK_BUTTON)
    ocr = get_ocr_engine()

    all_rows: list[dict] = []
    prev_fp: PageFingerprint | None = None
    scanned_at = datetime.now(timezone.utc).isoformat()

    for page_i in range(1, max_pages + 1):
        park_cursor_for_ocr(
            back=back,
            next_btn=next_btn,
            on_next=(page_i > 1),
        )
        frame = grab_screen_rect(market.left, market.top, market.width, market.height)
        cur_fp = fingerprint_page(frame.bgr)
        if page_i > 1 and page_unchanged(prev_fp, cur_fp):
            break

        indicator = read_page_indicator(frame.bgr, ocr)
        page_num = indicator.current if indicator else page_i
        rows = parse_page_rows(frame.bgr, page=page_num, ocr=ocr)

        for row in rows:
            if row.price_adena is None:
                continue
            record = row.to_dict()
            record["scanned_at"] = scanned_at
            record["vendor_page"] = page_num
            all_rows.append(record)

        if indicator is None or indicator.is_last:
            break

        prev_fp = cur_fp
        cx, cy = next_btn.center_screen()
        smooth_move_to(cx, cy, duration_s=0.08, steps=4, sync=False)
        time.sleep(0.03)
        pico.click_left_prepare(hold_ms=100, ping=True)
        time.sleep(page_delay_s)

    return all_rows


def collect_search_result_rows(
    *,
    roi_path: Path,
    pico: PicoHidSerial,
    max_pages: int = MAX_SEARCH_RESULT_PAGES,
    page_delay_s: float = CRAFT_PAGE_DELAY_S,
) -> list:
    """OCR search-results list. Only paginates when a ``current / total`` indicator exists."""
    cfg = load_market_roi_config(roi_path)
    market = cfg.require(REGION_MARKET_WINDOW)
    next_btn = cfg.require(REGION_NEXT_PAGE)
    back = cfg.require(REGION_BACK_BUTTON)
    ocr = get_ocr_engine()

    all_rows = []
    prev_fp: PageFingerprint | None = None

    for page_i in range(1, max_pages + 1):
        park_cursor_for_ocr(
            back=back,
            next_btn=next_btn,
            on_next=(page_i > 1),
        )
        frame = grab_screen_rect(market.left, market.top, market.width, market.height)
        cur_fp = fingerprint_page(frame.bgr)
        if page_i > 1 and page_unchanged(prev_fp, cur_fp):
            break

        indicator = read_page_indicator(frame.bgr, ocr)
        page_num = indicator.current if indicator else page_i
        rows = parse_search_result_rows(frame.bgr, page=page_num, ocr=ocr)
        all_rows.extend(rows)

        if indicator is None or indicator.is_last:
            break

        prev_fp = cur_fp
        cx, cy = next_btn.center_screen()
        smooth_move_to(cx, cy, duration_s=0.08, steps=4, sync=False)
        time.sleep(0.03)
        pico.click_left_prepare(hold_ms=100, ping=True)
        time.sleep(page_delay_s)

    return all_rows


def _summarize_listings(
    listings: list[dict],
    *,
    item_id: str,
    search_name: str,
    scanned_at: str,
) -> MaterialPrice:
    if not listings:
        return MaterialPrice(
            item_id=item_id,
            search_name=search_name,
            unit_price_adena=None,
            scanned_at=scanned_at,
        )

    best = min(listings, key=lambda r: int(r["price_adena"]))
    units = best.get("units")
    return MaterialPrice(
        item_id=item_id,
        search_name=search_name,
        unit_price_adena=int(best["price_adena"]),
        vendor=best.get("vendor"),
        units_available=int(units) if units is not None else None,
        listing_count=len(listings),
        source="vendor_search",
        scanned_at=scanned_at,
    )


def _back_from_search_results(
    *,
    back: RoiRect,
    pico: PicoHidSerial,
    back_settle_s: float,
    fast: bool = False,
) -> None:
    """Search results → search hub (one Back)."""
    press_back_button(back=back, pico=pico, settle_s=back_settle_s, fast=fast)


def _back_from_vendor_list(
    *,
    back: RoiRect,
    pico: PicoHidSerial,
    back_settle_s: float,
    fast: bool = False,
) -> None:
    """Vendor list → search results → search hub (two Backs)."""
    press_back_button(back=back, pico=pico, settle_s=back_settle_s, fast=fast)
    press_back_button(back=back, pico=pico, settle_s=back_settle_s, fast=fast)
    time.sleep(0.15)


def fetch_material_vendor_price(
    *,
    item_id: str,
    search_name: str,
    search_queries: list[str] | None = None,
    roi_path: Path,
    pico: PicoHidSerial,
    search: RoiRect,
    back: RoiRect,
    search_settle_s: float = CRAFT_SEARCH_SETTLE_S,
    back_settle_s: float = CRAFT_BACK_SETTLE_S,
    input_mode: str = "pico",
    fast: bool = False,
) -> MaterialPrice:
    """
    Full flow: search → pick matching row → vendor pages → back to search hub.

    Requires the Buy Item window with search box visible (category screen).
    """
    scanned_at = datetime.now(timezone.utc).isoformat()
    market_cfg = load_market_roi_config(roi_path)
    market = market_cfg.require(REGION_MARKET_WINDOW)

    queries = list(search_queries or [search_name])
    seen_q: set[str] = set()
    unique_queries: list[str] = []
    for q in queries:
        key = q.casefold()
        if key in seen_q:
            continue
        seen_q.add(key)
        unique_queries.append(q)

    picked = None
    last_rows = []
    price: MaterialPrice | None = None

    for query in unique_queries:
        print(f"[craft-price] search {query!r} (want {search_name!r})", flush=True)
        submit_search_query(
            query,
            search=search,
            pico=pico,
            settle_s=search_settle_s,
            input_mode=input_mode,
            fast=fast,
        )
        time.sleep(CRAFT_POST_SEARCH_WAIT_S)

        result_rows = collect_search_result_rows(roi_path=roi_path, pico=pico)
        last_rows = result_rows
        picked = pick_result_row(
            result_rows,
            search_name,
            search_query=query,
        )
        if picked is None:
            time.sleep(CRAFT_OCR_RETRY_WAIT_S)
            result_rows = collect_search_result_rows(roi_path=roi_path, pico=pico)
            last_rows = result_rows
            picked = pick_result_row(
                result_rows,
                search_name,
                search_query=query,
            )
        if picked is None:
            visible = filter_search_result_rows(result_rows)
            print(
                f"[craft-price] no match for {search_name!r} via {query!r} — "
                f"OCR: {format_result_rows(visible)}",
                flush=True,
            )
            _back_from_search_results(back=back, pico=pico, back_settle_s=back_settle_s)
            continue

        print(
            f"[craft-price] open vendors — row {picked.row}: {picked.item!r}"
            + (f" (query {query!r})" if query != search_name else ""),
            flush=True,
        )
        _click_row(picked.row, market=market, pico=pico)
        time.sleep(CRAFT_VENDOR_SETTLE_S)

        listings = collect_vendor_listings(roi_path=roi_path, pico=pico)
        price = _summarize_listings(
            listings,
            item_id=item_id,
            search_name=search_name,
            scanned_at=scanned_at,
        )

        if price.unit_price_adena is not None:
            print(
                f"[craft-price] {search_name!r} → {price.unit_price_adena:,} adena "
                f"({price.listing_count} listings, best vendor {price.vendor!r})",
                flush=True,
            )
            _back_from_vendor_list(back=back, pico=pico, back_settle_s=back_settle_s)
            return price

        print(
            f"[craft-price] {search_name!r} — row {picked.row} opened but no vendor "
            f"prices OCR'd, trying next query if any",
            flush=True,
        )
        _back_from_vendor_list(back=back, pico=pico, back_settle_s=back_settle_s)
        picked = None

    print(
        f"[craft-price] search failed for {search_name!r} "
        f"(tried {len(unique_queries)} queries, last OCR: "
        f"{format_result_rows(filter_search_result_rows(last_rows))})",
        flush=True,
    )
    return MaterialPrice(
        item_id=item_id,
        search_name=search_name,
        unit_price_adena=None,
        scanned_at=scanned_at,
    )

"""Search hub → search results → optional vendor list."""

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
from market.craft.match import (
    filter_search_result_rows,
    find_search_result_price_row,
    format_result_rows,
    is_likely_search_hub,
    is_ocr_garbage_item,
    pick_result_row,
    visual_click_row,
)
from market.craft.models import (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_INSUFFICIENT_QTY,
    AVAILABILITY_NOT_ON_MARKET,
    AVAILABILITY_SCAN_UNCERTAIN,
    MaterialPrice,
)
from market.full_list_parser import MarketRow, parse_page_rows
from market.input_ctl import smooth_move_to
from market.ocr_engine import get_ocr_engine
from market.page_fingerprint import PageFingerprint, fingerprint_page, page_unchanged
from market.pagination import PageIndicator, read_page_indicator
from market.pico_hid import PicoHidSerial
from market.run_control import RunControl, StopRequested, check_stop, sleep_checked
from market.search import park_cursor_for_ocr, press_back_button, submit_search_query
from market.ui_layout import search_results_row_click_xy
from market.vendor_page_parser import parse_vendor_page_rows

MAX_VENDOR_PAGES = 15
MAX_SEARCH_RESULT_PAGES = 8

CRAFT_SEARCH_SETTLE_S = 0.2
CRAFT_BACK_SETTLE_S = 0.2
CRAFT_PAGE_DELAY_S = 0.2
CRAFT_ROW_SETTLE_S = 0.25
CRAFT_VENDOR_SETTLE_S = 0.2
CRAFT_RESULTS_HUB_RETRY_S = 0.25


def _search_results_ready(rows: list[MarketRow]) -> bool:
    """True when Screen B listings are visible (not the category hub)."""
    if is_likely_search_hub(rows):
        return False
    if filter_search_result_rows(rows):
        return True
    for row in rows:
        if row.price_adena is not None and row.price_adena >= 50:
            return True
        if row.vendor and (row.item or row.raw_text):
            return True
    return False


def _click_search_result_row(
    *,
    market: RoiRect,
    row: int,
    pico: PicoHidSerial,
    run_control: RunControl | None = None,
) -> None:
    cx, cy = search_results_row_click_xy(market, row=row)
    print(f"[craft-price] click search row {row} at ({cx}, {cy})", flush=True)
    check_stop(run_control)
    smooth_move_to(cx, cy, duration_s=0.1, steps=6, sync=True)
    sleep_checked(0.03, run_control=run_control)
    pico.click_left_prepare(hold_ms=100, ping=True)
    sleep_checked(CRAFT_ROW_SETTLE_S, run_control=run_control)


def _click_next_page(
    *,
    next_btn: RoiRect,
    pico: PicoHidSerial,
    page_delay_s: float = CRAFT_PAGE_DELAY_S,
    run_control: RunControl | None = None,
) -> None:
    cx, cy = next_btn.center_screen()
    smooth_move_to(cx, cy, duration_s=0.08, steps=4, sync=False)
    time.sleep(0.03)
    pico.click_left_prepare(hold_ms=100, ping=True)
    sleep_checked(page_delay_s, run_control=run_control)


def _read_search_results_page(
    *,
    roi_path: Path,
    pico: PicoHidSerial,
    page_i: int,
    run_control: RunControl | None = None,
) -> tuple[list[MarketRow], PageIndicator | None]:
    """OCR one page of the search-results list (Screen B)."""
    cfg = load_market_roi_config(roi_path)
    market = cfg.require(REGION_MARKET_WINDOW)
    back = cfg.require(REGION_BACK_BUTTON)
    next_btn = cfg.require(REGION_NEXT_PAGE)
    ocr = get_ocr_engine()

    if run_control and run_control.should_stop():
        raise StopRequested()
    park_cursor_for_ocr(back=back, next_btn=next_btn, on_next=(page_i > 1), settle_s=0.04, move_duration_s=0.08)
    frame = grab_screen_rect(market.left, market.top, market.width, market.height)
    indicator = read_page_indicator(frame.bgr, ocr)
    # Search hits are often 1–2 rows; row-band fallback OCRs 7 strips and takes ~3–5 s.
    rows = parse_page_rows(frame.bgr, page=page_i, ocr=ocr, row_fallback=False)
    return rows, indicator


def _scan_search_results(
    *,
    roi_path: Path,
    pico: PicoHidSerial,
    search_name: str,
    query: str,
    qty_needed: int | None,
    prefer_vendor_list: bool = False,
    run_control: RunControl | None = None,
) -> tuple[MarketRow | None, MarketRow | None, list[MarketRow]]:
    """
    Walk paginated search results until the target item is found.

    Returns ``(picked_row, fast_price_row, visible_on_matched_page)``.
    Either ``picked_row`` (open vendors) or ``fast_price_row`` (qty 1) is set.
    """
    cfg = load_market_roi_config(roi_path)
    next_btn = cfg.require(REGION_NEXT_PAGE)
    last_visible: list[MarketRow] = []

    for page_i in range(1, MAX_SEARCH_RESULT_PAGES + 1):
        if page_i == 1:
            rows, indicator = _read_search_results_with_retry(
                roi_path=roi_path,
                pico=pico,
                run_control=run_control,
            )
        else:
            rows, indicator = _read_search_results_page(
                roi_path=roi_path,
                pico=pico,
                page_i=page_i,
                run_control=run_control,
            )

        visible = filter_search_result_rows(rows)
        last_visible = visible

        price_row = find_search_result_price_row(
            rows,
            search_name,
            search_query=query,
        )
        if (
            not prefer_vendor_list
            and price_row
            and _can_use_search_results_min_price(price_row, qty_needed)
        ):
            page_label = _page_label(indicator, page_i)
            print(
                f"[craft-price] matched {search_name!r} on search results{page_label}",
                flush=True,
            )
            return None, price_row, visible

        picked = pick_result_row(rows, search_name, search_query=query)
        if picked is not None:
            page_label = _page_label(indicator, page_i)
            print(
                f"[craft-price] matched {search_name!r} on search results{page_label} "
                f"— {picked.item!r}",
                flush=True,
            )
            return picked, None, visible

        if indicator is None or indicator.is_last:
            break

        print(
            f"[craft-price] search results {indicator.current}/{indicator.total} "
            f"— {search_name!r} not here, next page",
            flush=True,
        )
        _click_next_page(
            next_btn=next_btn,
            pico=pico,
            run_control=run_control,
        )

    return None, None, last_visible


def _page_label(indicator: PageIndicator | None, page_i: int) -> str:
    if indicator:
        return f" (page {indicator.current}/{indicator.total})"
    if page_i > 1:
        return f" (page {page_i})"
    return ""


def _read_search_results_with_retry(
    *,
    roi_path: Path,
    pico: PicoHidSerial,
    run_control: RunControl | None = None,
) -> tuple[list[MarketRow], PageIndicator | None]:
    """OCR search results page 1; one retry only if the category hub is still showing."""
    rows, indicator = _read_search_results_page(
        roi_path=roi_path,
        pico=pico,
        page_i=1,
        run_control=run_control,
    )
    if _search_results_ready(rows):
        return rows, indicator

    if not rows or is_likely_search_hub(rows):
        sleep_checked(CRAFT_RESULTS_HUB_RETRY_S, run_control=run_control)
        print("[craft-price] waiting for search-results list …", flush=True)
        return _read_search_results_page(
            roi_path=roi_path,
            pico=pico,
            page_i=1,
            run_control=run_control,
        )

    return rows, indicator


def _can_use_search_results_min_price(
    picked: MarketRow | None,
    qty_needed: int | None,
) -> bool:
    """Screen B min price is only enough for single-unit buys."""
    if picked is None or picked.price_adena is None or picked.price_adena < 50:
        return False
    need = qty_needed if qty_needed is not None else 1
    return need <= 1


def _summarize_search_result_row(
    row: MarketRow,
    *,
    item_id: str,
    search_name: str,
    scanned_at: str,
) -> MaterialPrice:
    return MaterialPrice(
        item_id=item_id,
        search_name=search_name,
        unit_price_adena=int(row.price_adena) if row.price_adena is not None else None,
        vendor=row.vendor,
        units_available=int(row.units) if row.units is not None else None,
        listing_count=1,
        source="search_results",
        scanned_at=scanned_at,
        availability=AVAILABILITY_AVAILABLE,
        cached_unit_price_adena=int(row.price_adena) if row.price_adena is not None else None,
    )


def ocr_vendor_listings_once(
    *,
    roi_path: Path,
    run_control: RunControl | None = None,
) -> list[dict]:
    """OCR the current vendor list screen once — no pagination clicks."""
    cfg = load_market_roi_config(roi_path)
    market = cfg.require(REGION_MARKET_WINDOW)
    back = cfg.require(REGION_BACK_BUTTON)
    ocr = get_ocr_engine()

    if run_control and run_control.should_stop():
        raise StopRequested()
    park_cursor_for_ocr(back=back, settle_s=0.04, move_duration_s=0.08)
    frame = grab_screen_rect(market.left, market.top, market.width, market.height)
    rows = parse_vendor_page_rows(frame.bgr, page=1, ocr=ocr)
    scanned_at = datetime.now(timezone.utc).isoformat()
    out: list[dict] = []
    for row in rows:
        record = dict(row)
        record["scanned_at"] = scanned_at
        record["vendor_page"] = 1
        out.append(record)
    return out


def collect_vendor_listings(
    *,
    roi_path: Path,
    pico: PicoHidSerial,
    max_pages: int = MAX_VENDOR_PAGES,
    page_delay_s: float = CRAFT_PAGE_DELAY_S,
    qty_needed: int | None = None,
    run_control: RunControl | None = None,
) -> list[dict]:
    """OCR vendor list after opening a search-result row."""
    if max_pages <= 1:
        return ocr_vendor_listings_once(roi_path=roi_path, run_control=run_control)
    cfg = load_market_roi_config(roi_path)
    market = cfg.require(REGION_MARKET_WINDOW)
    next_btn = cfg.require(REGION_NEXT_PAGE)
    back = cfg.require(REGION_BACK_BUTTON)
    ocr = get_ocr_engine()

    all_rows: list[dict] = []
    prev_fp: PageFingerprint | None = None
    scanned_at = datetime.now(timezone.utc).isoformat()
    stock_seen = 0

    for page_i in range(1, max_pages + 1):
        if run_control and run_control.should_stop():
            raise StopRequested()
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
        vendor_rows = parse_vendor_page_rows(frame.bgr, page=page_num, ocr=ocr)

        page_stock = 0
        page_all_known = True
        for row in vendor_rows:
            if row.get("price_adena") is None:
                continue
            if row.get("units") is None:
                page_all_known = False
            else:
                page_stock += int(row["units"])
            record = dict(row)
            record["scanned_at"] = scanned_at
            record["vendor_page"] = page_num
            all_rows.append(record)

        if qty_needed is not None and page_stock > 0:
            stock_seen += page_stock
            if page_i == 1 and page_all_known and page_stock >= qty_needed:
                print(
                    f"[craft-price] page 1 stock {page_stock:,} >= need {qty_needed:,} "
                    f"— skip remaining vendor pages",
                    flush=True,
                )
                break
            if stock_seen >= qty_needed:
                print(
                    f"[craft-price] vendor stock {stock_seen:,} >= need {qty_needed:,} "
                    f"after page {page_i} — skip remaining pages",
                    flush=True,
                )
                break

        if indicator is None or indicator.is_last:
            break

        prev_fp = cur_fp
        _click_next_page(
            next_btn=next_btn,
            pico=pico,
            page_delay_s=page_delay_s,
            run_control=run_control,
        )

    return all_rows


def _fill_unit_price(listings: list[dict], qty: int) -> tuple[int, str | None] | None:
    """Average unit price buying ``qty`` from cheapest listings with known stock."""
    ordered = sorted(listings, key=lambda r: int(r["price_adena"]))
    remaining = qty
    total = 0
    first_vendor: str | None = None
    for row in ordered:
        if remaining <= 0:
            break
        price = int(row["price_adena"])
        units = row.get("units")
        if units is None:
            continue
        take = min(remaining, int(units))
        if take <= 0:
            continue
        total += take * price
        remaining -= take
        if first_vendor is None:
            first_vendor = row.get("vendor")
    if remaining > 0:
        return None
    return (total + qty - 1) // qty, first_vendor


def _summarize_listings(
    listings: list[dict],
    *,
    item_id: str,
    search_name: str,
    scanned_at: str,
    qty_needed: int | None = None,
) -> MaterialPrice:
    valid = [
        r for r in listings
        if r.get("price_adena") is not None
        and int(r["price_adena"]) >= 50
        and not is_ocr_garbage_item(str(r.get("item") or ""))
    ]
    if not valid:
        return MaterialPrice(
            item_id=item_id,
            search_name=search_name,
            unit_price_adena=None,
            scanned_at=scanned_at,
            availability=AVAILABILITY_NOT_ON_MARKET,
            availability_note="vendor list empty or no readable prices",
            listing_count=0,
        )

    need = qty_needed if qty_needed is not None else 1
    if need > 1:
        filled = _fill_unit_price(valid, need)
        if filled is not None:
            unit_price, vendor = filled
            return MaterialPrice(
                item_id=item_id,
                search_name=search_name,
                unit_price_adena=unit_price,
                vendor=vendor,
                listing_count=len(valid),
                source="vendor_search",
                scanned_at=scanned_at,
                availability=AVAILABILITY_AVAILABLE,
                cached_unit_price_adena=unit_price,
            )
        stock_known = sum(int(r["units"]) for r in valid if r.get("units") is not None)
        best = min(valid, key=lambda r: int(r["price_adena"]))
        best_price = int(best["price_adena"])
        if stock_known > 0:
            return MaterialPrice(
                item_id=item_id,
                search_name=search_name,
                unit_price_adena=best_price,
                vendor=best.get("vendor"),
                units_available=stock_known,
                listing_count=len(valid),
                source="vendor_search",
                scanned_at=scanned_at,
                availability=AVAILABILITY_INSUFFICIENT_QTY,
                availability_note=f"need {need:,}, only {stock_known:,} units on market",
                cached_unit_price_adena=best_price,
            )
        return MaterialPrice(
            item_id=item_id,
            search_name=search_name,
            unit_price_adena=None,
            scanned_at=scanned_at,
            availability=AVAILABILITY_NOT_ON_MARKET,
            availability_note=f"need {need:,}, no listings with known stock",
            listing_count=len(valid),
        )

    best = min(valid, key=lambda r: int(r["price_adena"]))
    units = best.get("units")
    best_price = int(best["price_adena"])
    return MaterialPrice(
        item_id=item_id,
        search_name=search_name,
        unit_price_adena=best_price,
        vendor=best.get("vendor"),
        units_available=int(units) if units is not None else None,
        listing_count=len(valid),
        source="vendor_search",
        scanned_at=scanned_at,
        availability=AVAILABILITY_AVAILABLE,
        cached_unit_price_adena=best_price,
    )


def _back_to_search_hub(
    *,
    back: RoiRect,
    pico: PicoHidSerial,
    back_settle_s: float,
    count: int,
    run_control: RunControl | None = None,
) -> None:
    """Press Back ``count`` times to return to the search hub (search bar visible)."""
    check_stop(run_control)
    for _ in range(count):
        press_back_button(
            back=back,
            pico=pico,
            settle_s=back_settle_s,
            fast=True,
            run_control=run_control,
        )
        sleep_checked(0.08, run_control=run_control)
    if count == 1:
        print("[craft-price] back ×1 — search hub", flush=True)
    else:
        print(f"[craft-price] back ×{count} — search hub", flush=True)


def open_vendor_list_price_from_results(
    *,
    item_id: str,
    search_name: str,
    search_query: str | None = None,
    roi_path: Path,
    pico: PicoHidSerial,
    back: RoiRect,
    qty_needed: int | None = 1,
    max_vendor_pages: int = MAX_VENDOR_PAGES,
    back_settle_s: float = CRAFT_BACK_SETTLE_S,
    run_control: RunControl | None = None,
) -> MaterialPrice:
    """
    Search results screen → pick row → vendor list → back ×2 to search hub.

    Assumes the search query was already submitted and results are visible.
    """
    scanned_at = datetime.now(timezone.utc).isoformat()
    query = search_query or search_name
    market_cfg = load_market_roi_config(roi_path)
    market = market_cfg.require(REGION_MARKET_WINDOW)

    picked, _, visible = _scan_search_results(
        roi_path=roi_path,
        pico=pico,
        search_name=search_name,
        query=query,
        qty_needed=qty_needed,
        prefer_vendor_list=True,
        run_control=run_control,
    )

    if picked is None:
        note = (
            "no row match on a loaded search-results page"
            if visible
            else "search results empty or not readable"
        )
        print(f"[search] vendor open failed — {note}", flush=True)
        _back_to_search_hub(
            back=back,
            pico=pico,
            back_settle_s=back_settle_s,
            count=1,
            run_control=run_control,
        )
        return MaterialPrice(
            item_id=item_id,
            search_name=search_name,
            unit_price_adena=None,
            scanned_at=scanned_at,
            availability=AVAILABILITY_SCAN_UNCERTAIN,
            availability_note=note,
            listing_count=0,
        )

    click_row = visual_click_row(visible, picked)
    print(
        f"[search] open vendors — row {click_row}: {picked.item!r}",
        flush=True,
    )
    _click_search_result_row(
        market=market,
        row=click_row,
        pico=pico,
        run_control=run_control,
    )
    sleep_checked(CRAFT_VENDOR_SETTLE_S, run_control=run_control)

    listings = collect_vendor_listings(
        roi_path=roi_path,
        pico=pico,
        qty_needed=qty_needed,
        max_pages=max_vendor_pages,
        run_control=run_control,
    )
    price = _summarize_listings(
        listings,
        item_id=item_id,
        search_name=search_name,
        scanned_at=scanned_at,
        qty_needed=qty_needed,
    )

    if price.unit_price_adena is not None and price.availability == AVAILABILITY_AVAILABLE:
        print(
            f"[search] {search_name!r} → {price.unit_price_adena:,} adena "
            f"({price.listing_count} listings, best vendor {price.vendor!r})",
            flush=True,
        )
    elif price.availability == AVAILABILITY_NOT_ON_MARKET:
        print(
            f"[search] {search_name!r} — no vendor listings ({price.availability_note})",
            flush=True,
        )
    else:
        print(
            f"[search] {search_name!r} — {price.availability_note or price.availability}",
            flush=True,
        )

    _back_to_search_hub(
        back=back,
        pico=pico,
        back_settle_s=back_settle_s,
        count=2,
        run_control=run_control,
    )
    check_stop(run_control)
    return price


def fetch_material_vendor_price(
    *,
    item_id: str,
    search_name: str,
    search_queries: list[str] | None = None,
    qty_needed: int | None = None,
    roi_path: Path,
    pico: PicoHidSerial,
    search: RoiRect,
    back: RoiRect,
    search_settle_s: float = CRAFT_SEARCH_SETTLE_S,
    back_settle_s: float = CRAFT_BACK_SETTLE_S,
    input_mode: str = "pico",
    fast: bool = False,
    prefer_vendor_list: bool = False,
    max_vendor_pages: int = MAX_VENDOR_PAGES,
    run_control: RunControl | None = None,
) -> MaterialPrice:
    """
    Hub → search results → (optional) vendor list.

    Three screens: search hub (search bar) → results (min price) → vendors (all sellers).
    Use min price on the results screen when enough; otherwise open the matched row.
    Navigation: results only → 1× Back; opened vendors → 2× Back.
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

    last_visible: list[MarketRow] = []
    last_failure_note = "search did not find a matching row"
    last_failure_kind = AVAILABILITY_SCAN_UNCERTAIN

    for query in unique_queries:
        if run_control and run_control.should_stop():
            raise StopRequested()
        print(f"[craft-price] search {query!r} (want {search_name!r})", flush=True)
        submit_search_query(
            query,
            search=search,
            pico=pico,
            settle_s=search_settle_s,
            input_mode=input_mode,
            fast=fast,
            run_control=run_control,
        )

        picked, price_row, visible = _scan_search_results(
            roi_path=roi_path,
            pico=pico,
            search_name=search_name,
            query=query,
            qty_needed=qty_needed,
            prefer_vendor_list=prefer_vendor_list,
            run_control=run_control,
        )
        last_visible = visible

        if (
            not prefer_vendor_list
            and price_row
            and _can_use_search_results_min_price(price_row, qty_needed)
        ):
            price = _summarize_search_result_row(
                price_row,
                item_id=item_id,
                search_name=search_name,
                scanned_at=scanned_at,
            )
            print(
                f"[craft-price] {search_name!r} → {price.unit_price_adena:,} adena "
                f"(min price on search results, vendor {price.vendor!r})",
                flush=True,
            )
            _back_to_search_hub(
                back=back,
                pico=pico,
                back_settle_s=back_settle_s,
                count=1,
                run_control=run_control,
            )
            check_stop(run_control)
            return price

        if picked is None:
            if visible:
                last_failure_kind = AVAILABILITY_SCAN_UNCERTAIN
                last_failure_note = "no row match on a loaded search-results page"
            else:
                last_failure_kind = AVAILABILITY_SCAN_UNCERTAIN
                last_failure_note = "search results empty or not readable"
            print(
                f"[craft-price] no row match for {search_name!r} "
                f"(visible: {format_result_rows(visible)})",
                flush=True,
            )
            _back_to_search_hub(
                back=back,
                pico=pico,
                back_settle_s=back_settle_s,
                count=1,
                run_control=run_control,
            )
            continue

        click_row = visual_click_row(visible, picked)
        print(
            f"[craft-price] open vendors — row {click_row}: {picked.item!r}"
            + (f" (query {query!r})" if query != search_name else ""),
            flush=True,
        )

        _click_search_result_row(
            market=market,
            row=click_row,
            pico=pico,
            run_control=run_control,
        )
        sleep_checked(CRAFT_VENDOR_SETTLE_S, run_control=run_control)

        listings = collect_vendor_listings(
            roi_path=roi_path,
            pico=pico,
            qty_needed=qty_needed,
            max_pages=max_vendor_pages,
            run_control=run_control,
        )
        price = _summarize_listings(
            listings,
            item_id=item_id,
            search_name=search_name,
            scanned_at=scanned_at,
            qty_needed=qty_needed,
        )

        if price.unit_price_adena is not None and price.availability == AVAILABILITY_AVAILABLE:
            print(
                f"[craft-price] {search_name!r} → {price.unit_price_adena:,} adena "
                f"({price.listing_count} listings, best vendor {price.vendor!r})",
                flush=True,
            )
            _back_to_search_hub(
                back=back,
                pico=pico,
                back_settle_s=back_settle_s,
                count=2,
                run_control=run_control,
            )
            check_stop(run_control)
            return price

        if price.availability == AVAILABILITY_INSUFFICIENT_QTY:
            print(
                f"[craft-price] {search_name!r} — insufficient market stock "
                f"({price.availability_note})",
                flush=True,
            )
            _back_to_search_hub(
                back=back,
                pico=pico,
                back_settle_s=back_settle_s,
                count=2,
                run_control=run_control,
            )
            check_stop(run_control)
            return price

        if price.availability == AVAILABILITY_NOT_ON_MARKET:
            last_failure_kind = AVAILABILITY_NOT_ON_MARKET
            last_failure_note = price.availability_note or "no vendor listings"
        else:
            last_failure_kind = AVAILABILITY_SCAN_UNCERTAIN
            last_failure_note = "matched row but vendor OCR returned no prices"

        print(
            f"[craft-price] {search_name!r} — {last_failure_note} "
            f"on row {click_row} (query {query!r})",
            flush=True,
        )
        _back_to_search_hub(
            back=back,
            pico=pico,
            back_settle_s=back_settle_s,
            count=2,
            run_control=run_control,
        )

    print(
        f"[craft-price] search failed for {search_name!r} "
        f"(tried {len(unique_queries)} queries, last visible: "
        f"{format_result_rows(last_visible)})",
        flush=True,
    )
    check_stop(run_control)
    return MaterialPrice(
        item_id=item_id,
        search_name=search_name,
        unit_price_adena=None,
        scanned_at=scanned_at,
        availability=last_failure_kind,
        availability_note=last_failure_note,
        listing_count=0,
    )

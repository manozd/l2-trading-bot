"""Detect Buy Item UI depth and navigate back to the search hub."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from market.capture import grab_screen_rect
from market.capture_rois import REGION_MARKET_WINDOW, RoiRect, load_market_roi_config
from market.craft.match import filter_search_result_rows, is_ui_chrome_row
from market.full_list_parser import MarketRow, parse_page_rows, parse_search_result_rows
from market.ocr_engine import get_ocr_engine
from market.pico_hid import PicoHidSerial
from market.run_control import RunControl, StopRequested, sleep_checked
from market.search import press_back_button

MarketScreen = Literal["hub", "search_results", "vendor_list"]

_HUB_TEXT_MARKERS = (
    "greetings",
    "choose a category",
    "sell item",
    "buy items",
    "buy item",
)

_NAV_BACK_GAP_S = 0.30


def _row_text(row: MarketRow) -> str:
    return (row.item or row.raw_text or "").casefold()


def _has_hub_markers(rows: list[MarketRow]) -> bool:
    for row in rows:
        text = _row_text(row)
        if any(marker in text for marker in _HUB_TEXT_MARKERS):
            return True
        if is_ui_chrome_row(row):
            return True
    return False


def _is_vendor_list(vendor_rows: list[MarketRow]) -> bool:
    priced = [r for r in vendor_rows if r.price_adena is not None]
    if len(priced) >= 2:
        return True
    if len(priced) == 1:
        row = priced[0]
        if row.vendor and row.price_adena is not None and row.price_adena >= 50:
            return True
        if row.price_adena is not None and row.units is not None and row.price_adena >= 50:
            return True
    return False


def _is_search_results(search_rows: list[MarketRow]) -> bool:
    if _has_hub_markers(search_rows):
        return False
    filtered = filter_search_result_rows(search_rows)
    if len(filtered) >= 2:
        return True
    if len(filtered) == 1:
        item = (filtered[0].item or "").strip()
        if len(item) >= 6:
            return True
    return False


def detect_market_screen(roi_path: Path) -> MarketScreen:
    """Classify the current Buy Item window screen from one OCR capture."""
    cfg = load_market_roi_config(roi_path)
    market = cfg.require(REGION_MARKET_WINDOW)
    frame = grab_screen_rect(market.left, market.top, market.width, market.height)
    ocr = get_ocr_engine()

    vendor_rows = parse_page_rows(frame.bgr, page=1, ocr=ocr)
    search_rows = parse_search_result_rows(frame.bgr, page=1, ocr=ocr)

    if _has_hub_markers(search_rows) or _has_hub_markers(vendor_rows):
        return "hub"

    if _is_vendor_list(vendor_rows):
        return "vendor_list"

    if _is_search_results(search_rows):
        return "search_results"

    return "hub"


def return_to_search_hub(
    *,
    roi_path: Path,
    back: RoiRect,
    pico: PicoHidSerial,
    back_settle_s: float,
    run_control: RunControl | None = None,
    max_backs: int = 4,
) -> MarketScreen:
    """
    OCR-guided Back clicks until the category/search hub is visible.

    Must be on hub before typing a new search query.
    """
    screen = detect_market_screen(roi_path)
    if screen == "hub":
        return screen

    print(f"[craft-price] return to search hub (currently {screen!r})", flush=True)
    for back_i in range(1, max_backs + 1):
        if run_control and run_control.should_stop():
            raise StopRequested()
        press_back_button(
            back=back,
            pico=pico,
            settle_s=back_settle_s,
            fast=False,
            run_control=run_control,
        )
        sleep_checked(_NAV_BACK_GAP_S, run_control=run_control)
        screen = detect_market_screen(roi_path)
        print(f"[craft-price] after back {back_i}: screen={screen!r}", flush=True)
        if screen == "hub":
            return screen

    if screen != "hub":
        print(
            f"[craft-price] warning: could not reach search hub (still {screen!r})",
            flush=True,
        )
    return screen


# Alias for older call sites
navigate_back_to_hub = return_to_search_hub

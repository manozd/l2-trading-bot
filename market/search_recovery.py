"""Detect empty search results and recover to the search hub."""

from __future__ import annotations

from typing import Literal

from market.capture import grab_screen_rect
from market.capture_rois import REGION_BACK_BUTTON, REGION_MARKET_WINDOW, RoiRect, load_market_roi_config
from market.full_list_parser import ROWS_PER_PAGE, _row_bands
from market.icon_hash import row_icon_slot_occupied
from market.page_fingerprint import list_body_bgr
from market.pico_hid import PicoHidSerial
from market.run_control import RunControl, check_stop, sleep_checked
from market.search import clear_search_field, press_back_button

SearchListState = Literal["has_items", "empty_list", "unreadable"]


def list_body_item_icon_count(bgr) -> int:
    """Count list rows whose left icon slot looks occupied (real item, not blank UI)."""
    body = list_body_bgr(bgr)
    h = body.shape[0]
    bands = _row_bands(h, top_frac=0.0, rows_per_page=ROWS_PER_PAGE)
    count = 0
    for y0, y1 in bands:
        if y1 <= y0:
            continue
        row_crop = body[y0:y1, :]
        if row_icon_slot_occupied(row_crop):
            count += 1
    return count


def detect_search_list_state(*, roi_path) -> SearchListState:
    """
    Inspect the market list area after a search.

    ``empty_list`` — filtered search returned no sell listings (sold out).
    ``has_items`` — at least one row has an item icon.
    """
    from market.search import park_cursor_on_back

    cfg = load_market_roi_config(roi_path)
    market = cfg.require(REGION_MARKET_WINDOW)
    back = cfg.require(REGION_BACK_BUTTON)
    park_cursor_on_back(back)
    frame = grab_screen_rect(market.left, market.top, market.width, market.height)
    if frame.bgr is None or frame.bgr.size == 0:
        return "unreadable"
    icons = list_body_item_icon_count(frame.bgr)
    if icons > 0:
        return "has_items"
    return "empty_list"


def recover_to_search_hub(
    *,
    back: RoiRect,
    search: RoiRect,
    pico: PicoHidSerial,
    back_settle_s: float = 0.35,
    run_control: RunControl | None = None,
) -> None:
    """
    Leave a filtered (possibly empty) results screen and clear the search bar.

    Typical flow: Back once to hub, then select-all/clear search field so the next
    query does not append to stale text.
    """
    check_stop(run_control)
    print("[search] recover — back to hub and clear search bar", flush=True)
    press_back_button(
        back=back,
        pico=pico,
        settle_s=back_settle_s,
        fast=True,
        run_control=run_control,
    )
    sleep_checked(0.18, run_control=run_control)
    clear_search_field(search, pico, run_control=run_control)

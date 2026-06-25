"""Fixed UI control positions inside the Buy Item market window (BOHPTS).

Calibrate only ``market_window``; search box, next page, and back button are
derived from these fractions of the window size.
"""

from __future__ import annotations

from dataclasses import dataclass

from market.capture_rois import (
    REGION_BACK_BUTTON,
    REGION_MARKET_WINDOW,
    REGION_NEXT_PAGE,
    REGION_SEARCH_BOX,
    RoiRect,
)


@dataclass(frozen=True)
class RelRect:
    """Region as fractions of the market window (0.0–1.0)."""

    left: float
    top: float
    width: float
    height: float

    def to_screen(self, window: RoiRect) -> RoiRect:
        return RoiRect(
            left=window.left + round(self.left * window.width),
            top=window.top + round(self.top * window.height),
            width=max(1, round(self.width * window.width)),
            height=max(1, round(self.height * window.height)),
        )


# Measured from a working BOHPTS Buy Item window (352×406 px content area).
_SEARCH = RelRect(left=62 / 352, top=79 / 406, width=158 / 352, height=23 / 406)
_NEXT_PAGE = RelRect(left=202 / 352, top=350 / 406, width=14 / 352, height=15 / 406)
_BACK = RelRect(left=77 / 352, top=373 / 406, width=198 / 352, height=28 / 406)

# Search-results rows are tall (name + vendor + price); anchor clicks from search box.
_SEARCH_RESULT_NAME_OFFSET_PX = 10
_SEARCH_RESULT_ROW_STEP_PX = 62
# Crop only the listing strip below the search field (above decorative art).
_SEARCH_RESULTS_CROP_HEIGHT_PX = 90

_DERIVED: dict[str, RelRect] = {
    REGION_SEARCH_BOX: _SEARCH,
    REGION_NEXT_PAGE: _NEXT_PAGE,
    REGION_BACK_BUTTON: _BACK,
}


def derive_market_ui_regions(market_window: RoiRect) -> dict[str, RoiRect]:
    """Return search / next / back ROIs in screen coordinates."""
    out = {REGION_MARKET_WINDOW: market_window}
    for name, rel in _DERIVED.items():
        out[name] = rel.to_screen(market_window)
    return out


def search_list_top_frac(market_window: RoiRect, search: RoiRect) -> float:
    """List body starts just below the search box (for search-results OCR rows)."""
    bottom_px = search.top + search.height - market_window.top
    list_top_px = bottom_px + 2
    return min(0.38, max(0.10, list_top_px / max(market_window.height, 1)))


def search_results_crop_bounds(market_window: RoiRect, search: RoiRect) -> tuple[int, int]:
    """Y0/Y1 in market-window image pixels for the search-results strip."""
    y0 = (search.top + search.height - market_window.top) + 2
    y1 = y0 + _SEARCH_RESULTS_CROP_HEIGHT_PX
    return y0, min(y1, market_window.height - 6)


def search_result_row_click_xy(
    market_window: RoiRect,
    search: RoiRect,
    row: int = 1,
) -> tuple[int, int]:
    """Screen coords on the item-name line of a search-results row (1-based)."""
    list_top = search.top + search.height
    y = list_top + _SEARCH_RESULT_NAME_OFFSET_PX + (max(1, row) - 1) * _SEARCH_RESULT_ROW_STEP_PX
    x = market_window.left + round(market_window.width * 0.20)
    return x, y


def search_results_row_click_xy(market_window: RoiRect, row: int = 1) -> tuple[int, int]:
    """Screen B: click a search-result row (list starts below title, no search bar)."""
    from market.full_list_parser import ROWS_PER_PAGE, TOP_FRAC, _row_bands

    bands = _row_bands(market_window.height, top_frac=TOP_FRAC, rows_per_page=ROWS_PER_PAGE)
    idx = max(0, min(row - 1, len(bands) - 1))
    y0, y1 = bands[idx]
    cy = market_window.top + (y0 + y1) // 2
    cx = market_window.left + round(market_window.width * 0.20)
    return cx, cy

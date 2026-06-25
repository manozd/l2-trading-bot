"""OCR + parse BOHPTS Buy Item → Full List page crop (7 rows per page)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from PIL import Image

from market.icon_hash import icon_hash_from_row
from market.item_identity import item_slug, make_item_key
from market.ocr_engine import get_ocr_engine, run_ocr
from market.row_fields import PriceConfidence, parse_fields_from_lines

ROWS_PER_PAGE = 7
# Search hits are tall (name + vendor + price); fewer bands than vendor list.
SEARCH_RESULT_ROWS = 4
SEARCH_CLICK_Y_FRAC = 0.18
# Whole Buy Item window ROI (title + list + pagination + back).
TOP_FRAC = 0.10
# List body ends just above "4 / 131" pagination text (~88% on typical crops).
LIST_BOTTOM_FRAC = 0.88
PRICE_SLOP_PX = 18
LAST_ROW_PRICE_SLOP_PX = 40


@dataclass(frozen=True)
class MarketRow:
    page: int
    row: int
    item: str | None
    vendor: str | None
    price_adena: int | None
    units: int | None
    item_icon_hash: str | None
    raw_text: str
    price_confidence: PriceConfidence = "none"
    enchant: int | None = None
    item_base: str | None = None
    item_display: str | None = None

    @property
    def item_key(self) -> str | None:
        return make_item_key(
            icon_hash=self.item_icon_hash,
            item=self.item_base or self.item,
            enchant=self.enchant,
        )

    @property
    def item_slug(self) -> str:
        return item_slug(self.item)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["item_key"] = self.item_key
        d["item_slug"] = self.item_slug
        return d


def parse_row_text(text: str, *, page: int, row: int) -> MarketRow:
    fields = parse_fields_from_lines([text])
    return MarketRow(
        page=page,
        row=row,
        item=fields["item"],
        vendor=fields["vendor"],
        price_adena=fields["price_adena"],
        units=fields["units"],
        item_icon_hash=None,
        raw_text=fields["raw_text"],
        price_confidence=fields["price_confidence"],
        enchant=fields.get("enchant"),
        item_base=fields.get("item_base"),
        item_display=fields.get("item_display"),
    )


def is_plausible_market_row(row: MarketRow) -> bool:
    if row.price_adena is not None and row.price_adena >= 1000:
        return True
    if row.vendor:
        return bool(row.item or row.price_adena is not None or row.units is not None)
    if row.item and row.units is not None:
        return True
    return row.price_adena is not None and row.units is not None


def is_plausible_search_result_row(row: MarketRow) -> bool:
    """Search results list item names only — no vendor/price required."""
    if row.item and len(row.item.strip()) >= 2:
        low = row.item.casefold()
        if "adena" in low or "price per" in low or "min. price" in low:
            return False
        if "on market" in low or re.search(r"\bunits?\b", low):
            return False
        if len(row.item.strip()) <= 4 and row.item.strip().isalpha() and row.item.isupper():
            return False
        if re.fullmatch(r"[\d,\s]+", row.item.replace(" ", "")):
            return False
        return True
    raw = (row.raw_text or "").strip()
    if len(raw) < 2:
        return False
    low = raw.lower()
    if "adena" in low or "price per" in low or "vendor" in low:
        return False
    if low.startswith("vendor"):
        return False
    if "on market" in low or re.search(r"\bunits?\b", low):
        return False
    return True


def _coerce_search_result_item(row: MarketRow) -> MarketRow:
    if row.item and row.item.strip():
        return row
    raw = (row.raw_text or "").strip()
    if not raw:
        return row
    from market.row_fields import _clean_item_name

    item = _clean_item_name(raw) or raw
    return MarketRow(
        page=row.page,
        row=row.row,
        item=item,
        vendor=row.vendor,
        price_adena=row.price_adena,
        units=row.units,
        item_icon_hash=row.item_icon_hash,
        raw_text=row.raw_text,
        price_confidence=row.price_confidence,
        enchant=row.enchant,
        item_base=row.item_base,
        item_display=row.item_display,
    )


def _list_bounds(
    height: int,
    *,
    top_frac: float = TOP_FRAC,
    rows_per_page: int = ROWS_PER_PAGE,
) -> tuple[int, int]:
    top_skip = int(height * top_frac)
    list_y1 = min(int(height * LIST_BOTTOM_FRAC), height - 4)
    return top_skip, max(top_skip + rows_per_page * 8, list_y1)


def icon_hash_for_list_row(
    bgr: np.ndarray,
    row: int,
    *,
    top_frac: float = TOP_FRAC,
    rows_per_page: int = ROWS_PER_PAGE,
) -> str:
    """Icon fingerprint for a fixed list row index (1-based), independent of OCR."""
    bands = _row_bands(bgr.shape[0], top_frac=top_frac, rows_per_page=rows_per_page)
    idx = max(0, min(row - 1, len(bands) - 1))
    y0, y1 = bands[idx]
    return icon_hash_from_row(bgr[y0:y1, :])


def _row_bands(
    height: int,
    *,
    top_frac: float = TOP_FRAC,
    rows_per_page: int = ROWS_PER_PAGE,
) -> list[tuple[int, int]]:
    top_skip, list_y1 = _list_bounds(height, top_frac=top_frac, rows_per_page=rows_per_page)
    body = list_y1 - top_skip
    row_h = body // rows_per_page
    bands: list[tuple[int, int]] = []
    for i in range(rows_per_page):
        y0 = top_skip + i * row_h
        if i < rows_per_page - 1:
            y1 = top_skip + (i + 1) * row_h
        else:
            y1 = list_y1
        bands.append((y0, y1))
    return bands


def search_result_click_xy(
    bgr: np.ndarray,
    *,
    item_label: str,
    window_left: int,
    window_top: int,
    window_width: int,
    top_frac: float = TOP_FRAC,
    ocr=None,
) -> tuple[int, int] | None:
    """Click the OCR box for a search hit (topmost matching line), not a fixed row band."""
    if ocr is None:
        ocr = get_ocr_engine()
    from market.craft.match import _compact, _compact_prefix_match, _norm

    target = _norm(item_label)
    target_c = _compact(item_label)
    top_skip, list_y1 = _list_bounds(
        bgr.shape[0], top_frac=top_frac, rows_per_page=SEARCH_RESULT_ROWS
    )

    best: tuple[float, float, float] | None = None  # cy, cx, score
    for box, text, _score in _ocr_on_bgr(bgr, ocr):
        if _is_price_line(text):
            continue
        low = text.lower()
        if "on market" in low or "vendor" in low:
            continue
        cx, cy = _box_center(box)
        if not (top_skip <= cy < list_y1):
            continue
        text_n = _norm(text)
        text_c = _compact(text)
        match = 0
        if text_n == target:
            match = 100
        elif target_c and text_c and (
            text_c == target_c
            or _compact_prefix_match(text_c, target_c)
            or _compact_prefix_match(target_c, text_c)
        ):
            match = 90
        elif target and (target in text_n or text_n in target):
            match = 75
        if match < 70:
            continue
        if best is None or cy < best[0] or (cy == best[0] and match > best[2]):
            best = (cy, cx, float(match))

    if best is None:
        return None
    cy, cx, _m = best
    screen_x = window_left + max(int(window_width * 0.32), int(cx))
    screen_y = window_top + int(cy)
    return screen_x, screen_y


def row_click_screen_xy(
    *,
    crop_height: int,
    row: int,
    window_left: int,
    window_top: int,
    window_width: int,
    top_frac: float = TOP_FRAC,
    rows_per_page: int = ROWS_PER_PAGE,
    y_frac: float = 0.5,
) -> tuple[int, int]:
    """Screen coordinates to click a list row in the market window."""
    bands = _row_bands(crop_height, top_frac=top_frac, rows_per_page=rows_per_page)
    idx = max(0, min(row - 1, len(bands) - 1))
    y0, y1 = bands[idx]
    cy = window_top + y0 + int((y1 - y0) * y_frac)
    cx = window_left + window_width // 2
    return cx, cy


def _box_center(box: list[list[float]]) -> tuple[float, float]:
    cx = sum(p[0] for p in box) / len(box)
    cy = sum(p[1] for p in box) / len(box)
    return cx, cy


def _is_price_line(text: str) -> bool:
    t = text.lower()
    return "adena" in t or "price" in t or bool(re.search(r"1:\s*[\d,]", t))


def _assign_row_index(
    cy: float,
    text: str,
    bands: list[tuple[int, int]],
    *,
    search_rows: bool = False,
) -> int:
    """Price lines sit on the row divider; last-row prices may extend below the band."""
    last_i = len(bands) - 1
    last_y0, last_y1 = bands[last_i]

    if not search_rows and _is_price_line(text):
        if last_y0 <= cy <= last_y1 + LAST_ROW_PRICE_SLOP_PX:
            return last_i
        slop = PRICE_SLOP_PX
        candidates = [i for i, (y0, y1) in enumerate(bands) if y0 - 4 <= cy <= y1 + slop]
        if not candidates:
            candidates = list(range(len(bands)))
        return min(candidates, key=lambda i: abs(cy - bands[i][1]))

    for i, (y0, y1) in enumerate(bands):
        if y0 <= cy < y1:
            return i
    return min(
        range(len(bands)),
        key=lambda i: abs(cy - (bands[i][0] + bands[i][1]) / 2),
    )


def _upscale_bgr(bgr: np.ndarray, scale: int = 2) -> np.ndarray:
    if scale <= 1:
        return bgr
    pil = Image.fromarray(bgr[:, :, ::-1])
    pil = pil.resize((pil.width * scale, pil.height * scale), Image.Resampling.LANCZOS)
    return np.array(pil)[:, :, ::-1]


def _ocr_on_bgr(bgr: np.ndarray, ocr) -> list[tuple[list[list[float]], str, float]]:
    return run_ocr(ocr, bgr)


def _parse_from_grouped_items(
    row_items: list[list[tuple[float, float, str]]],
    *,
    bgr: np.ndarray,
    bands: list[tuple[int, int]],
    page: int,
    row_filter=is_plausible_market_row,
) -> list[MarketRow]:
    rows: list[MarketRow] = []
    for i, (items, (y0, y1)) in enumerate(zip(row_items, bands, strict=False), start=1):
        if not items:
            continue
        items.sort(key=lambda t: (t[0], t[1]))
        lines = [text for _cy, _cx, text in items]
        row_bgr = bgr[y0:y1, :]
        row_boxes = [(cx, cy - y0, text) for cy, cx, text in items]
        parsed = _build_row_from_lines(
            lines, page=page, row=i, row_bgr=row_bgr, boxes=row_boxes
        )
        if row_filter(parsed):
            rows.append(parsed)
    return rows


def _ocr_page_grouped(
    bgr: np.ndarray,
    ocr,
    *,
    top_frac: float = TOP_FRAC,
    rows_per_page: int = ROWS_PER_PAGE,
    search_rows: bool = False,
) -> list[list[tuple[float, float, str]]]:
    """One OCR pass on the full crop; assign list-zone detections to row bands by Y."""
    h = bgr.shape[0]
    bands = _row_bands(h, top_frac=top_frac, rows_per_page=rows_per_page)
    top_skip, list_y1 = _list_bounds(h, top_frac=top_frac, rows_per_page=rows_per_page)

    # OCR the full window — body-only crops sometimes drop noisy price lines (+0 glued to label).
    detections = _ocr_on_bgr(bgr, ocr)
    row_items: list[list[tuple[float, float, str]]] = [[] for _ in range(rows_per_page)]

    for box, text, _score in detections:
        cx, cy_full = _box_center(box)
        if not (top_skip <= cy_full < list_y1):
            continue
        idx = _assign_row_index(cy_full, text, bands, search_rows=search_rows)
        row_items[idx].append((cy_full, cx, text))
    return row_items


def _ocr_row_crops_grouped(
    bgr: np.ndarray,
    ocr,
    *,
    scale: int = 2,
    top_frac: float = TOP_FRAC,
    rows_per_page: int = ROWS_PER_PAGE,
) -> list[list[tuple[float, float, str]]]:
    """Fallback: OCR each row band separately (2x upscale)."""
    bands = _row_bands(bgr.shape[0], top_frac=top_frac, rows_per_page=rows_per_page)
    row_items: list[list[tuple[float, float, str]]] = [[] for _ in range(rows_per_page)]
    for idx, (y0, y1) in enumerate(bands):
        row_bgr = _upscale_bgr(bgr[y0:y1, :], scale=scale)
        for box, text, _score in _ocr_on_bgr(row_bgr, ocr):
            cx, cy = _box_center(box)
            row_items[idx].append((cy / scale + y0, cx / scale, text))
    return row_items


def _build_row_from_lines(
    lines: list[str],
    *,
    page: int,
    row: int,
    row_bgr: np.ndarray,
    boxes: list[tuple[float, float, str]] | None = None,
) -> MarketRow:
    fields = parse_fields_from_lines(
        lines,
        row_width=row_bgr.shape[1],
        boxes=boxes,
    )
    return MarketRow(
        page=page,
        row=row,
        item=fields["item"],
        vendor=fields["vendor"],
        price_adena=fields["price_adena"],
        units=fields["units"],
        item_icon_hash=icon_hash_from_row(row_bgr),
        raw_text=fields["raw_text"],
        price_confidence=fields["price_confidence"],
        enchant=fields.get("enchant"),
        item_base=fields.get("item_base"),
        item_display=fields.get("item_display"),
    )


def parse_page_rows(
    bgr: np.ndarray,
    *,
    page: int = 0,
    ocr=None,
    row_fallback: bool = True,
) -> list[MarketRow]:
    """Parse one market window crop into up to 7 rows (full-page OCR, row crop fallback)."""
    if ocr is None:
        ocr = get_ocr_engine()

    bands = _row_bands(bgr.shape[0])
    grouped = _ocr_page_grouped(bgr, ocr)
    rows = _parse_from_grouped_items(grouped, bgr=bgr, bands=bands, page=page)

    if row_fallback and len(rows) < 3:
        grouped_fb = _ocr_row_crops_grouped(bgr, ocr, scale=2)
        rows_fb = _parse_from_grouped_items(grouped_fb, bgr=bgr, bands=bands, page=page)
        if len(rows_fb) > len(rows):
            rows = rows_fb

    return rows


def ocr_shows_search_listings(bgr: np.ndarray, *, crop_y0: int, crop_y1: int, ocr=None) -> bool:
    """True when the search-results strip shows a market listing (not the category hub)."""
    if ocr is None:
        ocr = get_ocr_engine()
    y0 = max(0, crop_y0)
    y1 = min(bgr.shape[0], crop_y1)
    if y1 <= y0 + 8:
        return False
    crop = _upscale_bgr(bgr[y0:y1, :], scale=2)
    parts: list[str] = []
    for _box, text, _score in _ocr_on_bgr(crop, ocr):
        t = text.strip()
        if t:
            parts.append(t)
    if not parts:
        return False
    blob = " ".join(parts).lower()
    if "on market" in blob or "min. price" in blob:
        return True
    return "vendor" in blob and "adena" in blob


def _parse_search_results_crop(
    bgr: np.ndarray,
    *,
    crop_y0: int,
    crop_y1: int,
    page: int,
    ocr,
) -> list[MarketRow]:
    """OCR only the listing strip below the search box (avoids band mis-alignment)."""
    y0 = max(0, crop_y0)
    y1 = min(bgr.shape[0], crop_y1)
    if y1 <= y0 + 8:
        return []
    crop = bgr[y0:y1, :]
    crop_h = crop.shape[0]
    rows_in_crop = max(1, min(SEARCH_RESULT_ROWS, crop_h // 28))
    bands = _row_bands(crop_h, top_frac=0.0, rows_per_page=rows_in_crop)
    scaled = _upscale_bgr(crop, scale=2)
    row_items: list[list[tuple[float, float, str]]] = [[] for _ in range(rows_in_crop)]
    for box, text, _score in _ocr_on_bgr(scaled, ocr):
        cx, cy = _box_center(box)
        cy_crop = cy / 2
        if not (0 <= cy_crop < crop_h):
            continue
        idx = _assign_row_index(cy_crop, text, bands, search_rows=True)
        row_items[idx].append((cy_crop + y0, cx, text))

    rows = _parse_from_grouped_items(
        row_items,
        bgr=bgr,
        bands=[(y0 + a, y0 + b) for a, b in bands],
        page=page,
        row_filter=is_plausible_search_result_row,
    )
    return [_coerce_search_result_item(r) for r in rows]


def parse_search_result_rows(
    bgr: np.ndarray,
    *,
    page: int = 0,
    ocr=None,
    top_frac: float = TOP_FRAC,
    crop_y0: int | None = None,
    crop_y1: int | None = None,
) -> list[MarketRow]:
    """Parse search-results screen (item names only, no vendor prices)."""
    if ocr is None:
        ocr = get_ocr_engine()

    if crop_y0 is not None and crop_y1 is not None:
        crop_rows = _parse_search_results_crop(
            bgr,
            crop_y0=crop_y0,
            crop_y1=crop_y1,
            page=page,
            ocr=ocr,
        )
        if crop_rows:
            return crop_rows

    bands = _row_bands(bgr.shape[0], top_frac=top_frac, rows_per_page=SEARCH_RESULT_ROWS)
    grouped = _ocr_page_grouped(
        bgr,
        ocr,
        top_frac=top_frac,
        rows_per_page=SEARCH_RESULT_ROWS,
        search_rows=True,
    )
    rows = _parse_from_grouped_items(
        grouped,
        bgr=bgr,
        bands=bands,
        page=page,
        row_filter=is_plausible_search_result_row,
    )

    if len(rows) < 2:
        grouped_fb = _ocr_row_crops_grouped(
            bgr,
            ocr,
            scale=2,
            top_frac=top_frac,
            rows_per_page=SEARCH_RESULT_ROWS,
        )
        rows_fb = _parse_from_grouped_items(
            grouped_fb,
            bgr=bgr,
            bands=bands,
            page=page,
            row_filter=is_plausible_search_result_row,
        )
        if len(rows_fb) > len(rows):
            rows = rows_fb

    return [_coerce_search_result_item(r) for r in rows]

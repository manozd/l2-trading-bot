"""Read Buy Item pagination indicator (e.g. ``47 / 47``) from the market window crop."""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from PIL import Image

from market.ocr_engine import run_ocr

# Bottom-center strip where "current / total" appears (above the Back button).
PAGINATION_Y0_FRAC = 0.82
PAGINATION_Y1_FRAC = 0.93
PAGINATION_X0_FRAC = 0.22
PAGINATION_X1_FRAC = 0.78


@dataclass(frozen=True)
class PageIndicator:
    current: int
    total: int
    raw_text: str

    @property
    def is_last(self) -> bool:
        return self.current >= self.total


def _pagination_strip(bgr: np.ndarray) -> np.ndarray:
    h, w = bgr.shape[:2]
    y0 = int(h * PAGINATION_Y0_FRAC)
    y1 = max(y0 + 4, int(h * PAGINATION_Y1_FRAC))
    x0 = int(w * PAGINATION_X0_FRAC)
    x1 = max(x0 + 8, int(w * PAGINATION_X1_FRAC))
    strip = bgr[y0:y1, x0:x1]
    pil = Image.fromarray(strip).convert("L")
    pil = pil.resize((max(1, pil.width * 3), max(1, pil.height * 3)), Image.Resampling.LANCZOS)
    return np.array(pil)


def parse_page_indicator_text(text: str) -> PageIndicator | None:
    t = re.sub(r"\s+", " ", text.strip())
    m = re.search(r"(\d{1,4})\s*/\s*(\d{1,4})", t)
    if not m:
        return None
    current, total = int(m.group(1)), int(m.group(2))
    if current < 1 or total < 1 or current > total:
        return None
    return PageIndicator(current=current, total=total, raw_text=t)


def read_page_indicator(bgr: np.ndarray, ocr) -> PageIndicator | None:
    """OCR the pagination strip; return ``current / total`` or None."""
    strip = _pagination_strip(bgr)
    detections = run_ocr(ocr, strip)
    if not detections:
        return None

    texts = [text for _box, text, _score in detections]
    joined = " ".join(texts)
    hit = parse_page_indicator_text(joined)
    if hit is not None:
        return hit

    for _box, text, _score in detections:
        hit = parse_page_indicator_text(text)
        if hit is not None:
            return hit
    return None


def is_plausible_list_indicator(indicator: PageIndicator | None) -> bool:
    """Reject common OCR garbage such as ``999/999``."""
    if indicator is None:
        return False
    if indicator.total >= 500:
        return False
    return 1 <= indicator.current <= indicator.total


def read_page_indicator_robust(bgr: np.ndarray, ocr) -> PageIndicator | None:
    """Read pagination; retry once on the raw bottom strip if the cropped read fails."""
    hit = read_page_indicator(bgr, ocr)
    if is_plausible_list_indicator(hit):
        return hit
    h, w = bgr.shape[:2]
    y0 = int(h * PAGINATION_Y0_FRAC)
    y1 = max(y0 + 4, int(h * PAGINATION_Y1_FRAC))
    wide = bgr[y0:y1, int(w * 0.12) : int(w * 0.88)]
    if wide.size == 0:
        return hit if is_plausible_list_indicator(hit) else None
    pil = Image.fromarray(wide).convert("L")
    pil = pil.resize((max(1, pil.width * 3), max(1, pil.height * 3)), Image.Resampling.LANCZOS)
    detections = run_ocr(ocr, np.array(pil))
    texts = [text for _box, text, _score in detections]
    for blob in (" ".join(texts), *texts):
        retry = parse_page_indicator_text(blob)
        if is_plausible_list_indicator(retry):
            return retry
    return hit if is_plausible_list_indicator(hit) else None


class ListPageTracker:
    """Track list page by click counter; OCR is logged only, never used to stop."""

    def __init__(self) -> None:
        self.page: int = 1
        self.total_hint: int | None = None
        self._initialized = False

    def resolve(self, indicator: PageIndicator | None, *, loop_i: int) -> int:
        if is_plausible_list_indicator(indicator):
            assert indicator is not None
            if not self._initialized:
                self.page = indicator.current
                self._initialized = True
            elif abs(indicator.current - self.page) <= 2:
                self.page = indicator.current
            if self.total_hint is None:
                self.total_hint = indicator.total
            elif abs(indicator.total - self.total_hint) <= 3:
                self.total_hint = indicator.total
            return self.page

        if not self._initialized:
            self.page = loop_i
            self._initialized = True
        return self.page

    def after_next_click(self) -> None:
        self.page += 1
        self._initialized = True

    def ocr_log_suffix(self, indicator: PageIndicator | None) -> str:
        if indicator is None:
            hint = f"/{self.total_hint}" if self.total_hint else ""
            return f"tracked {self.page}{hint} (pagination OCR failed)"
        if self.total_hint and abs(indicator.total - self.total_hint) > 3:
            return (
                f"OCR {indicator.current}/{indicator.total} "
                f"(tracked {self.page}, total hint {self.total_hint})"
            )
        return f"OCR {indicator.current}/{indicator.total} (tracked {self.page})"

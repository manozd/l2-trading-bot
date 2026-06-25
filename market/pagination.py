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

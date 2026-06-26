"""Page / row fingerprints for pagination stop without OCR."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

from market.full_list_parser import (
    LIST_BOTTOM_FRAC,
    ROWS_PER_PAGE,
    TOP_FRAC,
    _row_bands,
    icon_hash_for_list_row,
)


@dataclass(frozen=True)
class PageFingerprint:
    page_hash: str
    row_hashes: tuple[str, ...]
    icon_hashes: tuple[str, ...]


def list_body_bgr(bgr: np.ndarray) -> np.ndarray:
    """Crop to the item list strip — excludes pagination and Next/Back buttons."""
    h = bgr.shape[0]
    y0 = int(h * TOP_FRAC)
    y1 = min(int(h * LIST_BOTTOM_FRAC), h - 4)
    return bgr[y0:y1, :]


def list_icon_fingerprint(bgr) -> tuple[str, ...]:
    """Per-row item icon hashes on the full window crop."""
    return tuple(icon_hash_for_list_row(bgr, row) for row in range(1, ROWS_PER_PAGE + 1))


def list_icon_fingerprint_body(bgr) -> tuple[str, ...]:
    """Icon hashes from the list body only (cursor-safe when parked on Next/Back below)."""
    body = list_body_bgr(bgr)
    return tuple(
        icon_hash_for_list_row(body, row, top_frac=0.0) for row in range(1, ROWS_PER_PAGE + 1)
    )


def fingerprint_page(bgr) -> PageFingerprint:
    h = bgr.shape[0]
    bands = _row_bands(h)
    row_hashes: list[str] = []
    for y0, y1 in bands:
        row_hashes.append(hashlib.md5(bgr[y0:y1, :].tobytes()).hexdigest())
    while len(row_hashes) < ROWS_PER_PAGE:
        row_hashes.append("")
    icon_hashes = list_icon_fingerprint(bgr)
    return PageFingerprint(
        page_hash=hashlib.md5(bgr.tobytes()).hexdigest(),
        row_hashes=tuple(row_hashes[:ROWS_PER_PAGE]),
        icon_hashes=icon_hashes,
    )


def fingerprint_list_body(bgr) -> PageFingerprint:
    """
    Fingerprint only rows above the pagination strip.

    Use with the cursor parked on Next/Back so hover highlights never touch list rows.
    """
    body = list_body_bgr(bgr)
    h = body.shape[0]
    bands = _row_bands(h, top_frac=0.0, rows_per_page=ROWS_PER_PAGE)
    row_hashes: list[str] = []
    for y0, y1 in bands:
        row_hashes.append(hashlib.md5(body[y0:y1, :].tobytes()).hexdigest())
    while len(row_hashes) < ROWS_PER_PAGE:
        row_hashes.append("")
    icon_hashes = list_icon_fingerprint_body(bgr)
    return PageFingerprint(
        page_hash=hashlib.md5(body.tobytes()).hexdigest(),
        row_hashes=tuple(row_hashes[:ROWS_PER_PAGE]),
        icon_hashes=icon_hashes,
    )


def page_unchanged(prev: PageFingerprint | None, cur: PageFingerprint) -> bool:
    if prev is None:
        return False
    # Icon hashes ignore Next-button hover, pagination flash, and text anti-aliasing noise.
    if prev.icon_hashes == cur.icon_hashes:
        return True
    if prev.page_hash == cur.page_hash:
        return True
    return prev.row_hashes == cur.row_hashes

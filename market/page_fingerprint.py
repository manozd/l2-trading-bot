"""Page / row fingerprints for pagination stop without OCR."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from market.full_list_parser import ROWS_PER_PAGE, _row_bands


@dataclass(frozen=True)
class PageFingerprint:
    page_hash: str
    row_hashes: tuple[str, ...]


def fingerprint_page(bgr) -> PageFingerprint:
    h = bgr.shape[0]
    bands = _row_bands(h)
    row_hashes: list[str] = []
    for y0, y1 in bands:
        row_hashes.append(hashlib.md5(bgr[y0:y1, :].tobytes()).hexdigest())
    while len(row_hashes) < ROWS_PER_PAGE:
        row_hashes.append("")
    return PageFingerprint(
        page_hash=hashlib.md5(bgr.tobytes()).hexdigest(),
        row_hashes=tuple(row_hashes[:ROWS_PER_PAGE]),
    )


def page_unchanged(prev: PageFingerprint | None, cur: PageFingerprint) -> bool:
    if prev is None:
        return False
    if prev.page_hash == cur.page_hash:
        return True
    return prev.row_hashes == cur.row_hashes

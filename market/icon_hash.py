"""Per-row item icon fingerprint (distinguishes truncated names like Lucky Enchant Stone: Ar...)."""

from __future__ import annotations

import numpy as np
from PIL import Image

# Fractions of row width/height for the left icon square (BOHPTS Buy Item list).
ICON_X0_FRAC = 0.01
ICON_X1_FRAC = 0.10
ICON_Y0_FRAC = 0.07
ICON_Y1_FRAC = 0.93

# Fuzzy match thresholds (256-bit dHash space when hash expands beyond 128 bits).
FUZZY_EXACT_MAX = 10
FUZZY_STRONG_MAX = 35
FUZZY_NAME_ACCEPT_MAX = 60


def icon_hash_from_row(row_bgr: np.ndarray) -> str:
    """Icon dHash + coarse color tag (reduces false matches between similar slots)."""
    icon = _icon_crop(row_bgr)
    gray = np.array(Image.fromarray(icon).convert("L").resize((17, 16), Image.Resampling.LANCZOS))
    bits = gray[:, 1:] > gray[:, :-1]
    dhash = sum((1 << i) for i, b in enumerate(bits.flatten()) if b)
    mean = icon.reshape(-1, 3).mean(axis=0)
    color = "".join(f"{int(c // 32):x}" for c in mean[:3])
    return f"{dhash:032x}:{color}"


def split_icon_hash(icon_hash: str | None) -> tuple[str, str]:
    """Return ``(dhash_hex, color_tag)``; empty strings when missing."""
    if not icon_hash:
        return "", ""
    dhash, _, color = icon_hash.partition(":")
    return dhash.lower(), color.lower()


def color_tag_match(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return split_icon_hash(a)[1] == split_icon_hash(b)[1]


def dhash_hamming(a: str | None, b: str | None) -> int | None:
    """Hamming distance between dHash hex strings (aligned to common width)."""
    da, _ = split_icon_hash(a if ":" in (a or "") else f"{a}:")
    db, _ = split_icon_hash(b if ":" in (b or "") else f"{b}:")
    if not da or not db:
        return None
    width = max(len(da), len(db))
    da = da.zfill(width)
    db = db.zfill(width)
    return hamming_hex(da, db)


def _icon_crop(row_bgr: np.ndarray) -> np.ndarray:
    rh, rw = row_bgr.shape[:2]
    x0 = max(0, int(rw * ICON_X0_FRAC))
    x1 = max(x0 + 8, int(rw * ICON_X1_FRAC))
    y0 = max(0, int(rh * ICON_Y0_FRAC))
    y1 = max(y0 + 8, int(rh * ICON_Y1_FRAC))
    return row_bgr[y0:y1, x0:x1]


def row_icon_slot_occupied(row_bgr: np.ndarray) -> bool:
    """True when the left icon slot looks like a real item (not an empty list row)."""
    icon = _icon_crop(row_bgr)
    if icon.size == 0:
        return False
    gray = np.array(Image.fromarray(icon).convert("L"))
    std = float(gray.std())
    mean = float(gray.mean())
    if std < 9.0:
        return False
    if mean < 16.0 and std < 14.0:
        return False
    return True


def hamming_hex(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()

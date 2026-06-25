"""Per-row item icon fingerprint (distinguishes truncated names like Lucky Enchant Stone: Ar...)."""

from __future__ import annotations

import numpy as np
from PIL import Image

# Fractions of row width/height for the left icon square (BOHPTS Buy Item list).
ICON_X0_FRAC = 0.01
ICON_X1_FRAC = 0.10
ICON_Y0_FRAC = 0.07
ICON_Y1_FRAC = 0.93


def icon_hash_from_row(row_bgr: np.ndarray) -> str:
    """Icon dHash + coarse color tag (reduces false matches between similar slots)."""
    rh, rw = row_bgr.shape[:2]
    x0 = max(0, int(rw * ICON_X0_FRAC))
    x1 = max(x0 + 8, int(rw * ICON_X1_FRAC))
    y0 = max(0, int(rh * ICON_Y0_FRAC))
    y1 = max(y0 + 8, int(rh * ICON_Y1_FRAC))
    icon = row_bgr[y0:y1, x0:x1]
    gray = np.array(Image.fromarray(icon).convert("L").resize((17, 16), Image.Resampling.LANCZOS))
    bits = gray[:, 1:] > gray[:, :-1]
    dhash = sum((1 << i) for i, b in enumerate(bits.flatten()) if b)
    mean = icon.reshape(-1, 3).mean(axis=0)
    color = "".join(f"{int(c // 32):x}" for c in mean[:3])
    return f"{dhash:032x}:{color}"


def hamming_hex(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()

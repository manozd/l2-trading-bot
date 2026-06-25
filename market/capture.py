"""Screen capture for market ROI regions."""

from __future__ import annotations

from dataclasses import dataclass

import mss
import numpy as np


@dataclass(frozen=True)
class CropFrame:
    bgr: np.ndarray
    left: int
    top: int

    def save_png(self, path: str) -> None:
        from PIL import Image

        rgb = self.bgr[:, :, ::-1]
        Image.fromarray(rgb).save(path)


def grab_screen_rect(left: int, top: int, width: int, height: int) -> CropFrame:
    if width <= 0 or height <= 0:
        raise ValueError(f"grab_screen_rect: invalid size {width}x{height}")
    with mss.mss() as sct:
        raw = np.array(
            sct.grab({"left": int(left), "top": int(top), "width": int(width), "height": int(height)}),
            dtype=np.uint8,
        )
    return CropFrame(bgr=raw[:, :, :3].copy(), left=int(left), top=int(top))

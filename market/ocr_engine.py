"""Shared RapidOCR instance for market page parsing."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from rapidocr_onnxruntime import RapidOCR


@lru_cache(maxsize=1)
def get_ocr_engine() -> RapidOCR:
    return RapidOCR()


def run_ocr(engine: RapidOCR, bgr) -> list[tuple[list[list[float]], str, str]]:
    result, _ = engine(bgr)
    if not result:
        return []
    out: list[tuple[list[list[float]], str, str]] = []
    for line in result:
        if len(line) < 2:
            continue
        box, text = line[0], str(line[1])
        score = str(line[2]) if len(line) > 2 else "0"
        out.append((box, text, score))
    return out

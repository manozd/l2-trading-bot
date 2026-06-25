"""Enchant level (+0, +4, …) extraction and item naming."""

from __future__ import annotations

import re

_ENCHANT_STANDALONE = re.compile(r"^\+(\d{1,2})$")
_ENCHANT_PREFIX = re.compile(r"^\+(\d{1,2})(?:\s+|$)")
_ENCHANT_SUFFIX = re.compile(r"\s+\+(\d{1,2})$")


def parse_enchant_token(text: str) -> int | None:
    """Parse +N from a single OCR fragment."""
    t = text.strip()
    for pat in (_ENCHANT_STANDALONE, _ENCHANT_PREFIX, _ENCHANT_SUFFIX):
        m = pat.search(t)
        if m:
            return int(m.group(1))
    return None


def peel_enchant_prefix(text: str) -> tuple[int | None, str]:
    """Split leading +N from a glued price line (``+0 Price per unit: …``)."""
    t = text.strip()
    m = _ENCHANT_PREFIX.match(t)
    if not m:
        return None, t
    rest = t[m.end() :].strip()
    return int(m.group(1)), rest


def split_item_base_and_enchant(name: str | None) -> tuple[str | None, int | None]:
    if not name:
        return None, None
    t = name.strip()
    m = _ENCHANT_SUFFIX.search(t)
    if m:
        base = t[: m.start()].strip(" .")
        return base or None, int(m.group(1))
    return t or None, None


def format_item_display(base: str, enchant: int | None) -> str:
    if enchant is not None:
        return f"{base} +{enchant}"
    return base


def collect_enchant_from_texts(texts: list[str]) -> int | None:
    """Return enchant if all hints agree; None if absent or conflicting."""
    found: list[int] = []
    for text in texts:
        peeled, rest = peel_enchant_prefix(text)
        if peeled is not None:
            found.append(peeled)
            text = rest
        token = parse_enchant_token(text)
        if token is not None:
            found.append(token)
        _base, suffix = split_item_base_and_enchant(text)
        if suffix is not None:
            found.append(suffix)
    if not found:
        return None
    if len(set(found)) == 1:
        return found[0]
    return found[0]


def validate_search_enchant(
    ocr_enchant: int | None,
    expected: int | None,
) -> tuple[bool, str | None]:
    """Return (ok, reject_reason). When expected is set, OCR must match exactly."""
    if expected is None:
        return True, None
    if ocr_enchant is None:
        return False, "enchant_unknown"
    if ocr_enchant != expected:
        return False, f"enchant_mismatch:expected+{expected}:got+{ocr_enchant}"
    return True, None

"""Parse OCR text lines into item / vendor / price / units fields."""

from __future__ import annotations

import re
from typing import Literal

from market.enchant import (
    collect_enchant_from_texts,
    format_item_display,
    peel_enchant_prefix,
    split_item_base_and_enchant,
)


PriceConfidence = Literal["high", "medium", "low", "none"]


def normalize_market_text(text: str) -> str:
    """Normalize common OCR glitches before field parsing."""
    t = text.replace("|", " ")
    # Label fixes before any digit-like character swaps.
    t = re.sub(r"(?i)vend0r", "Vendor", t)
    t = re.sub(r"(?i)0n\s*market", "On market", t)
    t = re.sub(r"(?i)0nmarket", "On market", t)
    t = re.sub(r"(?i)a\s*dena", "Adena", t)
    t = re.sub(r"(?i)a0ena", "Adena", t)
    t = re.sub(r"(?i)price\s*per", "price per", t)
    t = re.sub(r"(?i)min\.?\s*price\s*per", "Min. price per", t)
    t = re.sub(r"(?<=[\d,\s])[lI](?=[\d,\s])", "1", t)
    t = t.replace("，", ",")
    t = re.sub(r":(\S)", r": \1", t)
    t = re.sub(r"(\d)\s*units?", r"\1 units", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"(?i)on\s*market\s*:", "On market:", t)
    t = re.sub(r"(?i)onmarket\s*:", "On market:", t)
    t = re.sub(r"(?i)in\s*stock\s*:", "In stock:", t)
    t = re.sub(r"(?i)instock\s*:", "In stock:", t)
    t = re.sub(r"(?i)min\.?\s*price\s*per\s*1?\s*:", "Min. price per 1:", t)
    t = re.sub(r"(?i)price\s*per\s*unit\s*:", "Price per unit:", t)
    t = re.sub(r"(?i)\bVendor(?=[A-Za-z0-9])", "Vendor: ", t)
    t = re.sub(r"(?i)\bVendor\s+(?!:)([A-Za-z0-9])", r"Vendor: \1", t)
    return t


def _normalize_ocr_text(text: str) -> str:
    return normalize_market_text(text)


def _fix_ocr_price_chars(text: str) -> str:
    return re.sub(r"[oO]", "0", text)


def _parse_int(s: str) -> int:
    return int(re.sub(r"[^\d]", "", s))


def parse_price_adena_with_confidence(text: str) -> tuple[int | None, PriceConfidence]:
    """Extract Adena price and confidence from one or more OCR fragments."""
    t = _normalize_ocr_text(_fix_ocr_price_chars(text))

    high_patterns = (
        r"Min\. price per 1:\s*([\d,\s]+)\s*A[0oO]?dena",
        r"Price per unit:\s*([\d,\s]+)\s*A[0oO]?dena",
    )
    for pat in high_patterns:
        m = re.search(pat, t, re.I)
        if m:
            try:
                return _parse_int(m.group(1)), "high"
            except ValueError:
                continue

    medium_patterns = (
        r"1:\s*([\d,\s]+)\s*A[0oO]?dena",
        r"([\d,\s]{4,})\s*A[0oO]?dena",
    )
    for pat in medium_patterns:
        m = re.search(pat, t, re.I)
        if m:
            try:
                return _parse_int(m.group(1)), "medium"
            except ValueError:
                continue

    m = re.search(r"([\d,\.\s]{4,})\s*A[0oO]ena", t, re.I)
    if m:
        digits = re.sub(r"[^\d]", "", m.group(1))
        if len(digits) >= 4:
            return int(digits), "low"

    for s in re.findall(r"\b([\d]{1,3}(?:,\d{3})+)\b", t):
        v = _parse_int(s)
        if v >= 1000:
            return v, "low"
    return None, "none"


def parse_price_adena(text: str) -> int | None:
    price, _conf = parse_price_adena_with_confidence(text)
    return price


def _is_price_line(text: str) -> bool:
    t = text.lower()
    return "adena" in t or "price" in t or bool(re.search(r"1:\s*[\d,]", t))


def _is_vendor_line(text: str) -> bool:
    return bool(re.search(r"(?i)\b(?:Vendor|Vend0r)\s*:?", text))


def _is_units_line(text: str) -> bool:
    return bool(re.search(r"(?i)(?:on\s*market|0n\s*market|in\s*stock)\s*:", text))


_ENCHANT_TRAIL = re.compile(r"(?i)\+\s*(\d{1,2})(?:\s*$|\s+(?:Min\.|Price))")


def _is_standalone_enchant_line(text: str) -> bool:
    peeled, rest = peel_enchant_prefix(text.strip())
    return peeled is not None and not rest.strip()


def _split_glued_vendor_price(text: str) -> list[str]:
    """One OCR box may contain vendor + price without space (``+0Min. price per 1:``)."""
    t = _normalize_ocr_text(text)
    peeled, rest = peel_enchant_prefix(t)
    if peeled is not None and rest:
        t = rest
    m = re.search(r"(?i)^(.+?)(Min\. price per 1:.*)$", t)
    if m:
        out = [m.group(1).strip(), m.group(2).strip()]
        return ([f"+{peeled}"] + out) if peeled is not None else out
    m = re.search(r"(?i)^(.+?)(Price per unit:.*)$", t)
    if m:
        out = [m.group(1).strip(), m.group(2).strip()]
        return ([f"+{peeled}"] + out) if peeled is not None else out
    m = re.search(r"(?i)^(.+?)(Min\.?\s*price.*)$", t)
    if m:
        out = [m.group(1).strip(), m.group(2).strip()]
        return ([f"+{peeled}"] + out) if peeled is not None else out
    if peeled is not None:
        return [f"+{peeled}", t] if t else [f"+{peeled}"]
    return [t]


def _expand_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for ln in lines:
        out.extend(_split_glued_vendor_price(ln))
    return [_normalize_ocr_text(x) for x in out if x.strip()]


def _extract_units(text: str) -> int | None:
    t = _normalize_ocr_text(text)
    for pat in (
        r"On market:\s*([\d,\s]+)\s*units?",
        r"In stock:\s*([\d,\s]+)\s*units?",
        r"On\s*market\s*:\s*(\d+)",
        r"In\s*stock\s*:\s*(\d+)",
        r"(?i)on\s*market\s*:\s*(\d+)",
        r"(?i)market\s*:\s*(\d+)\s*units?",
    ):
        m = re.search(pat, t, re.I)
        if m:
            digits = re.sub(r"[^\d]", "", m.group(1))
            if digits:
                return int(digits)
    return None


def _extract_vendor_from_text(text: str) -> str | None:
    t = _normalize_ocr_text(text)
    for pat in (
        r"(?i)Vendor\s*:?\s*(.+?)(?:\s+Min\. price|\s+Price per unit|\s+On market|\s+In stock|$)",
        r"(?i)Vendor\s*:?\s*(.+?)(?:Min\. price|Price per unit|On market|In stock|$)",
        r"(?i)Vendor\s*:?\s*(.+)$",
    ):
        m = re.search(pat, t)
        if m:
            v = m.group(1).strip(" .")
            v = re.sub(r"(?i)Min\.?\s*price.*", "", v).strip(" .")
            v = re.sub(r"(?i)[\d,]+\s*Adena.*", "", v).strip(" .")
            if v:
                return v
    return None


def _clean_item_name(text: str) -> str | None:
    t = _normalize_ocr_text(text)
    t = re.sub(r"(?i)\s+Vendor\s*:?\s*.+$", "", t)
    for pat in (
        r"On market:\s*[\d,\s]+\s*units?",
        r"Vendor\s*:?\s*.+",
        r"Min\. price per 1:\s*[\d,\s]+\s*[Aa]dena",
        r"[\d,\s]+\s*[Aa]dena",
        r"\bMin\. price per\b",
        r"\b1:\s*[\d,]+",
    ):
        t = re.sub(pat, " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t or None


def sanitize_vendor_nickname(vendor: str) -> str:
    """
    L2 vendor nicknames are alphanumeric only.

    OCR often appends item enchant (``+0``, ``+5``) or junk (``$+5``) to the vendor line.
    """
    v = vendor.strip()
    v = re.sub(r"[\s$]*\+\d+.*$", "", v)
    v = re.sub(r"[^A-Za-z0-9]", "", v)
    return v


def _clean_vendor(vendor: str | None) -> str | None:
    if not vendor:
        return None
    v = _normalize_ocr_text(vendor)
    v = re.sub(r"(?i)Min\.?\s*price\s*per.*", "", v)
    v = re.sub(r"(?i)[\d,]+\s*A[0oO]?dena.*", "", v)
    v = re.sub(r"(?i)\bon\s*market\b.*", "", v)
    v = sanitize_vendor_nickname(v)
    return v or None


def _extract_item_from_merged(text: str) -> str | None:
    """Item name is the left/top fragment before On market / Vendor / Min. price."""
    t = _normalize_ocr_text(text)
    m = re.search(r"(?i)^(.+?)(?:\s+On market:|\s+In stock:|\s+Vendor\s*:?|\s+Min\. price|\s+Price per unit:)", t)
    if m:
        return _clean_item_name(m.group(1))
    return _clean_item_name(t)


def _best_price_from_texts(texts: list[str]) -> tuple[int | None, PriceConfidence]:
    best: tuple[int | None, PriceConfidence] = (None, "none")
    rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
    for text in texts:
        price, conf = parse_price_adena_with_confidence(text)
        if price is not None and rank[conf] >= rank[best[1]]:
            best = (price, conf)
    return best


def parse_fields_from_lines(
    lines: list[str],
    *,
    row_width: int | None = None,
    boxes: list[tuple[float, float, str]] | None = None,
) -> dict:
    """
    Return dict with keys item, vendor, price_adena, units, price_confidence, raw_text.

    When ``boxes`` are (cx, cy, text) in row coordinates, use column layout:
    left = item/vendor/price, right = units.
    """
    expanded = _expand_lines(lines)
    raw_text = _normalize_ocr_text(" | ".join(expanded))

    units: int | None = None
    price_adena: int | None = None
    price_confidence: PriceConfidence = "none"
    vendor: str | None = None
    item_parts: list[str] = []
    price_texts: list[str] = []

    if boxes and row_width:
        left_max = row_width * 0.58
        left: list[tuple[float, float, str]] = []
        right: list[tuple[float, float, str]] = []
        for cx, cy, text in boxes:
            ex = _expand_lines([text])
            for piece in ex:
                (right if cx >= left_max else left).append((cy, cx, piece))

        left.sort(key=lambda t: (t[0], t[1]))
        right.sort(key=lambda t: (t[0], t[1]))

        for _cy, _cx, text in right:
            u = _extract_units(text)
            if u is not None:
                units = u

        for _cy, _cx, text in left:
            if _is_price_line(text):
                price_texts.append(text)
                p, c = parse_price_adena_with_confidence(text)
                if p is not None:
                    price_adena = p
                    price_confidence = c
            elif _is_vendor_line(text):
                v = _extract_vendor_from_text(text)
                if v:
                    vendor = v
            elif _is_units_line(text):
                u = _extract_units(text)
                if u is not None:
                    units = u
            else:
                if not _is_standalone_enchant_line(text):
                    item_parts.append(text)

        if vendor is None:
            for _cy, _cx, text in left:
                v = _extract_vendor_from_text(text)
                if v:
                    vendor = v
                    break

        if price_adena is None:
            price_adena, price_confidence = _best_price_from_texts(price_texts + [raw_text])

        item = _clean_item_name(" ".join(item_parts)) if item_parts else None
        if not item:
            merged = " ".join(expanded)
            item = _extract_item_from_merged(merged)
    else:
        for text in expanded:
            if _is_units_line(text):
                u = _extract_units(text)
                if u is not None:
                    units = u
            elif _is_price_line(text):
                price_texts.append(text)
                p, c = parse_price_adena_with_confidence(text)
                if p is not None:
                    price_adena = p
                    price_confidence = c
            elif _is_vendor_line(text):
                v = _extract_vendor_from_text(text)
                if v:
                    vendor = v
            else:
                if not _is_standalone_enchant_line(text):
                    item_parts.append(text)

        if price_adena is None:
            price_adena, price_confidence = _best_price_from_texts(price_texts + [raw_text])
        if vendor is None:
            vendor = _extract_vendor_from_text(raw_text)
        item = _clean_item_name(" ".join(item_parts)) if item_parts else None

    if item:
        item = _clean_item_name(item)
    vendor = _clean_vendor(vendor)

    if item and vendor is None:
        m = re.search(r"(?i)\s+Vendor\s*:?\s*(.+)$", item)
        if m:
            vendor = _clean_vendor(m.group(1))
            item = _clean_item_name(re.sub(r"(?i)\s+Vendor\s*:?\s*.+$", "", item))

    if not item and item_parts:
        item = _clean_item_name(" ".join(item_parts))

    if not item:
        item = _extract_item_from_merged(raw_text.replace(" | ", " "))

    item_base, suffix_enchant = split_item_base_and_enchant(item)
    enchant = collect_enchant_from_texts(expanded)
    for text in expanded:
        m = _ENCHANT_TRAIL.search(text)
        if m:
            if enchant is None:
                enchant = int(m.group(1))
            break
    if enchant is None:
        enchant = suffix_enchant
    elif suffix_enchant is not None and suffix_enchant != enchant:
        enchant = suffix_enchant
    if item_base:
        item = format_item_display(item_base, enchant)
    elif item and enchant is not None:
        item_base, _ = split_item_base_and_enchant(item)
        if item_base:
            item = format_item_display(item_base, enchant)
    item_base, _ = split_item_base_and_enchant(item)

    return {
        "item": item,
        "item_base": item_base or item,
        "item_display": item,
        "enchant": enchant,
        "vendor": vendor,
        "price_adena": price_adena,
        "units": units,
        "price_confidence": price_confidence,
        "raw_text": raw_text,
    }

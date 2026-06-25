"""Human-readable search collection logs for validation."""

from __future__ import annotations

from pathlib import Path


def format_adena(price: int | None) -> str:
    if price is None:
        return "-"
    return f"{price:,}"


def format_search_result_line(summary: dict) -> str:
    name = summary.get("item_ocr") or summary.get("item") or summary.get("item_full_name") or "?"
    query = summary.get("search_query") or summary.get("item_full_name") or "?"
    price = format_adena(summary.get("price_adena") if "price_adena" in summary else summary.get("min_price_adena"))
    vendor = summary.get("vendor") or (summary.get("vendors") or [None])[0] or "-"
    units = summary.get("units")
    units_s = str(units) if units is not None else "-"
    reject = summary.get("reject_reason")
    enc_bits = []
    if summary.get("expected_enchant") is not None:
        exp = summary["expected_enchant"]
        got = summary.get("ocr_enchant")
        enc_bits.append(f"enchant +{exp}" + (f" (OCR +{got})" if got is not None else " (OCR ?)"))
    enc_s = f"  |  {' '.join(enc_bits)}" if enc_bits else ""
    reject_s = f"  |  REJECT: {reject}" if reject else ""
    if query != name and name != "?":
        return (
            f"{query}  →  {name}  |  {price} adena  |  {vendor}  |  {units_s} units"
            f"{enc_s}{reject_s}"
        )
    return f"{name}  |  {price} adena  |  {vendor}  |  {units_s} units{enc_s}{reject_s}"


VALIDATE_CSV_HEADER = (
    "search_query,item_ocr,price_adena,vendor,units,item_key,"
    "expected_enchant,ocr_enchant,enchant_match,reject_reason"
)


def append_validate_csv(path: Path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.is_file() or path.stat().st_size == 0
    query = (summary.get("search_query") or "").replace('"', '""')
    item = (summary.get("item_ocr") or summary.get("item") or "").replace('"', '""')
    vendor = (summary.get("vendor") or "").replace('"', '""')
    price = summary.get("price_adena") if summary.get("price_adena") is not None else summary.get("min_price_adena")
    units = summary.get("units")
    key = (summary.get("item_key") or "").replace('"', '""')
    exp = summary.get("expected_enchant")
    ocr = summary.get("ocr_enchant")
    match = summary.get("enchant_match")
    reject = (summary.get("reject_reason") or "").replace('"', '""')
    match_s = "" if match is None else ("yes" if match else "no")
    with path.open("a", encoding="utf-8", newline="") as fh:
        if write_header:
            fh.write(VALIDATE_CSV_HEADER + "\n")
        fh.write(
            f'"{query}","{item}",{price if price is not None else ""},"{vendor}",'
            f'{units if units is not None else ""},"{key}",'
            f'{exp if exp is not None else ""},{ocr if ocr is not None else ""},'
            f'{match_s},"{reject}"\n'
        )


def append_validate_log(path: Path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(format_search_result_line(summary) + "\n")

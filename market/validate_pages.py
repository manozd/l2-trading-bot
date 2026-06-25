"""Offline validation of saved market page PNG crops."""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

from market.full_list_parser import ROWS_PER_PAGE, parse_page_rows
from market.ocr_engine import get_ocr_engine
from market.pagination import read_page_indicator


def page_num_from_path(path: Path) -> int:
    return int(path.stem.split("_")[1])


def load_png_bgr(path: Path) -> np.ndarray:
    rgb = np.array(Image.open(path).convert("RGB"))
    return rgb[:, :, ::-1].copy()


@dataclass
class PageStats:
    page: int
    file: str
    rows: int
    priced: int
    with_vendor: int
    with_units: int
    with_item: int
    indicator: str
    avg_row_confidence: int = 0

    @property
    def ok(self) -> bool:
        return self.rows >= 5 and self.priced >= 5


@dataclass
class ValidateReport:
    pages_total: int
    pages_ok: int
    pages_empty: int
    pages_partial: int
    rows_total: int
    rows_priced: int
    rows_with_vendor: int
    rows_with_units: int
    elapsed_s: float
    page_stats: list[PageStats]

    def to_summary_dict(self) -> dict:
        return {
            "pages_total": self.pages_total,
            "pages_ok": self.pages_ok,
            "pages_empty": self.pages_empty,
            "pages_partial": self.pages_partial,
            "rows_total": self.rows_total,
            "rows_priced": self.rows_priced,
            "rows_with_vendor": self.rows_with_vendor,
            "rows_with_units": self.rows_with_units,
            "priced_pct": round(100 * self.rows_priced / max(1, self.rows_total), 1),
            "vendor_pct": round(100 * self.rows_with_vendor / max(1, self.rows_total), 1),
            "units_pct": round(100 * self.rows_with_units / max(1, self.rows_total), 1),
            "elapsed_s": round(self.elapsed_s, 1),
        }


def validate_page_pngs(
    *,
    in_dir: Path,
    glob_pattern: str = "page_*.png",
    start_page: int = 1,
    end_page: int = 0,
    out_jsonl: Path | None = None,
    out_csv: Path | None = None,
    out_summary: Path | None = None,
) -> ValidateReport:
    paths = sorted(in_dir.glob(glob_pattern), key=page_num_from_path)
    paths = [p for p in paths if page_num_from_path(p) >= start_page]
    if end_page > 0:
        paths = [p for p in paths if page_num_from_path(p) <= end_page]
    if not paths:
        raise SystemExit(f"No PNG files in {in_dir} for the requested page range.")

    if out_jsonl and out_jsonl.exists():
        out_jsonl.unlink()
    if out_jsonl:
        out_jsonl.parent.mkdir(parents=True, exist_ok=True)
        out_jsonl.write_text("", encoding="utf-8")

    ocr = get_ocr_engine()
    scanned_at = datetime.now(timezone.utc).isoformat()
    page_stats: list[PageStats] = []
    t0 = time.perf_counter()

    for i, path in enumerate(paths, start=1):
        page = page_num_from_path(path)
        bgr = load_png_bgr(path)
        indicator = read_page_indicator(bgr, ocr)
        rows = parse_page_rows(bgr, page=page, ocr=ocr)

        priced = sum(1 for r in rows if r.price_adena is not None)
        with_vendor = sum(1 for r in rows if r.vendor)
        with_units = sum(1 for r in rows if r.units is not None)
        with_item = sum(1 for r in rows if r.item)

        ind_str = f"{indicator.current}/{indicator.total}" if indicator else "—"
        ps = PageStats(
            page=page,
            file=path.name,
            rows=len(rows),
            priced=priced,
            with_vendor=with_vendor,
            with_units=with_units,
            with_item=with_item,
            indicator=ind_str,
        )
        page_stats.append(ps)

        if out_jsonl:
            with out_jsonl.open("a", encoding="utf-8") as fh:
                for row in rows:
                    record = row.to_dict()
                    record["scanned_at"] = scanned_at
                    record["source_file"] = path.name
                    if indicator:
                        record["page_total"] = indicator.total
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")

        status = "OK" if ps.ok else ("EMPTY" if ps.rows == 0 else "PARTIAL")
        print(
            f"[validate] {path.name} → {ps.rows} rows, {ps.priced} priced, "
            f"{ps.with_vendor} vendors, page {ind_str} [{status}] [{i}/{len(paths)}]",
            flush=True,
        )

    elapsed = time.perf_counter() - t0
    pages_ok = sum(1 for p in page_stats if p.ok)
    pages_empty = sum(1 for p in page_stats if p.rows == 0)
    pages_partial = len(page_stats) - pages_ok - pages_empty
    rows_total = sum(p.rows for p in page_stats)
    rows_priced = sum(p.priced for p in page_stats)
    rows_with_vendor = sum(p.with_vendor for p in page_stats)
    rows_with_units = sum(p.with_units for p in page_stats)

    report = ValidateReport(
        pages_total=len(page_stats),
        pages_ok=pages_ok,
        pages_empty=pages_empty,
        pages_partial=pages_partial,
        rows_total=rows_total,
        rows_priced=rows_priced,
        rows_with_vendor=rows_with_vendor,
        rows_with_units=rows_with_units,
        elapsed_s=elapsed,
        page_stats=page_stats,
    )

    if out_csv:
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with out_csv.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(
                fh,
                fieldnames=[
                    "page",
                    "file",
                    "rows",
                    "priced",
                    "with_vendor",
                    "with_units",
                    "with_item",
                    "indicator",
                    "status",
                ],
            )
            w.writeheader()
            for p in page_stats:
                status = "ok" if p.ok else ("empty" if p.rows == 0 else "partial")
                w.writerow(
                    {
                        "page": p.page,
                        "file": p.file,
                        "rows": p.rows,
                        "priced": p.priced,
                        "with_vendor": p.with_vendor,
                        "with_units": p.with_units,
                        "with_item": p.with_item,
                        "indicator": p.indicator,
                        "status": status,
                    }
                )

    if out_summary:
        out_summary.parent.mkdir(parents=True, exist_ok=True)
        out_summary.write_text(
            json.dumps(report.to_summary_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    s = report.to_summary_dict()
    print(
        f"\n[validate] done — {s['pages_total']} pages in {s['elapsed_s']}s\n"
        f"  pages OK (≥5 rows+priced): {s['pages_ok']}\n"
        f"  pages partial: {report.pages_partial}\n"
        f"  pages empty: {s['pages_empty']}\n"
        f"  rows: {s['rows_total']} total, {s['rows_priced']} priced ({s['priced_pct']}%)\n"
        f"  vendors: {s['rows_with_vendor']} ({s['vendor_pct']}%), "
        f"units: {s['rows_with_units']} ({s['units_pct']}%)\n"
        f"  expected ~{ROWS_PER_PAGE} rows/page",
        flush=True,
    )
    if out_jsonl:
        print(f"  JSONL: {out_jsonl}", flush=True)
    if out_csv:
        print(f"  CSV:   {out_csv}", flush=True)
    if out_summary:
        print(f"  summary: {out_summary}", flush=True)

    return report

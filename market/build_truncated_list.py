"""Build truncated-item registry from saved market page PNGs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from market.full_list_parser import parse_page_rows
from market.name_truncation import is_truncated_market_row
from market.ocr_engine import get_ocr_engine
from market.truncated_storage import (
    DEFAULT_TRUNCATED_ITEMS_PATH,
    DEFAULT_TRUNCATED_LISTINGS_PATH,
    TruncatedItemsStore,
    save_truncated_store,
    write_truncated_listings_jsonl,
)
from market.validate_pages import load_png_bgr, page_num_from_path


@dataclass
class BuildTruncatedReport:
    pages_total: int
    rows_total: int
    truncated_rows: int
    full_rows: int
    unique_truncated_keys: int
    ambiguous_truncated_keys: int = 0
    elapsed_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "pages_total": self.pages_total,
            "rows_total": self.rows_total,
            "truncated_rows": self.truncated_rows,
            "full_rows": self.full_rows,
            "unique_truncated_keys": self.unique_truncated_keys,
            "ambiguous_truncated_keys": self.ambiguous_truncated_keys,
            "elapsed_s": round(self.elapsed_s, 1),
        }


def build_truncated_list_from_pages(
    *,
    in_dir: Path,
    glob_pattern: str = "page_*.png",
    out_registry: Path = DEFAULT_TRUNCATED_ITEMS_PATH,
    out_listings: Path = DEFAULT_TRUNCATED_LISTINGS_PATH,
    start_page: int = 1,
    end_page: int = 0,
) -> BuildTruncatedReport:
    import time

    t0 = time.perf_counter()
    paths = sorted(in_dir.glob(glob_pattern))
    if start_page > 1:
        paths = [p for p in paths if page_num_from_path(p) >= start_page]
    if end_page > 0:
        paths = [p for p in paths if page_num_from_path(p) <= end_page]

    ocr = get_ocr_engine()
    store = TruncatedItemsStore(source="market_pages")
    truncated_listings: list[dict] = []
    rows_total = 0
    truncated_rows = 0
    scanned_at = datetime.now(timezone.utc).isoformat()

    for i, path in enumerate(paths, start=1):
        page = page_num_from_path(path)
        bgr = load_png_bgr(path)
        rows = parse_page_rows(bgr, page=page, ocr=ocr)
        rows_total += len(rows)

        for row in rows:
            record = row.to_dict()
            record["scanned_at"] = scanned_at
            record["source_file"] = path.name
            record["identity_status"] = "truncated"
            record["item_name_source"] = "ocr_truncated"
            record["name_source"] = "list_truncated"

            if is_truncated_market_row(record):
                truncated_rows += 1
                store.merge_listing_row(record, source="market_pages")
                truncated_listings.append(record)

        trunc_count = sum(1 for row in rows if is_truncated_market_row(row.to_dict()))
        print(
            f"[truncated] {path.name} → {len(rows)} rows ({trunc_count} truncated) "
            f"[{i}/{len(paths)}]",
            flush=True,
        )

    save_truncated_store(store, out_registry)
    write_truncated_listings_jsonl(truncated_listings, out_listings)

    summary = store.identity_summary()
    elapsed = time.perf_counter() - t0
    report = BuildTruncatedReport(
        pages_total=len(paths),
        rows_total=rows_total,
        truncated_rows=truncated_rows,
        full_rows=rows_total - truncated_rows,
        unique_truncated_keys=summary["unique"],
        ambiguous_truncated_keys=summary["ambiguous"],
        elapsed_s=elapsed,
    )
    print(
        f"[truncated] done — {summary['item_keys']} item keys "
        f"({summary['unique']} unique, {summary['ambiguous']} ambiguous) "
        f"from {report.truncated_rows}/{report.rows_total} rows ({report.pages_total} pages) "
        f"in {report.elapsed_s:.1f}s\n"
        f"  registry: {out_registry.resolve()}\n"
        f"  listings: {out_listings.resolve()}",
        flush=True,
    )
    return report

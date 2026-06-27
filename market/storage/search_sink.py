"""Write search scan results to JSONL, validate CSV/log, and summary CSV."""

from __future__ import annotations

import json
from pathlib import Path

from market.core.models import SearchResult
from market.min_prices import MinPriceEntry, write_min_prices_json
from market.search_log import append_validate_csv, append_validate_log, format_search_result_line


class SearchResultSink:
    def __init__(
        self,
        *,
        jsonl_path: Path,
        validate_csv: Path,
        validate_log: Path,
        min_json: Path,
        min_csv: Path,
    ) -> None:
        self.jsonl_path = jsonl_path
        self.validate_csv = validate_csv
        self.validate_log = validate_log
        self.min_json = min_json
        self.min_csv = min_csv
        self._results: list[SearchResult] = []

    def reset(self) -> None:
        self._results.clear()
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        for p in (self.jsonl_path, self.validate_csv, self.validate_log):
            if p.exists():
                p.unlink()

    def append(self, result: SearchResult) -> None:
        self._results.append(result)
        summary = result.to_summary_dict()
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with self.jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
            if result.raw_row:
                fh.write(json.dumps(result.raw_row, ensure_ascii=False) + "\n")
        append_validate_csv(self.validate_csv, summary)
        append_validate_log(self.validate_log, summary)

    def log_collected(self, result: SearchResult) -> None:
        summary = result.to_summary_dict()
        if result.reject_reason:
            print(
                f"[search] rejected: {result.item_name!r}  |  {result.reject_reason}",
                flush=True,
            )
        elif result.found:
            print(f"[search] collected: {format_search_result_line(summary)}", flush=True)
        else:
            print(f"[search] collected: {result.item_name!r}  |  NOT FOUND (no row 1)", flush=True)

    def finalize(self) -> None:
        entries: list[MinPriceEntry] = []
        for r in self._results:
            if r.price_adena is None:
                continue
            s = r.to_summary_dict()
            entries.append(
                MinPriceEntry(
                    item_key=r.item_key or r.item_id,
                    item=r.item_ocr or r.item_name,
                    item_full_name=r.item_name,
                    name_source=r.item_name_source,
                    min_price_adena=int(r.price_adena),
                    listing_count=1,
                    vendors=[r.vendor] if r.vendor else [],
                    sample_page=s.get("sample_page"),
                )
            )
        write_min_prices_json(self.min_json, entries)
        self._write_summary_csv(self.min_csv)

    def _write_summary_csv(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "item_id,search_query,item_name,item_ocr,price_adena,vendor,units,"
            "item_key,found,price_confidence,row_confidence,"
            "expected_enchant,ocr_enchant,enchant_match,reject_reason"
        )
        lines = [header]
        for r in self._results:
            match_s = "" if r.enchant_match is None else ("yes" if r.enchant_match else "no")
            lines.append(
                f"{r.item_id},{_csv(r.search_query)},{_csv(r.item_name)},{_csv(r.item_ocr or '')},"
                f"{r.price_adena if r.price_adena is not None else ''},"
                f"{_csv(r.vendor or '')},{r.units if r.units is not None else ''},"
                f"{_csv(r.item_key or '')},{'yes' if r.found else 'no'},"
                f"{r.price_confidence},{r.row_confidence},"
                f"{r.expected_enchant if r.expected_enchant is not None else ''},"
                f"{r.ocr_enchant if r.ocr_enchant is not None else ''},"
                f"{match_s},{_csv(r.reject_reason or '')}"
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @property
    def results(self) -> list[SearchResult]:
        return list(self._results)

    def print_done(self) -> None:
        priced = sum(1 for r in self._results if r.price_adena is not None)
        print(
            f"[search] done — {priced}/{len(self._results)} with prices\n"
            f"  Raw JSONL (debug): {self.jsonl_path.resolve()}\n"
            f"  Validate CSV (debug): {self.validate_csv.resolve()}\n"
            f"  Trusted rollup + prices: automatic unless --no-post-run",
            flush=True,
        )


def _csv(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'

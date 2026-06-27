"""Post-scan hooks — resolve bulk (M+1) and trusted price rollup (M+1 / M+2)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from market.core.models import DEFAULT_VARIANT_CATALOG_PATH
from market.resolve_bulk import (
    ResolveStats,
    load_bulk_jsonl,
    print_resolve_summary,
    resolve_bulk_observations,
    write_resolved_jsonl,
)
from market.trusted_prices import (
    DEFAULT_TRUSTED_CSV,
    DEFAULT_TRUSTED_GROUPED_CSV,
    DEFAULT_TRUSTED_JSONL,
    aggregate_trusted_prices,
    aggregate_trusted_prices_grouped,
    collect_trusted_price_points,
    write_trusted_csv,
    write_trusted_grouped_csv,
    write_trusted_jsonl,
)
from market.user_prices import load_user_prices
from market.variant_catalog import VariantCatalog

DEFAULT_BULK_RESOLVED = Path("logs/market_all_items_resolved.jsonl")
DEFAULT_SEARCH_PRICES = Path("logs/market_search_prices.jsonl")
DEFAULT_BULK_JSONL = Path("logs/market_all_items.jsonl")


@dataclass(frozen=True)
class TrustedRollupResult:
    item_uid_count: int
    grouped_count: int
    fungible_count: int
    trusted_jsonl: Path
    trusted_csv: Path
    grouped_csv: Path


def run_trusted_prices_rollup(
    *,
    bulk_resolved_path: Path,
    search_prices_path: Path,
    catalog_path: Path = DEFAULT_VARIANT_CATALOG_PATH,
    out_jsonl: Path = DEFAULT_TRUSTED_JSONL,
    out_csv: Path = DEFAULT_TRUSTED_CSV,
    out_grouped_csv: Path = DEFAULT_TRUSTED_GROUPED_CSV,
    write_grouped: bool = True,
) -> TrustedRollupResult:
    catalog = VariantCatalog.load(catalog_path.resolve())
    points = collect_trusted_price_points(
        resolved_bulk_path=bulk_resolved_path.resolve(),
        search_prices_path=search_prices_path.resolve(),
    )
    rows = aggregate_trusted_prices(points)
    out_jsonl = out_jsonl.resolve()
    out_csv = out_csv.resolve()
    out_grouped_csv = out_grouped_csv.resolve()
    write_trusted_jsonl(out_jsonl, rows)
    write_trusted_csv(out_csv, rows)

    grouped_count = 0
    fungible_count = 0
    if write_grouped:
        grouped = aggregate_trusted_prices_grouped(points, catalog)
        write_trusted_grouped_csv(out_grouped_csv, grouped)
        grouped_count = len(grouped)
        fungible_count = sum(1 for row in grouped if row.fungible)

    return TrustedRollupResult(
        item_uid_count=len(rows),
        grouped_count=grouped_count,
        fungible_count=fungible_count,
        trusted_jsonl=out_jsonl,
        trusted_csv=out_csv,
        grouped_csv=out_grouped_csv,
    )


def print_trusted_rollup_summary(result: TrustedRollupResult, *, tag: str = "post-run") -> None:
    print(
        f"[{tag}] trusted rollup — {result.item_uid_count} item_uid(s), "
        f"{result.grouped_count} grouped row(s) ({result.fungible_count} fungible)",
        flush=True,
    )
    print(f"  JSONL: {result.trusted_jsonl}", flush=True)
    print(f"  CSV:   {result.trusted_csv}", flush=True)
    print(f"  Grouped CSV: {result.grouped_csv}", flush=True)


def run_resolve_bulk_pipeline(
    *,
    bulk_path: Path,
    catalog_path: Path = DEFAULT_VARIANT_CATALOG_PATH,
    out_resolved: Path = DEFAULT_BULK_RESOLVED,
    record_aliases: bool = False,
) -> ResolveStats:
    catalog = VariantCatalog.load(catalog_path.resolve())
    observations = load_bulk_jsonl(bulk_path.resolve())
    if not observations:
        raise FileNotFoundError(f"No bulk observations in {bulk_path.resolve()}")

    resolved, stats = resolve_bulk_observations(
        observations,
        catalog,
        record_aliases=record_aliases,
    )
    out_resolved = out_resolved.resolve()
    write_resolved_jsonl(out_resolved, resolved)
    print(f"[post-run] resolve-bulk wrote {out_resolved}", flush=True)
    print_resolve_summary(stats)
    return stats


def _print_prices_hint(grouped_csv: Path) -> None:
    try:
        rows = load_user_prices(grouped_csv)
        print(f"[post-run] decision prices ready — {len(rows)} row(s)", flush=True)
    except FileNotFoundError:
        pass
    print("[post-run] view: python -m cli prices", flush=True)


def run_post_m2_hooks(
    *,
    search_prices_path: Path = DEFAULT_SEARCH_PRICES,
    bulk_resolved_path: Path = DEFAULT_BULK_RESOLVED,
    catalog_path: Path = DEFAULT_VARIANT_CATALOG_PATH,
    out_grouped_csv: Path = DEFAULT_TRUSTED_GROUPED_CSV,
) -> TrustedRollupResult | None:
    """After M+2 search scan — rebuild trusted prices from search + resolved bulk."""
    print("[post-run] M+2 finished — rolling up trusted prices", flush=True)
    if not search_prices_path.is_file():
        print(f"[post-run] skip — search JSONL missing: {search_prices_path.resolve()}", flush=True)
        return None

    result = run_trusted_prices_rollup(
        bulk_resolved_path=bulk_resolved_path,
        search_prices_path=search_prices_path,
        catalog_path=catalog_path,
        out_grouped_csv=out_grouped_csv,
    )
    print_trusted_rollup_summary(result)
    _print_prices_hint(result.grouped_csv)
    return result


def run_post_m1_hooks(
    *,
    bulk_path: Path = DEFAULT_BULK_JSONL,
    bulk_resolved_path: Path = DEFAULT_BULK_RESOLVED,
    search_prices_path: Path = DEFAULT_SEARCH_PRICES,
    catalog_path: Path = DEFAULT_VARIANT_CATALOG_PATH,
    out_grouped_csv: Path = DEFAULT_TRUSTED_GROUPED_CSV,
    record_aliases: bool = False,
) -> TrustedRollupResult | None:
    """After M+1 bulk crawl — resolve-bulk then trusted-prices rollup."""
    print("[post-run] M+1 finished — resolve-bulk + trusted-prices", flush=True)
    if not bulk_path.is_file():
        print(f"[post-run] skip — bulk JSONL missing: {bulk_path.resolve()}", flush=True)
        return None

    try:
        run_resolve_bulk_pipeline(
            bulk_path=bulk_path,
            catalog_path=catalog_path,
            out_resolved=bulk_resolved_path,
            record_aliases=record_aliases,
        )
    except FileNotFoundError as exc:
        print(f"[post-run] resolve-bulk skipped: {exc}", flush=True)
        return None

    result = run_trusted_prices_rollup(
        bulk_resolved_path=bulk_resolved_path,
        search_prices_path=search_prices_path,
        catalog_path=catalog_path,
        out_grouped_csv=out_grouped_csv,
    )
    print_trusted_rollup_summary(result)
    _print_prices_hint(result.grouped_csv)
    return result

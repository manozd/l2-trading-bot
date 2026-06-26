"""Unified CLI for BOHPTS market monitoring."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market.capture_rois import DEFAULT_MARKET_ROI_PATH, REGION_MARKET_WINDOW
from market.constants import DEFAULT_PICO_COM
from market.catalog import DEFAULT_TARGET_LISTS
from market.core.models import BulkRunConfig, SearchRunConfig, DEFAULT_VARIANT_CATALOG_PATH
from market.daemon import DaemonConfig, run_daemon
from market.pico_hid import PicoHidSerial
from market.roi_calibrate import run_region_calibration
from market.search import submit_search_query
from market.search_input import INPUT_PASTE, INPUT_PC, INPUT_PICO
from market.services.bulk_scanner import BulkScanner
from market.services.search_scanner import SearchScanner
from market.build_truncated_list import build_truncated_list_from_pages
from market.truncated_storage import DEFAULT_TRUNCATED_ITEMS_PATH, DEFAULT_TRUNCATED_LISTINGS_PATH
from market.catalog_dedupe import dedupe_catalog, print_dedupe_summary
from market.resolve_bulk import (
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
from market.variant_catalog import VariantCatalog

_LOGS = ROOT / "logs"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli",
        description="BOHPTS market monitor — search (production) and bulk (discovery).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    _add_run_command(sub)
    _add_search_command(sub)
    _add_bulk_command(sub)
    _add_calibrate_command(sub)
    _add_test_keys_command(sub)
    _add_validate_pages_command(sub)
    _add_build_truncated_list_command(sub)
    _add_catalog_command(sub)
    _add_resolve_command(sub)
    _add_trusted_prices_command(sub)
    return parser


def _add_run_command(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "run",
        help="Hotkey daemon (paused by default): C+1 calibrate window, M+1/M+2/M+3 mode, F12 start/stop",
    )
    p.add_argument("--roi-config", type=Path, default=DEFAULT_MARKET_ROI_PATH)
    p.add_argument("--pico-com", type=str, default=DEFAULT_PICO_COM)
    p.add_argument(
        "--targets",
        type=Path,
        default=DEFAULT_TARGET_LISTS,
        help="M+2 priority list (default: config/target_lists.yaml)",
    )
    p.add_argument("--search-category", type=str, default="", help="M+2: scan one YAML category only")
    p.add_argument("--delay", type=float, default=10.0, help="Countdown before scan starts (F12)")
    p.add_argument("--calibrate-delay", type=float, default=2.0, help="Seconds before calib overlay")
    p.add_argument("--monitor", type=int, default=None, help="Monitor index (default: from ROI file)")
    p.add_argument("--bulk-category", type=str, default="all_items")
    p.add_argument("--bulk-pages", type=int, default=200)
    p.add_argument("--bulk-page-delay", type=float, default=0.45)
    p.add_argument("--bulk-vendor-page-delay", type=float, default=0.2)
    p.add_argument("--bulk-max-vendor-pages", type=int, default=1)
    p.add_argument("--no-resume", action="store_true", help="Search: do not skip completed items")
    p.set_defaults(func=cmd_run)


def _add_search_command(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("search", help="M+2 priority monitor (config/target_lists.yaml)")
    p.add_argument("--roi-config", type=Path, default=DEFAULT_MARKET_ROI_PATH)
    p.add_argument(
        "--targets",
        type=Path,
        default=DEFAULT_TARGET_LISTS,
        help="Priority item list (default: config/target_lists.yaml)",
    )
    p.add_argument("--pico-com", type=str, default=DEFAULT_PICO_COM)
    p.add_argument("--category", type=str, default="search", help="Filter YAML category or JSONL tag")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--search-settle", type=float, default=0.45)
    p.add_argument("--back-settle", type=float, default=0.5)
    p.add_argument("--input", choices=[INPUT_PICO, INPUT_PC, INPUT_PASTE], default=INPUT_PICO)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--filter", type=str, default="", dest="name_filter")
    p.add_argument("--delay", type=float, default=10.0)
    p.set_defaults(func=cmd_search)


def _add_bulk_command(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "bulk",
        help="Crawl full market list — open each item's vendors and collect all listings",
    )
    p.add_argument("--roi-config", type=Path, default=DEFAULT_MARKET_ROI_PATH)
    p.add_argument("--pico-com", type=str, default=DEFAULT_PICO_COM)
    p.add_argument("--category", type=str, default="all_items")
    p.add_argument("--pages", type=int, default=200, help="Max list pages to crawl")
    p.add_argument("--page-delay", type=float, default=0.45, help="Delay after list page Next")
    p.add_argument(
        "--vendor-page-delay",
        type=float,
        default=0.2,
        help="Delay after vendor page Next",
    )
    p.add_argument(
        "--max-vendor-pages",
        type=int,
        default=1,
        help="Max vendor pages per item (bulk crawl; use higher for full depth)",
    )
    p.add_argument("--delay", type=float, default=10.0)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--save-images", action="store_true")
    p.add_argument("--no-aggregate", action="store_true")
    p.add_argument(
        "--include-truncated",
        action="store_true",
        help="Include truncated-name rows in bulk output (default: skip)",
    )
    p.add_argument(
        "--truncated-registry",
        type=Path,
        default=DEFAULT_TRUNCATED_ITEMS_PATH,
        help="Registry of truncated item keys (config/truncated_items.json)",
    )
    p.set_defaults(func=cmd_bulk)


def _add_calibrate_command(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("calibrate", help="Calibrate Buy Item market window ROI")
    p.add_argument(
        "which",
        nargs="?",
        default="window",
        choices=["window", "market", "list", "search", "back", "next"],
        help="All targets calibrate the market window (others are aliases)",
    )
    p.add_argument("--monitor", type=int, default=1)
    p.add_argument("-o", "--output", type=Path, default=DEFAULT_MARKET_ROI_PATH)
    p.add_argument("--delay", type=float, default=5.0)
    p.add_argument("--live-alpha", type=float, default=0.5)
    p.set_defaults(func=cmd_calibrate)


def _add_catalog_command(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("catalog", help="Variant-aware item catalog (item_uid + icon_hash)")
    p.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_VARIANT_CATALOG_PATH,
        help="Catalog JSON path (default: config/item_variant_catalog.json)",
    )
    sub2 = p.add_subparsers(dest="catalog_cmd", required=True)
    sp = sub2.add_parser("stats", help="Summary counts")
    sp.set_defaults(catalog_func="stats")
    lp = sub2.add_parser("list", help="List catalog entries")
    lp.add_argument("--group", type=str, default="", help="Filter by variant_group")
    lp.add_argument("--icon", type=str, default="", help="Filter by icon_hash prefix")
    lp.add_argument("--limit", type=int, default=0)
    lp.set_defaults(catalog_func="list")
    sh = sub2.add_parser("shared-icons", help="Icons mapped to multiple item_uids")
    sh.set_defaults(catalog_func="shared_icons")
    dp = sub2.add_parser("dedupe", help="Merge duplicate catalog entries (fuzzy icon + name)")
    dp.add_argument("--fungible-only", action="store_true", help="Only merge currency/material groups")
    dp.add_argument("--dry-run", action="store_true")
    dp.set_defaults(catalog_func="dedupe")
    p.set_defaults(func=cmd_catalog)


def _add_resolve_command(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "resolve-bulk",
        help="Resolve bulk JSONL observations against item_variant_catalog.json",
    )
    p.add_argument(
        "--bulk",
        type=Path,
        default=_LOGS / "market_all_items.jsonl",
        help="Bulk crawl JSONL (default: logs/market_all_items.jsonl)",
    )
    p.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_VARIANT_CATALOG_PATH,
        help="Variant catalog JSON",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=_LOGS / "market_all_items_resolved.jsonl",
        help="Output resolved JSONL",
    )
    p.add_argument(
        "--record-aliases",
        action="store_true",
        help="Add newly matched bulk icon hashes to catalog icon_aliases",
    )
    p.add_argument(
        "--save-catalog",
        action="store_true",
        help="Save catalog after resolve (use with --record-aliases)",
    )
    p.set_defaults(func=cmd_resolve_bulk)


def _add_trusted_prices_command(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "trusted-prices",
        help="Aggregate trusted min prices by item_uid",
    )
    p.add_argument(
        "--bulk-resolved",
        type=Path,
        default=_LOGS / "market_all_items_resolved.jsonl",
    )
    p.add_argument(
        "--search-prices",
        type=Path,
        default=_LOGS / "market_search_prices.jsonl",
    )
    p.add_argument("--out-jsonl", type=Path, default=DEFAULT_TRUSTED_JSONL)
    p.add_argument("--out-csv", type=Path, default=DEFAULT_TRUSTED_CSV)
    p.add_argument(
        "--out-grouped-csv",
        type=Path,
        default=DEFAULT_TRUSTED_GROUPED_CSV,
        help="Fungible rollup by variant_group (default: logs/trusted_min_prices_grouped.csv)",
    )
    p.add_argument(
        "--no-grouped",
        action="store_true",
        help="Skip writing grouped trading-view CSV",
    )
    p.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_VARIANT_CATALOG_PATH,
        help="Catalog for fungible grouping rules",
    )
    p.set_defaults(func=cmd_trusted_prices)


def _add_test_keys_command(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("test-keys", help="Test search box click + type + Enter")
    p.add_argument("--pico-com", type=str, default=DEFAULT_PICO_COM, help=f"Pico serial port (default: {DEFAULT_PICO_COM})")
    p.add_argument("--roi-config", type=Path, default=DEFAULT_MARKET_ROI_PATH)
    p.add_argument("--delay", type=float, default=8.0)
    p.add_argument("--text", default="Angel Slayer")
    p.set_defaults(func=cmd_test_keys)


def cmd_catalog(ns: argparse.Namespace) -> None:
    catalog = VariantCatalog.load(ns.path.resolve())
    if ns.catalog_func == "stats":
        stats = catalog.stats()
        print(f"Catalog: {ns.path.resolve()}", flush=True)
        for k, v in stats.items():
            print(f"  {k}: {v}", flush=True)
        return

    if ns.catalog_func == "shared_icons":
        by_icon: dict[str, list[str]] = {}
        for e in catalog.entries.values():
            for icon in e.all_icon_hashes():
                by_icon.setdefault(icon, []).append(e.item_uid)
        shared = {i: uids for i, uids in by_icon.items() if len(uids) > 1}
        print(f"Shared icons: {len(shared)}", flush=True)
        for icon, uids in sorted(shared.items(), key=lambda x: -len(x[1])):
            print(f"\n  {icon[:48]}...", flush=True)
            for uid in uids:
                ent = catalog.entries[uid]
                print(f"    {uid}  {ent.display_name!r}  query={ent.search_query!r}", flush=True)
        return

    if ns.catalog_func == "dedupe":
        stats = dedupe_catalog(
            catalog,
            fungible_only=ns.fungible_only,
            dry_run=ns.dry_run,
        )
        print_dedupe_summary(stats, dry_run=ns.dry_run)
        if not ns.dry_run:
            print(f"Catalog saved: {ns.path.resolve()}", flush=True)
        return

    entries = list(catalog.entries.values())
    if ns.group:
        g = ns.group.casefold()
        entries = [e for e in entries if (e.variant_group or "").casefold() == g]
    if ns.icon:
        prefix = ns.icon.casefold()
        entries = [e for e in entries if (e.icon_hash or "").casefold().startswith(prefix)]
    entries.sort(key=lambda e: (e.variant_group or "", e.item_uid))
    if ns.limit:
        entries = entries[: ns.limit]
    print(f"Entries: {len(entries)}", flush=True)
    for e in entries:
        icon_short = (e.icon_hash or "")[:24]
        alias_n = len(e.icon_aliases)
        print(
            f"  {e.item_uid}\n"
            f"    name={e.display_name!r}  group={e.variant_group!r}\n"
            f"    icon={icon_short}...  aliases={alias_n}  source={e.source}  query={e.search_query!r}",
            flush=True,
        )


def cmd_resolve_bulk(ns: argparse.Namespace) -> None:
    catalog_path = ns.catalog.resolve()
    catalog = VariantCatalog.load(catalog_path)
    observations = load_bulk_jsonl(ns.bulk.resolve())
    if not observations:
        raise SystemExit(f"No bulk observations in {ns.bulk}")
    resolved, stats = resolve_bulk_observations(
        observations,
        catalog,
        record_aliases=ns.record_aliases,
    )
    write_resolved_jsonl(ns.out.resolve(), resolved)
    print_resolve_summary(stats)
    print(f"[resolve] wrote {ns.out.resolve()}", flush=True)
    if ns.save_catalog or (ns.record_aliases and stats.aliases_added):
        catalog.save()
        print(f"[resolve] catalog saved: {catalog_path}", flush=True)


def cmd_trusted_prices(ns: argparse.Namespace) -> None:
    catalog = VariantCatalog.load(ns.catalog.resolve())
    points = collect_trusted_price_points(
        resolved_bulk_path=ns.bulk_resolved.resolve(),
        search_prices_path=ns.search_prices.resolve(),
    )
    rows = aggregate_trusted_prices(points)
    write_trusted_jsonl(ns.out_jsonl.resolve(), rows)
    write_trusted_csv(ns.out_csv.resolve(), rows)
    print(f"[trusted] {len(rows)} item_uid(s) with trusted prices", flush=True)
    print(f"  JSONL: {ns.out_jsonl.resolve()}", flush=True)
    print(f"  CSV:   {ns.out_csv.resolve()}", flush=True)
    if not ns.no_grouped:
        grouped = aggregate_trusted_prices_grouped(points, catalog)
        write_trusted_grouped_csv(ns.out_grouped_csv.resolve(), grouped)
        fungible_n = sum(1 for r in grouped if r.fungible)
        print(
            f"[trusted] {len(grouped)} grouped row(s) "
            f"({fungible_n} fungible by variant_group)",
            flush=True,
        )
        print(f"  Grouped CSV: {ns.out_grouped_csv.resolve()}", flush=True)


def cmd_run(ns: argparse.Namespace) -> None:
    cfg = DaemonConfig(
        roi_path=ns.roi_config,
        pico_com=ns.pico_com,
        start_delay_s=ns.delay,
        calibrate_delay_s=ns.calibrate_delay,
        monitor=ns.monitor,
        bulk_category=ns.bulk_category,
        bulk_pages=ns.bulk_pages,
        bulk_page_delay_s=ns.bulk_page_delay,
        bulk_vendor_page_delay_s=ns.bulk_vendor_page_delay,
        bulk_max_vendor_pages=ns.bulk_max_vendor_pages,
        search_resume=not ns.no_resume,
        search_targets=ns.targets.resolve(),
        search_category=ns.search_category,
    )
    run_daemon(cfg)


def cmd_search(ns: argparse.Namespace) -> None:
    cfg = SearchRunConfig(
        roi_path=ns.roi_config,
        target_lists=ns.targets.resolve(),
        pico_com=ns.pico_com,
        category=ns.category,
        input_mode=ns.input,
        search_settle_s=ns.search_settle,
        back_settle_s=ns.back_settle,
        start_delay_s=ns.delay,
        limit=ns.limit,
        start=ns.start,
        name_filter=ns.name_filter,
        dry_run=ns.dry_run,
        resume=ns.resume,
    )
    SearchScanner(cfg).run()


def cmd_bulk(ns: argparse.Namespace) -> None:
    cfg = BulkRunConfig(
        roi_path=ns.roi_config,
        pico_com=ns.pico_com,
        category=ns.category,
        pages=ns.pages,
        page_delay_s=ns.page_delay,
        vendor_page_delay_s=ns.vendor_page_delay,
        max_vendor_pages=ns.max_vendor_pages,
        start_delay_s=ns.delay,
        dry_run=ns.dry_run,
        save_images=ns.save_images,
        aggregate=not ns.no_aggregate,
        include_truncated=ns.include_truncated,
        truncated_items_path=ns.truncated_registry,
    )
    BulkScanner(cfg).run()


def cmd_calibrate(ns: argparse.Namespace) -> None:
    out = ns.output.resolve()
    if not run_region_calibration(
        REGION_MARKET_WINDOW,
        monitor_index=ns.monitor,
        output_path=out,
        capture_delay_s=ns.delay,
        live_alpha=ns.live_alpha,
    ):
        raise SystemExit("Calibration cancelled.")
    print(f"Saved {out} (market_window only; UI controls derived at runtime)", flush=True)


def _add_validate_pages_command(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "validate-pages",
        help="Offline OCR validation on saved page PNGs (logs/market_pages/)",
    )
    p.add_argument("--in-dir", type=Path, default=_LOGS / "market_pages")
    p.add_argument("--out-jsonl", type=Path, default=_LOGS / "market_pages_validated.jsonl")
    p.add_argument("--out-csv", type=Path, default=_LOGS / "market_pages_validate.csv")
    p.add_argument("--summary", type=Path, default=_LOGS / "market_pages_validate_summary.json")
    p.add_argument("--glob", type=str, default="page_*.png")
    p.add_argument("--start-page", type=int, default=1)
    p.add_argument("--end-page", type=int, default=0, help="0 = all pages")
    p.add_argument("--no-jsonl", action="store_true", help="Skip JSONL output")
    p.set_defaults(func=cmd_validate_pages)


def _add_build_truncated_list_command(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "build-truncated-list",
        help="Scan saved market pages → truncated-item registry + listings JSONL",
    )
    p.add_argument("--in-dir", type=Path, default=_LOGS / "market_pages")
    p.add_argument("--glob", type=str, default="page_*.png")
    p.add_argument("--out-registry", type=Path, default=DEFAULT_TRUNCATED_ITEMS_PATH)
    p.add_argument("--out-listings", type=Path, default=DEFAULT_TRUNCATED_LISTINGS_PATH)
    p.add_argument("--start-page", type=int, default=1)
    p.add_argument("--end-page", type=int, default=0, help="0 = all pages")
    p.set_defaults(func=cmd_build_truncated_list)


def cmd_build_truncated_list(ns: argparse.Namespace) -> None:
    build_truncated_list_from_pages(
        in_dir=ns.in_dir.resolve(),
        glob_pattern=ns.glob,
        out_registry=ns.out_registry.resolve(),
        out_listings=ns.out_listings.resolve(),
        start_page=ns.start_page,
        end_page=ns.end_page,
    )


def cmd_validate_pages(ns: argparse.Namespace) -> None:
    validate_page_pngs(
        in_dir=ns.in_dir.resolve(),
        glob_pattern=ns.glob,
        start_page=ns.start_page,
        end_page=ns.end_page,
        out_jsonl=None if ns.no_jsonl else ns.out_jsonl.resolve(),
        out_csv=ns.out_csv.resolve(),
        out_summary=ns.summary.resolve(),
    )


def cmd_test_keys(ns: argparse.Namespace) -> None:
    from market.capture_rois import REGION_SEARCH_BOX, load_market_roi_config
    from market.countdown import wait_before_start

    cfg = load_market_roi_config(ns.roi_config.resolve())
    search = cfg.require(REGION_SEARCH_BOX)
    wait_before_start(ns.delay, tag="test-keys")
    pico = PicoHidSerial(ns.pico_com)
    try:
        submit_search_query(ns.text, search=search, pico=pico, settle_s=0.6)
        print("[test-keys] done — check filtered list in game", flush=True)
    finally:
        pico.close()


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    ns = parser.parse_args(argv)
    ns.func(ns)


if __name__ == "__main__":
    main()

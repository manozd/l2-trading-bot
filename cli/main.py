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
from market.core.models import BulkRunConfig, SearchRunConfig
from market.daemon import DaemonConfig, run_daemon
from market.items_db import DEFAULT_ITEMS_DB
from market.pico_hid import PicoHidSerial
from market.roi_calibrate import run_region_calibration
from market.search import submit_search_query
from market.search_input import INPUT_PASTE, INPUT_PC, INPUT_PICO
from market.services.bulk_scanner import BulkScanner
from market.services.search_scanner import SearchScanner
from market.build_truncated_list import build_truncated_list_from_pages
from market.truncated_storage import DEFAULT_TRUNCATED_ITEMS_PATH, DEFAULT_TRUNCATED_LISTINGS_PATH
from market.validate_pages import validate_page_pngs

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
    return parser


def _add_run_command(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "run",
        help="Hotkey daemon (paused by default): C+1 calibrate window, M+1/M+2/M+3 mode, F12 start/stop",
    )
    p.add_argument("--roi-config", type=Path, default=DEFAULT_MARKET_ROI_PATH)
    p.add_argument("--pico-com", type=str, default=DEFAULT_PICO_COM)
    p.add_argument("--items-db", type=Path, default=DEFAULT_ITEMS_DB)
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
    p = sub.add_parser("search", help="Search each DB item and collect row-1 price (production)")
    p.add_argument("--roi-config", type=Path, default=DEFAULT_MARKET_ROI_PATH)
    p.add_argument("--items-db", type=Path, default=DEFAULT_ITEMS_DB)
    p.add_argument("--targets", type=Path, default=None, help="YAML target lists (config/target_lists.yaml)")
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


def _add_test_keys_command(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("test-keys", help="Test search box click + type + Enter")
    p.add_argument("--pico-com", type=str, default=DEFAULT_PICO_COM, help=f"Pico serial port (default: {DEFAULT_PICO_COM})")
    p.add_argument("--roi-config", type=Path, default=DEFAULT_MARKET_ROI_PATH)
    p.add_argument("--delay", type=float, default=8.0)
    p.add_argument("--text", default="Angel Slayer")
    p.set_defaults(func=cmd_test_keys)


def cmd_run(ns: argparse.Namespace) -> None:
    cfg = DaemonConfig(
        roi_path=ns.roi_config,
        pico_com=ns.pico_com,
        items_db=ns.items_db,
        start_delay_s=ns.delay,
        calibrate_delay_s=ns.calibrate_delay,
        monitor=ns.monitor,
        bulk_category=ns.bulk_category,
        bulk_pages=ns.bulk_pages,
        bulk_page_delay_s=ns.bulk_page_delay,
        bulk_vendor_page_delay_s=ns.bulk_vendor_page_delay,
        bulk_max_vendor_pages=ns.bulk_max_vendor_pages,
        search_resume=not ns.no_resume,
    )
    run_daemon(cfg)


def cmd_search(ns: argparse.Namespace) -> None:
    cfg = SearchRunConfig(
        roi_path=ns.roi_config,
        items_db=ns.items_db,
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
    targets = ns.targets.resolve() if ns.targets else None
    SearchScanner(cfg, target_lists=targets).run()


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

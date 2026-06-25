"""Offline OCR validation for search-results screens (screen B)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from market.capture_rois import REGION_MARKET_WINDOW, REGION_SEARCH_BOX, load_market_roi_config
from market.craft.match import (
    filter_search_result_rows,
    find_search_result_price_row,
    format_result_rows,
    is_likely_search_hub,
    pick_result_row,
)
from market.full_list_parser import (
    _ocr_on_bgr,
    _upscale_bgr,
    ocr_shows_search_listings,
    parse_page_rows,
    parse_search_result_rows,
)
from market.ocr_engine import get_ocr_engine, run_ocr
from market.ui_layout import (
    derive_market_ui_regions,
    search_list_top_frac,
    search_result_row_click_xy,
    search_results_crop_bounds,
)
from market.validate_pages import load_png_bgr, page_num_from_path


def load_market_bgr(path: Path, roi_path: Path) -> tuple[np.ndarray, object, object]:
    rgb = np.array(Image.open(path).convert("RGB"))
    cfg = load_market_roi_config(roi_path)
    market = cfg.require(REGION_MARKET_WINDOW)
    regions = derive_market_ui_regions(market)
    search = regions[REGION_SEARCH_BOX]
    h, w = rgb.shape[:2]
    if w >= market.left + market.width and h >= market.top + market.height:
        crop = rgb[market.top : market.top + market.height, market.left : market.left + market.width]
    else:
        crop = rgb
    bgr = crop[:, :, ::-1].copy()
    return bgr, market, search


def validate_one(
    path: Path,
    *,
    roi_path: Path,
    target: str | None,
    ocr,
) -> dict:
    bgr, market, search = load_market_bgr(path, roi_path)
    list_top = search_list_top_frac(market, search)
    y0, y1 = search_results_crop_bounds(market, search)
    click1 = search_result_row_click_xy(market, search, row=1)
    click2 = search_result_row_click_xy(market, search, row=2)

    raw_lines: list[str] = []
    for _box, text, score in run_ocr(ocr, bgr):
        t = text.strip()
        if t:
            raw_lines.append(f"{score}|{t}")

    strip_lines: list[str] = []
    strip = bgr[y0:y1, :]
    scaled = _upscale_bgr(strip, scale=2)
    for _box, text, score in _ocr_on_bgr(scaled, ocr):
        t = text.strip()
        if t:
            strip_lines.append(f"{score}|{t}")

    rows_top = parse_search_result_rows(bgr, page=1, ocr=ocr, top_frac=list_top)
    rows_crop = parse_search_result_rows(
        bgr, page=1, ocr=ocr, top_frac=list_top, crop_y0=y0, crop_y1=y1
    )
    rows_vendor = parse_page_rows(bgr, page=1, ocr=ocr)
    best_rows = rows_crop if rows_crop else rows_top
    visible = filter_search_result_rows(best_rows)

    out = {
        "file": path.name,
        "size": [int(bgr.shape[1]), int(bgr.shape[0])],
        "list_top_frac": round(list_top, 3),
        "crop_y": [y0, y1],
        "click_row1": list(click1),
        "click_row2": list(click2),
        "ocr_shows_search_listings": ocr_shows_search_listings(bgr, crop_y0=y0, crop_y1=y1, ocr=ocr),
        "raw_ocr": raw_lines,
        "strip_ocr": strip_lines,
        "search_rows_top": [r.to_dict() for r in rows_top],
        "search_rows_crop": [r.to_dict() for r in rows_crop],
        "vendor_rows_head": [r.to_dict() for r in rows_vendor[:5]],
        "visible": format_result_rows(visible),
        "is_likely_search_hub": is_likely_search_hub(best_rows),
    }
    if target:
        picked = pick_result_row(best_rows, target, search_query=target)
        out["pick"] = picked.to_dict() if picked else None
        pr = find_search_result_price_row(best_rows, target, search_query=target)
        out["price_row"] = pr.to_dict() if pr else None
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Validate search-results OCR on PNG crops")
    p.add_argument("images", nargs="*", type=Path, help="PNG files (default: none)")
    p.add_argument("--dir", type=Path, help="Directory of page_*.png files")
    p.add_argument("--roi", type=Path, default=Path("config/market_rois.json"))
    p.add_argument("--target", type=str, default="Recipe: Draconic Bow (60%)")
    p.add_argument("--summary", type=Path, help="Write JSON summary")
    p.add_argument("--limit", type=int, default=0, help="Max images from --dir")
    args = p.parse_args()

    paths: list[Path] = list(args.images)
    if args.dir:
        dir_paths = sorted(args.dir.glob("page_*.png"), key=page_num_from_path)
        if args.limit > 0:
            dir_paths = dir_paths[: args.limit]
        paths.extend(dir_paths)
    if not paths:
        raise SystemExit("Provide image paths and/or --dir")

    ocr = get_ocr_engine()
    results: list[dict] = []
    for path in paths:
        print(f"\n{'='*60}\n{path.name}\n{'='*60}", flush=True)
        r = validate_one(path, roi_path=args.roi, target=args.target if len(paths) == 1 else None, ocr=ocr)
        results.append(r)
        print(f"size={r['size']} crop_y={r['crop_y']} click1={r['click_row1']}", flush=True)
        print(f"ocr_shows_search_listings={r['ocr_shows_search_listings']}", flush=True)
        print(f"visible: {r['visible']}", flush=True)
        print(f"search_rows_crop: {len(r['search_rows_crop'])}", flush=True)
        for row in r["search_rows_crop"]:
            print(
                f"  row{row['row']}: {row['item']!r} vendor={row['vendor']!r} "
                f"price={row['price_adena']} raw={row['raw_text']!r}",
                flush=True,
            )
        if r.get("price_row"):
            print(f"price_row: {r['price_row']}", flush=True)
        elif args.target and len(paths) == 1:
            print("price_row: None", flush=True)
        print("strip OCR:", flush=True)
        for line in r["strip_ocr"]:
            print(f"  {line}", flush=True)

    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nWrote {args.summary}", flush=True)


if __name__ == "__main__":
    main()

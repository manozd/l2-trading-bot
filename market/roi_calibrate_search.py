"""Calibrate search box — redirects to market window calibration."""

from __future__ import annotations

from pathlib import Path

from market.capture_rois import DEFAULT_MARKET_ROI_PATH, REGION_MARKET_WINDOW
from market.roi_calibrate import run_region_calibration


def run_search_box_calibration(
    *,
    monitor_index: int,
    output_path: Path = DEFAULT_MARKET_ROI_PATH,
    capture_delay_s: float = 2.0,
    live_alpha: float = 0.5,
) -> bool:
    print("[calibrate] search_box is derived from market_window", flush=True)
    return run_region_calibration(
        REGION_MARKET_WINDOW,
        monitor_index=monitor_index,
        output_path=output_path,
        capture_delay_s=capture_delay_s,
        live_alpha=live_alpha,
    )

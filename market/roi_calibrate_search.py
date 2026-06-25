"""Add search_box ROI to an existing market_rois.json."""

from __future__ import annotations

from pathlib import Path

from market.capture_rois import DEFAULT_MARKET_ROI_PATH, REGION_SEARCH_BOX
from market.roi_calibrate import run_region_calibration


def run_search_box_calibration(
    *,
    monitor_index: int,
    output_path: Path,
    capture_delay_s: float = 5.0,
    live_alpha: float = 0.5,
) -> None:
    if not run_region_calibration(
        REGION_SEARCH_BOX,
        monitor_index=monitor_index,
        output_path=output_path,
        capture_delay_s=capture_delay_s,
        live_alpha=live_alpha,
    ):
        raise SystemExit("Calibration cancelled before finishing all steps.")

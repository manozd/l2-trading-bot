"""Load/save screen regions for market UI automation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MARKET_ROI_PATH = PROJECT_ROOT / "config" / "market_rois.json"

REGION_MARKET_WINDOW = "market_window"
REGION_NEXT_PAGE = "next_page"
REGION_SEARCH_BOX = "search_box"
REGION_BACK_BUTTON = "back_button"

MARKET_ROI_VERSION = 2


@dataclass(frozen=True)
class RoiRect:
    """Axis-aligned region in virtual-screen coordinates (mss space)."""

    left: int
    top: int
    width: int
    height: int

    def to_dict(self) -> dict[str, int]:
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> RoiRect:
        return RoiRect(
            left=int(d["left"]),
            top=int(d["top"]),
            width=int(d["width"]),
            height=int(d["height"]),
        )

    def center_screen(self) -> tuple[int, int]:
        return (self.left + self.width // 2, self.top + self.height // 2)


@dataclass(frozen=True)
class MarketRoiConfig:
    version: int
    monitor: int
    regions: dict[str, RoiRect]

    def require(self, name: str) -> RoiRect:
        if name not in self.regions:
            raise KeyError(f"Missing ROI {name!r} in market config")
        return self.regions[name]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "monitor": self.monitor,
            "regions": {k: v.to_dict() for k, v in self.regions.items()},
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> MarketRoiConfig:
        regions_raw = d.get("regions") or {}
        if not isinstance(regions_raw, dict):
            raise ValueError("market_rois: 'regions' must be an object")
        regions = {str(k): RoiRect.from_dict(v) for k, v in regions_raw.items()}
        return MarketRoiConfig(version=int(d.get("version", 1)), monitor=int(d["monitor"]), regions=regions)


def expand_market_regions(regions: dict[str, RoiRect]) -> dict[str, RoiRect]:
    """Derive search / next / back from ``market_window`` when present."""
    market = regions.get(REGION_MARKET_WINDOW)
    if market is None:
        return dict(regions)
    from market.ui_layout import derive_market_ui_regions

    return derive_market_ui_regions(market)


def slim_market_regions(regions: dict[str, RoiRect]) -> dict[str, RoiRect]:
    """Persist only the calibrated market window."""
    market = regions.get(REGION_MARKET_WINDOW)
    if market is None:
        raise ValueError("market_rois: missing market_window")
    return {REGION_MARKET_WINDOW: market}


def load_market_roi_config(path: Path) -> MarketRoiConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: root must be a JSON object")
    cfg = MarketRoiConfig.from_dict(raw)
    if REGION_MARKET_WINDOW not in cfg.regions:
        raise ValueError(f"{path}: missing required region {REGION_MARKET_WINDOW!r}")
    for name, r in cfg.regions.items():
        if r.width <= 0 or r.height <= 0:
            raise ValueError(f"{path}: region {name!r} has invalid size")
    expanded = expand_market_regions(cfg.regions)
    return MarketRoiConfig(version=cfg.version, monitor=cfg.monitor, regions=expanded)


def save_market_roi_config(path: Path, cfg: MarketRoiConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slim = MarketRoiConfig(
        version=MARKET_ROI_VERSION,
        monitor=cfg.monitor,
        regions=slim_market_regions(cfg.regions),
    )
    path.write_text(json.dumps(slim.to_dict(), indent=2), encoding="utf-8")

"""Interactive ROI calibration for market UI (semi-transparent overlay, saves JSON)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import mss
import tkinter as tk
from tkinter import messagebox

from market.capture_rois import (
    DEFAULT_MARKET_ROI_PATH,
    REGION_BACK_BUTTON,
    REGION_MARKET_WINDOW,
    REGION_NEXT_PAGE,
    REGION_SEARCH_BOX,
    MarketRoiConfig,
    RoiRect,
    load_market_roi_config,
    save_market_roi_config,
)

_CALIB_STEP_TITLE: dict[str, str] = {
    REGION_MARKET_WINDOW: "Market window — full item list area",
    REGION_NEXT_PAGE: "Next page — pagination button",
    REGION_SEARCH_BOX: "Search box — item name filter field",
    REGION_BACK_BUTTON: "Back button — return after viewing search results",
}

_CALIB_STEP_HINT: dict[str, str] = {
    REGION_SEARCH_BOX: (
        "Drag a box on the **search / filter** text field at the top of the market list.\n\n"
        "Game must show Buy Item with the search box visible (Equipment or Full List)."
    ),
    REGION_MARKET_WINDOW: (
        "Drag a box around the **entire Buy Item window** — from the title bar down to "
        "the Back button.\n\n"
        "Game must be on Buy Item → Full List (or category list). "
        "The bot skips title and pagination areas automatically."
    ),
    REGION_NEXT_PAGE: (
        "Drag a small box on the **Next page** button (or arrow) at the bottom of the market window. "
        "The bot moves the PC cursor here and the Pico sends the left click."
    ),
    REGION_BACK_BUTTON: (
        "Drag a box on the **Back** button (returns from item search results to the search screen).\n\n"
        "Open Buy Item and run one search so the Back button is visible, then calibrate."
    ),
}


def _mss_monitor_metrics(monitor_index: int) -> dict[str, int]:
    with mss.mss() as sct:
        if monitor_index < 0 or monitor_index >= len(sct.monitors):
            raise ValueError(f"monitor_index {monitor_index} out of range (0..{len(sct.monitors) - 1})")
        mon = sct.monitors[monitor_index]
    return {
        "left": int(mon["left"]),
        "top": int(mon["top"]),
        "width": int(mon["width"]),
        "height": int(mon["height"]),
    }


def _run_wizard_live_overlay(
    *,
    monitor_index: int,
    output_path: Path,
    steps: list[tuple[str, str]],
    capture_delay_s: float = 0.0,
    live_alpha: float = 0.5,
    initial_regions: dict[str, RoiRect] | None = None,
) -> bool:
    mon = _mss_monitor_metrics(monitor_index)
    w, h = mon["width"], mon["height"]
    left, top = mon["left"], mon["top"]

    if capture_delay_s > 0:
        print(
            "[market-calibrate] Open Buy Item → Full List (page 1), then wait for the overlay.",
            flush=True,
        )
        end = time.monotonic() + float(capture_delay_s)
        while True:
            rem = end - time.monotonic()
            if rem <= 0:
                break
            print(f"[market-calibrate] overlay in {rem:.1f}s — monitor {monitor_index}", flush=True)
            time.sleep(min(1.0, rem))

    root = tk.Tk()
    root.title("Market ROI calibration")
    root.overrideredirect(True)
    root.geometry(f"{w}x{h}+{left}+{top}")
    root.resizable(False, False)
    try:
        root.wm_attributes("-topmost", True)
    except tk.TclError:
        pass

    alpha = max(0.18, min(0.92, float(live_alpha)))
    try:
        root.wm_attributes("-alpha", alpha)
    except tk.TclError as e:
        try:
            root.destroy()
        except tk.TclError:
            pass
        raise SystemExit(
            "[market-calibrate] Semi-transparent overlay (-alpha) is not supported on this Tk build.\n"
            f"Original error: {e}"
        ) from e

    regions: dict[str, RoiRect] = dict(initial_regions or {})
    step_i = 0
    save_monitor = int(monitor_index)
    _cv: list[int] = [max(1, w), max(1, h)]

    def canvas_to_image(cx: int, cy: int) -> tuple[int, int]:
        cww, chh = _cv[0], _cv[1]
        ix = int(round(cx * (w - 1) / max(1, cww - 1)))
        iy = int(round(cy * (h - 1) / max(1, chh - 1)))
        return max(0, min(w - 1, ix)), max(0, min(h - 1, iy))

    def image_to_canvas(ix: int, iy: int) -> tuple[int, int]:
        cww, chh = _cv[0], _cv[1]
        cx = int(round(ix * (cww - 1) / max(1, w - 1)))
        cy = int(round(iy * (chh - 1) / max(1, h - 1)))
        return max(0, min(cww - 1, cx)), max(0, min(chh - 1, cy))

    def image_to_screen_rect(left_i: int, top_i: int, right_i: int, bottom_i: int) -> RoiRect:
        sl = mon["left"] + min(left_i, right_i)
        st = mon["top"] + min(top_i, bottom_i)
        sr = mon["left"] + max(left_i, right_i)
        sb = mon["top"] + max(top_i, bottom_i)
        return RoiRect(left=sl, top=st, width=sr - sl + 1, height=sb - st + 1)

    canvas = tk.Canvas(root, width=w, height=h, bg="#141414", highlightthickness=0, borderwidth=0)
    canvas.pack(fill=tk.BOTH, expand=True)
    root.update_idletasks()
    _cv[0] = max(1, int(canvas.winfo_width()))
    _cv[1] = max(1, int(canvas.winfo_height()))

    hud_title = canvas.create_text(12, 10, anchor=tk.NW, fill="#ffffff", font=("Segoe UI", 12, "bold"), tags="hud")
    hud_hint = canvas.create_text(
        12,
        34,
        anchor=tk.NW,
        fill="#e0e0e0",
        font=("Segoe UI", 10),
        width=max(400, min(900, w - 24)),
        tags="hud",
    )
    hud_keys = canvas.create_text(
        w // 2,
        max(24, h - 12),
        anchor=tk.S,
        fill="#cccccc",
        font=("Segoe UI", 10),
        tags="hud",
        text="Drag rectangle · ESC = quit",
    )

    def refresh_title() -> None:
        key, desc = steps[step_i]
        label = _CALIB_STEP_TITLE.get(key, key)
        canvas.itemconfig(hud_title, text=f"Step {step_i + 1}/{len(steps)} · {label}")
        canvas.itemconfig(hud_hint, text=desc)

    def _on_canvas_configure(_e: tk.Event) -> None:
        _cv[0] = max(1, int(canvas.winfo_width()))
        _cv[1] = max(1, int(canvas.winfo_height()))

    canvas.bind("<Configure>", _on_canvas_configure)
    refresh_title()

    state: dict = {"x0": None, "y0": None, "rect": None}

    def on_press(e: tk.Event) -> None:
        ix, iy = canvas_to_image(e.x, e.y)
        state["x0"], state["y0"] = ix, iy
        if state["rect"] is not None:
            canvas.delete(state["rect"])
            state["rect"] = None

    def on_drag(e: tk.Event) -> None:
        if state["x0"] is None:
            return
        ix, iy = canvas_to_image(e.x, e.y)
        x0, y0 = state["x0"], state["y0"]
        xa, ya = min(x0, ix), min(y0, iy)
        xb, yb = max(x0, ix), max(y0, iy)
        if state["rect"] is not None:
            canvas.delete(state["rect"])
        cxa, cya = image_to_canvas(xa, ya)
        cxb, cyb = image_to_canvas(xb, yb)
        state["rect"] = canvas.create_rectangle(
            min(cxa, cxb), min(cya, cyb), max(cxa, cxb), max(cya, cyb), outline="#0cf", width=2
        )

    def on_release(e: tk.Event) -> None:
        nonlocal step_i
        if state["x0"] is None:
            return
        ix, iy = canvas_to_image(e.x, e.y)
        x0, y0 = state["x0"], state["y0"]
        key, _desc = steps[step_i]
        r = image_to_screen_rect(x0, y0, ix, iy)
        if r.width < 8 or r.height < 8:
            messagebox.showwarning("Too small", "Drag a larger rectangle (at least ~8px each side).")
            state["x0"] = state["y0"] = None
            if state["rect"] is not None:
                canvas.delete(state["rect"])
                state["rect"] = None
            return
        regions[key] = r
        state["x0"] = state["y0"] = None
        if state["rect"] is not None:
            canvas.delete(state["rect"])
            state["rect"] = None
        step_i += 1
        if step_i >= len(steps):
            cfg = MarketRoiConfig(version=1, monitor=save_monitor, regions=regions)
            save_market_roi_config(output_path, cfg)
            messagebox.showinfo("Saved", f"Wrote {output_path}")
            root.destroy()
            return
        refresh_title()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", lambda _e: root.destroy())

    def on_close() -> None:
        if 0 < step_i < len(steps):
            if not messagebox.askokcancel("Quit", "Calibration incomplete. Discard progress?"):
                return
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(100, lambda: root.focus_force())
    root.mainloop()

    return step_i >= len(steps)


def _load_existing_regions(output_path: Path) -> tuple[dict[str, RoiRect], int]:
    regions: dict[str, RoiRect] = {}
    mon = 1
    if output_path.is_file():
        try:
            cfg = load_market_roi_config(output_path)
            regions = dict(cfg.regions)
            mon = int(cfg.monitor)
        except (ValueError, KeyError, json.JSONDecodeError):
            pass
    return regions, mon


def run_region_calibration(
    region_key: str,
    *,
    monitor_index: int | None = None,
    output_path: Path = DEFAULT_MARKET_ROI_PATH,
    capture_delay_s: float = 2.0,
    live_alpha: float = 0.5,
) -> bool:
    """Calibrate a single ROI region. Returns True if saved."""
    if region_key not in _CALIB_STEP_HINT:
        raise ValueError(f"Unknown region {region_key!r}")

    regions, mon = _load_existing_regions(output_path)
    if monitor_index is not None:
        mon = int(monitor_index)

    steps = [(region_key, _CALIB_STEP_HINT[region_key])]
    ok = _run_wizard_live_overlay(
        monitor_index=mon,
        output_path=output_path,
        steps=steps,
        capture_delay_s=capture_delay_s,
        live_alpha=live_alpha,
        initial_regions=regions,
    )
    if not ok:
        print(f"[calibrate] cancelled — {region_key}", flush=True)
    return ok


def run_market_calibration_wizard(
    *,
    monitor_index: int,
    output_path: Path,
    capture_delay_s: float = 5.0,
    live_alpha: float = 0.5,
) -> None:
    """Calibrate ``market_window`` then ``next_page`` for Full List pagination."""
    regions: dict[str, RoiRect] = {}
    mon = int(monitor_index)
    if output_path.is_file():
        try:
            cfg = load_market_roi_config(output_path)
            regions = dict(cfg.regions)
            mon = int(cfg.monitor)
        except (ValueError, KeyError, json.JSONDecodeError):
            pass

    steps: list[tuple[str, str]] = [
        (
            REGION_MARKET_WINDOW,
            _CALIB_STEP_HINT[REGION_MARKET_WINDOW],
        ),
        (
            REGION_NEXT_PAGE,
            "Drag a small box on the **Next page** button (or arrow) at the bottom of the market window. "
            "The bot moves the PC cursor here and the Pico sends the left click.",
        ),
    ]
    if not _run_wizard_live_overlay(
        monitor_index=mon,
        output_path=output_path,
        steps=steps,
        capture_delay_s=capture_delay_s,
        live_alpha=live_alpha,
        initial_regions=regions,
    ):
        raise SystemExit("Calibration cancelled before finishing all steps.")

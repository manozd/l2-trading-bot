"""Win32 cursor movement for market UI (PC positions; Pico clicks).

L2 / raw-input clients often ignore a single SetCursorPos — retry until
GetCursorPos matches, same approach as Lineage2Bot.
"""

from __future__ import annotations

import sys
import time


def _ease_smoothstep(t: float) -> float:
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t * (3.0 - 2.0 * t)


_dpi_ready = False


def _ensure_dpi_aware() -> None:
    global _dpi_ready
    if _dpi_ready or sys.platform != "win32":
        return
    _dpi_ready = True
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # type: ignore[attr-defined]
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def get_cursor_pos() -> tuple[int, int]:
    if sys.platform != "win32":
        raise OSError("get_cursor_pos requires Windows")
    import ctypes

    _ensure_dpi_aware()
    user32 = ctypes.windll.user32
    pt = ctypes.wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def sync_cursor_to(
    x: int,
    y: int,
    *,
    tol: int = 2,
    attempts: int = 14,
    step_sleep: float = 0.012,
) -> tuple[int, int]:
    """Repeat SetCursorPos until the OS cursor reaches the target."""
    import ctypes

    _ensure_dpi_aware()
    user32 = ctypes.windll.user32
    last = get_cursor_pos()
    for _ in range(attempts):
        user32.SetCursorPos(int(x), int(y))
        time.sleep(max(0.003, step_sleep))
        last = get_cursor_pos()
        if abs(last[0] - x) <= tol and abs(last[1] - y) <= tol:
            break
    return last


def smooth_move_to(
    x: int,
    y: int,
    *,
    duration_s: float = 0.22,
    steps: int = 16,
    sync: bool = True,
    debug: bool = False,
) -> tuple[int, int]:
    """Move OS cursor to virtual-screen (x, y); return final position."""
    if sys.platform != "win32":
        raise OSError("smooth_move_to requires Windows")
    import ctypes

    _ensure_dpi_aware()
    user32 = ctypes.windll.user32
    x0, y0 = get_cursor_pos()
    steps = max(2, int(steps))
    for i in range(1, steps + 1):
        u = _ease_smoothstep(i / steps)
        nx = int(round(x0 + (x - x0) * u))
        ny = int(round(y0 + (y - y0) * u))
        user32.SetCursorPos(nx, ny)
        time.sleep(max(0.0, duration_s) / steps)
    if sync:
        final = sync_cursor_to(x, y)
    else:
        user32.SetCursorPos(int(x), int(y))
        final = get_cursor_pos()
    if debug:
        print(f"[input] cursor move target=({x},{y}) final=({final[0]},{final[1]})", flush=True)
    return final

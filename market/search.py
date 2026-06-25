"""Market search box: focus, enter query, submit with Pico Enter."""

from __future__ import annotations

import time

from market.capture_rois import RoiRect
from market.input_ctl import get_cursor_pos, smooth_move_to
from market.pc_keyboard import paste_search_text, type_search_text
from market.pico_hid import PicoHidSerial
from market.run_control import RunControl, check_stop, sleep_checked
from market.search_input import INPUT_PASTE, INPUT_PC, INPUT_PICO, pico_strips_characters, unsupported_pico_chars


def park_cursor_on_back(back: RoiRect, *, settle_s: float = 0.12) -> None:
    """Move cursor onto Back so it does not cover list rows during OCR."""
    park_cursor_for_ocr(back=back, settle_s=settle_s)


def park_cursor_for_ocr(
    *,
    back: RoiRect,
    next_btn: RoiRect | None = None,
    on_next: bool = False,
    settle_s: float = 0.08,
    move_duration_s: float = 0.12,
) -> None:
    """
    Park cursor on Back (first page) or Next (after pagination) for OCR.

    Skips the move if the cursor is already on the target (saves ~0.3s per page).
    """
    target = next_btn if (on_next and next_btn is not None) else back
    cx, cy = target.center_screen()
    cur_x, cur_y = get_cursor_pos()
    if abs(cur_x - cx) <= 12 and abs(cur_y - cy) <= 12:
        time.sleep(settle_s)
        return
    smooth_move_to(cx, cy, duration_s=move_duration_s, steps=6, sync=False)
    time.sleep(settle_s)


def click_roi(
    roi: RoiRect,
    pico: PicoHidSerial,
    *,
    label: str,
    settle_s: float = 0.35,
    fast: bool = False,
    run_control: RunControl | None = None,
) -> None:
    check_stop(run_control)
    cx, cy = roi.center_screen()
    if fast:
        smooth_move_to(cx, cy, duration_s=0.1, steps=6, sync=False)
        sleep_checked(0.03, run_control=run_control)
        pico.click_left_prepare(hold_ms=100, ping=True)
        sleep_checked(min(settle_s, 0.25), run_control=run_control)
        return
    before = get_cursor_pos()
    print(f"[search] PC move ({before[0]},{before[1]}) -> ({cx},{cy}) [{label}]", flush=True)
    final = smooth_move_to(cx, cy, duration_s=0.24, steps=18, sync=True, debug=True)
    if abs(final[0] - cx) > 4 or abs(final[1] - cy) > 4:
        print(f"[search] WARNING: cursor did not reach {label}", flush=True)
    sleep_checked(0.08, run_control=run_control)
    print(f"[search] Pico CLICK {label}", flush=True)
    pico.click_left_prepare(hold_ms=120, ping=True)
    sleep_checked(settle_s, run_control=run_control)


def focus_search_box(
    search: RoiRect,
    pico: PicoHidSerial,
    *,
    settle_s: float = 0.25,
    fast: bool = False,
    clear: bool = False,
    run_control: RunControl | None = None,
) -> None:
    click_roi(
        search,
        pico,
        label="search box",
        settle_s=settle_s,
        fast=fast,
        run_control=run_control,
    )
    if clear:
        sleep_checked(0.08, run_control=run_control)
        pico.click_left(hold_ms=100, double=True)
        sleep_checked(0.12, run_control=run_control)


def submit_search_query(
    query: str,
    *,
    search: RoiRect,
    pico: PicoHidSerial,
    settle_s: float = 0.45,
    input_mode: str = INPUT_PICO,
    fast: bool = False,
    run_control: RunControl | None = None,
) -> None:
    """Click search box, enter text, Pico Enter to apply filter."""
    check_stop(run_control)
    focus_search_box(
        search,
        pico,
        settle_s=0.2 if fast else 0.25,
        fast=fast,
        clear=(input_mode == INPUT_PICO),
        run_control=run_control,
    )

    if input_mode == INPUT_PASTE:
        print("[search] clipboard set + PC Ctrl+A/V (often blocked in L2 / GameGuard)", flush=True)
        paste_search_text(query)
    elif input_mode == INPUT_PC:
        print("[search] PC SendInput typing (may be blocked by GameGuard)", flush=True)
        type_search_text(query)
    else:
        bad = unsupported_pico_chars(query)
        if bad:
            raise ValueError(
                f"Item name has chars Pico firmware cannot type: {sorted(bad)!r}. "
                "Use --input pc or shorten the name."
            )
        if not fast:
            if pico_strips_characters(query):
                print("[search] pico typing with SPACE/symbols (requires updated firmware)", flush=True)
            print(f"[search] pico typing {query!r}", flush=True)
        pico.type_search_text(query, run_control=run_control)
        if not fast:
            print("[search] pico typed", flush=True)

    sleep_checked(0.15 if fast else 0.28, run_control=run_control)
    pico.key_enter()
    if not fast:
        print("[search] pico KEY ENTER", flush=True)
    sleep_checked(settle_s, run_control=run_control)


def press_back_button(
    *,
    back: RoiRect,
    pico: PicoHidSerial,
    settle_s: float = 0.5,
    fast: bool = False,
    run_control: RunControl | None = None,
) -> None:
    """Click Back to leave filtered results before the next search."""
    click_roi(
        back,
        pico,
        label="back button",
        settle_s=settle_s,
        fast=fast,
        run_control=run_control,
    )
    if not fast:
        print("[search] back — ready for next item", flush=True)

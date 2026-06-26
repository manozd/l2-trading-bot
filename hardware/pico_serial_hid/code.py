# SPDX-License-Identifier: MIT
# Raspberry Pi Pico 2 + CircuitPython: read text lines from USB serial, emit HID mouse/keyboard.
# Copy to CIRCUITPY/code.py. Requires adafruit_hid in CIRCUITPY/lib/.
#
# Protocol (one line per command, UTF-8, newline-terminated):
#   CLICK [hold_ms]     — left button down, hold, up (default hold_ms=120)
#   DBLCLICK [hold_ms]  — two left clicks (80ms gap between)
#   L_DOWN / L_UP       — left button press / release (hold + drag: use MOVE between)
#   MOVE dx dy          — relative move; large deltas are split into HID-sized steps
#   R_DOWN / R_UP       — right mouse button press / release (camera: hold RMB + MOVE)
#   KEY NAME            — NAME is F1, F3, F4, ENTER, ESC, TAB, BACKSPACE, DELETE, SPACE, MINUS,
#                         LPAREN, RPAREN, PERCENT, COLON, APOSTROPHE (case-insensitive),
#                         or one letter a–z / digit 0–9

import sys
import time

import supervisor
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode
from adafruit_hid.mouse import Mouse

mouse = Mouse(usb_hid.devices)
kbd = Keyboard(usb_hid.devices)

_KEY = {
    "F1": Keycode.F1,
    "F3": Keycode.F3,
    "F4": Keycode.F4,
    "ENTER": Keycode.ENTER,
    "RETURN": Keycode.ENTER,
    "ESC": Keycode.ESCAPE,
    "ESCAPE": Keycode.ESCAPE,
    "TAB": Keycode.TAB,
    "BACKSPACE": Keycode.BACKSPACE,
    "DELETE": Keycode.DELETE,
    "SPACE": Keycode.SPACEBAR,
    "SPACEBAR": Keycode.SPACEBAR,
    "MINUS": Keycode.MINUS,
    "HYPHEN": Keycode.MINUS,
    "APOSTROPHE": Keycode.QUOTE,
    "QUOTE": Keycode.QUOTE,
    "SINGLEQUOTE": Keycode.QUOTE,
}

_SHIFT_KEY = {
    "LPAREN": Keycode.NINE,
    "RPAREN": Keycode.ZERO,
    "PERCENT": Keycode.FIVE,
    "COLON": Keycode.SEMICOLON,
}

_DIGIT_KEYCODES = (
    Keycode.ZERO,
    Keycode.ONE,
    Keycode.TWO,
    Keycode.THREE,
    Keycode.FOUR,
    Keycode.FIVE,
    Keycode.SIX,
    Keycode.SEVEN,
    Keycode.EIGHT,
    Keycode.NINE,
)


def _keycode_for_key_command(tok: str):
    """Return Keycode for KEY <tok>, or None if unknown."""
    if len(tok) == 1:
        ch = tok
        if ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
            return getattr(Keycode, ch.upper(), None)
        if ch.isdigit():
            return _DIGIT_KEYCODES[int(ch)]
    return _KEY.get(tok.upper())


def _prep_mouse():
    try:
        mouse.release_all()
    except Exception:
        try:
            mouse.release(Mouse.LEFT_BUTTON)
            mouse.release(Mouse.RIGHT_BUTTON)
        except Exception:
            pass
    time.sleep(0.012)


def _tap_keycode(code):
    try:
        kbd.release_all()
    except Exception:
        try:
            kbd.release(code)
        except Exception:
            pass
    time.sleep(0.02)
    kbd.press(code)
    time.sleep(0.055)
    kbd.release(code)
    time.sleep(0.04)


def _tap_shifted(base_keycode):
    try:
        kbd.release_all()
    except Exception:
        pass
    time.sleep(0.02)
    kbd.press(Keycode.SHIFT, base_keycode)
    time.sleep(0.055)
    kbd.release_all()
    time.sleep(0.04)


def _clamp_step(v):
    if v > 127:
        return 127
    if v < -127:
        return -127
    return v


def _move_rel(dx, dy):
    while dx != 0 or dy != 0:
        mx = _clamp_step(dx)
        my = _clamp_step(dy)
        mouse.move(mx, my)
        dx -= mx
        dy -= my


def _parse_hold_ms(parts, default_ms):
    if len(parts) < 2:
        return default_ms / 1000.0
    try:
        return max(0.0, float(parts[1]) / 1000.0)
    except ValueError:
        return default_ms / 1000.0


def handle_line(line):
    parts = line.strip().split()
    if not parts:
        return
    cmd = parts[0].upper()

    if cmd == "CLICK":
        hold = _parse_hold_ms(parts, 120)
        _prep_mouse()
        mouse.press(Mouse.LEFT_BUTTON)
        time.sleep(hold)
        mouse.release(Mouse.LEFT_BUTTON)
        return

    if cmd == "DBLCLICK":
        hold = _parse_hold_ms(parts, 120)
        _prep_mouse()
        mouse.press(Mouse.LEFT_BUTTON)
        time.sleep(hold)
        mouse.release(Mouse.LEFT_BUTTON)
        time.sleep(0.08)
        mouse.press(Mouse.LEFT_BUTTON)
        time.sleep(hold)
        mouse.release(Mouse.LEFT_BUTTON)
        return

    if cmd == "MOVE" and len(parts) >= 3:
        _move_rel(int(parts[1]), int(parts[2]))
        return

    if cmd == "R_DOWN":
        mouse.press(Mouse.RIGHT_BUTTON)
        return

    if cmd == "R_UP":
        mouse.release(Mouse.RIGHT_BUTTON)
        return

    if cmd == "L_DOWN":
        mouse.press(Mouse.LEFT_BUTTON)
        return

    if cmd == "L_UP":
        mouse.release(Mouse.LEFT_BUTTON)
        return

    if cmd == "KEY" and len(parts) >= 2:
        tok = parts[1].upper()
        if tok in _SHIFT_KEY:
            _tap_shifted(_SHIFT_KEY[tok])
            return
        code = _keycode_for_key_command(parts[1])
        if code is None:
            return
        _tap_keycode(code)
        return


buf = ""
while True:
    n = supervisor.runtime.serial_bytes_available
    if n:
        buf += sys.stdin.read(n)
    while "\n" in buf:
        line, buf = buf.split("\n", 1)
        try:
            handle_line(line)
        except Exception:
            pass
    time.sleep(0.01)

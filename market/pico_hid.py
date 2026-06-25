"""USB serial HID bridge to Raspberry Pi Pico (Lineage2Bot-compatible firmware)."""

from __future__ import annotations

import time


class PicoHidSerial:
    """Send line commands to Lineage2Bot Pico firmware (hardware/pico_serial_hid/code.py)."""

    def __init__(self, port: str, *, baud: int = 115200) -> None:
        import serial as pyserial  # type: ignore[import-untyped]

        self._ser = pyserial.Serial(port, baudrate=baud, timeout=0.25)
        time.sleep(0.15)

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass

    def _line(self, s: str) -> None:
        if not s.endswith("\n"):
            s += "\n"
        self._ser.write(s.encode("utf-8"))
        self._ser.flush()

    def click_left(self, *, hold_ms: int = 120, double: bool = False) -> None:
        hm = max(0, int(hold_ms))
        self._line(f"DBLCLICK {hm}" if double else f"CLICK {hm}")
        time.sleep(0.04)

    def move_rel(self, dx: int, dy: int) -> None:
        self._line(f"MOVE {int(dx)} {int(dy)}")
        time.sleep(0.02)

    def click_left_prepare(self, *, hold_ms: int = 120, ping: bool = True) -> None:
        """Tiny HID move ping before click — some L2 clients need this after keyboard input."""
        if ping:
            self.move_rel(0, 1)
            time.sleep(0.012)
            self.move_rel(0, -1)
            time.sleep(0.012)
        self.click_left(hold_ms=hold_ms)

    def key_named(self, name: str) -> None:
        self._line(f"KEY {name.upper()}")
        time.sleep(0.07)

    def key_enter(self) -> None:
        """Hardware Enter (GameGuard-safe). Needs updated Pico firmware."""
        self.key_named("ENTER")

    def key_tap_char(self, ch: str) -> None:
        if len(ch) != 1:
            raise ValueError("key_tap_char expects one character")
        self._line(f"KEY {ch}")
        time.sleep(0.07)

    def type_search_text(self, text: str) -> str:
        """Type item name via USB HID (a-z, 0-9, space, -, (), %)."""
        from market.search_input import iter_pico_key_tokens

        tokens = iter_pico_key_tokens(text)
        for tok in tokens:
            if len(tok) == 1:
                self.key_tap_char(tok)
            else:
                self.key_named(tok)
        return text

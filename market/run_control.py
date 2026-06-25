"""Cooperative stop flag for long-running scan loops."""

from __future__ import annotations

import threading
import time


class StopRequested(Exception):
    """Raised when F12 / run_control requests a graceful stop."""


class RunControl:
    def __init__(self) -> None:
        self._event = threading.Event()

    def request_stop(self) -> None:
        self._event.set()

    def clear(self) -> None:
        self._event.clear()

    def should_stop(self) -> bool:
        return self._event.is_set()


def check_stop(run_control: RunControl | None) -> None:
    """Raise ``StopRequested`` when F12 stop was pressed."""
    if run_control and run_control.should_stop():
        raise StopRequested()


def sleep_checked(seconds: float, *, run_control: RunControl | None = None) -> None:
    """Sleep in short slices so F12 stop is picked up quickly."""
    if seconds <= 0:
        if run_control and run_control.should_stop():
            raise StopRequested()
        return
    end = time.monotonic() + float(seconds)
    while True:
        if run_control and run_control.should_stop():
            raise StopRequested()
        rem = end - time.monotonic()
        if rem <= 0:
            break
        time.sleep(min(0.05, rem))

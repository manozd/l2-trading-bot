"""Cooperative stop flag for long-running scan loops."""

from __future__ import annotations

import threading


class RunControl:
    def __init__(self) -> None:
        self._event = threading.Event()

    def request_stop(self) -> None:
        self._event.set()

    def clear(self) -> None:
        self._event.clear()

    def should_stop(self) -> bool:
        return self._event.is_set()

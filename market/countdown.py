"""Console countdown before live automation (time to focus the game)."""

from __future__ import annotations

import time


def wait_before_start(seconds: float, *, tag: str = "market") -> None:
    if seconds <= 0:
        return
    print(f"[{tag}] Switch to the game — starting in {seconds:.0f}s", flush=True)
    end = time.monotonic() + float(seconds)
    while True:
        rem = end - time.monotonic()
        if rem <= 0:
            break
        print(f"[{tag}] {rem:.0f}s …", flush=True)
        time.sleep(min(1.0, rem))
    print(f"[{tag}] go", flush=True)

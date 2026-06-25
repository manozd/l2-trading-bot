"""Console countdown before live automation (time to focus the game)."""

from __future__ import annotations

import time

from market.run_control import RunControl, StopRequested, sleep_checked


def wait_before_start(
    seconds: float,
    *,
    tag: str = "market",
    run_control: RunControl | None = None,
) -> None:
    if seconds <= 0:
        return
    from market.run_control import sleep_checked

    print(f"[{tag}] Switch to the game — starting in {seconds:.0f}s", flush=True)
    end = time.monotonic() + float(seconds)
    while True:
        if run_control and run_control.should_stop():
            raise StopRequested()
        rem = end - time.monotonic()
        if rem <= 0:
            break
        print(f"[{tag}] {rem:.0f}s …", flush=True)
        sleep_checked(min(1.0, rem), run_control=run_control)
    print(f"[{tag}] go", flush=True)

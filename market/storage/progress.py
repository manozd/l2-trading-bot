"""Resume checkpoint for long search runs."""

from __future__ import annotations

import json
from pathlib import Path


class ProgressStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load_done(self) -> set[str]:
        if not self._path.is_file():
            return set()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return {str(x).casefold() for x in data.get("done", [])}
        except (json.JSONDecodeError, TypeError, AttributeError):
            return set()

    def save_done(self, done: set[str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"done": sorted(done, key=str.casefold)}, indent=2),
            encoding="utf-8",
        )

    def mark_done(self, name: str, done: set[str]) -> None:
        done.add(name.casefold())
        self.save_done(done)

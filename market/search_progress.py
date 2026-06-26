"""Versioned resume checkpoints for M+2 priority search."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

M2_MODE_VERSION = "m2_catalog_v2"


def target_config_hash(
    target_lists: Path,
    *,
    category_filter: str | None = None,
) -> str:
    """Hash target list file + optional category filter."""
    h = hashlib.sha256()
    h.update(target_lists.read_bytes())
    h.update(b"\0")
    h.update((category_filter or "").casefold().encode())
    return h.hexdigest()[:12]


class SearchProgressStore:
    """
    Resume keyed by item_id + mode_version + target_config_hash.

    Legacy ``{"done": [...]}`` files are ignored (treated as stale).
    """

    def __init__(
        self,
        path: Path,
        *,
        mode_version: str,
        config_hash: str,
    ) -> None:
        self._path = path
        self._mode_version = mode_version
        self._config_hash = config_hash

    def load_done_item_ids(self) -> set[str]:
        if not self._path.is_file():
            return set()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, TypeError, AttributeError):
            return set()

        if not isinstance(data, dict):
            return set()

        if data.get("mode_version") != self._mode_version:
            return set()
        if data.get("target_config_hash") != self._config_hash:
            return set()

        completed = data.get("completed") or []
        if not isinstance(completed, list):
            return set()

        out: set[str] = set()
        for entry in completed:
            if isinstance(entry, dict):
                item_id = entry.get("item_id")
                if item_id:
                    out.add(str(item_id).casefold())
            elif isinstance(entry, str):
                out.add(entry.casefold())
        return out

    def is_legacy_stale(self) -> bool:
        """True when an old-format progress file exists but will not apply."""
        if not self._path.is_file():
            return False
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, TypeError, AttributeError):
            return False
        if not isinstance(data, dict):
            return False
        if "done" in data and data.get("mode_version") != self._mode_version:
            return True
        if data.get("mode_version") != self._mode_version:
            return bool(data.get("completed") or data.get("done"))
        return data.get("target_config_hash") != self._config_hash

    def clear(self) -> None:
        """Remove progress file (fresh scan on next run)."""
        if self._path.is_file():
            self._path.unlink()

    def save_snapshot(self) -> dict[str, Any]:
        """Return current progress payload for display."""
        if not self._path.is_file():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError, AttributeError):
            return {}

    @property
    def path(self) -> Path:
        return self._path

    def mark_done(
        self,
        *,
        item_id: str,
        search_query: str,
        done: set[str],
    ) -> None:
        key = item_id.casefold()
        done.add(key)
        self._save(done, last_item_id=item_id, last_search_query=search_query)

    def _save(
        self,
        done: set[str],
        *,
        last_item_id: str | None = None,
        last_search_query: str | None = None,
    ) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()

        existing: dict[str, dict] = {}
        if self._path.is_file():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if (
                    isinstance(raw, dict)
                    and raw.get("mode_version") == self._mode_version
                    and raw.get("target_config_hash") == self._config_hash
                ):
                    for entry in raw.get("completed") or []:
                        if isinstance(entry, dict) and entry.get("item_id"):
                            existing[str(entry["item_id"]).casefold()] = entry
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass

        if last_item_id and last_search_query:
            existing[last_item_id.casefold()] = {
                "item_id": last_item_id,
                "search_query": last_search_query,
                "done_at": now,
            }

        completed = sorted(existing.values(), key=lambda e: str(e.get("item_id", "")).casefold())
        payload = {
            "mode_version": self._mode_version,
            "target_config_hash": self._config_hash,
            "completed": completed,
        }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

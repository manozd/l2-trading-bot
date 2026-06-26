"""Build config/items_database.txt from bulk crawl observations."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from market.items_db import DEFAULT_ITEMS_DB
from market.resolve_bulk import load_bulk_jsonl

_TRAILING_ELLIPSIS = re.compile(r"\.{2,}\s*$")
_FILE_HEADER = (
    "# BOHPTS market item names — one per line.\n"
    "# From bulk crawl OCR; edit truncated or bad lines. Lines starting with # are ignored.\n"
)


def normalize_bulk_visible_name(name: str | None) -> str | None:
    """Strip whitespace and trailing list-view ellipsis."""
    if not name:
        return None
    text = name.strip()
    if not text:
        return None
    text = _TRAILING_ELLIPSIS.sub("", text).strip()
    return text or None


def collect_bulk_item_names(
    observations: list[dict[str, Any]],
    *,
    include_resolved_names: bool = False,
) -> dict[str, str]:
    """
    Return casefold key → display name from bulk observations.

    Primary source is ``list_context.visible_name_ocr`` (market list label).
    """
    names: dict[str, str] = {}

    def add(raw: str | None) -> None:
        text = normalize_bulk_visible_name(raw)
        if not text:
            return
        key = text.casefold()
        if key not in names:
            names[key] = text

    for obs in observations:
        lc = obs.get("list_context") or {}
        add(lc.get("visible_name_ocr"))
        if not include_resolved_names:
            continue
        identity = obs.get("identity") or {}
        for field in ("catalog_search_query", "item_name", "display_name"):
            add(identity.get(field))

    return names


def build_items_database_from_bulk(
    bulk_path: Path,
    *,
    include_resolved_names: bool = False,
    existing_path: Path | None = None,
) -> list[str]:
    """Collect sorted unique names from bulk JSONL, optionally merging an existing file."""
    observations = load_bulk_jsonl(bulk_path.resolve())
    names = collect_bulk_item_names(
        observations,
        include_resolved_names=include_resolved_names,
    )

    if existing_path and existing_path.is_file():
        for line in existing_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:
                line = line.rsplit("|", 1)[0].strip()
            add_key = line.casefold()
            if add_key not in names:
                names[add_key] = line

    return sorted(names.values(), key=str.casefold)


def write_items_database(path: Path, names: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = _FILE_HEADER + "\n".join(names) + ("\n" if names else "")
    path.write_text(body, encoding="utf-8")


def print_build_summary(
    *,
    bulk_path: Path,
    out_path: Path,
    observation_count: int,
    name_count: int,
) -> None:
    print(
        f"[items-db] {name_count} unique name(s) from {observation_count} bulk observation(s)",
        flush=True,
    )
    print(f"  source: {bulk_path.resolve()}", flush=True)
    print(f"  output: {out_path.resolve()}", flush=True)

"""Stable item identity: icon + visible name slug + optional catalog / tooltip full name."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from market.enchant import split_item_base_and_enchant

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_PATH = PROJECT_ROOT / "config" / "item_name_catalog.json"


def item_slug(name: str | None) -> str:
    """Normalize visible (often truncated) list name for composite keys."""
    if not name:
        return ""
    t = name.lower().strip()
    t = re.sub(r"\.{2,}$", "", t)
    t = re.sub(r"[^a-z0-9]+", "", t)
    return t[:48]


def make_item_key(
    *,
    icon_hash: str | None,
    item: str | None,
    enchant: int | None = None,
) -> str | None:
    """
    Composite key: icon + visible name slug + optional enchant.

    Same icon but different visible text → different keys (e.g. Ar... vs We...).
    +0 vs +4 → different keys even when base name matches.
    """
    base, enc_from_name = split_item_base_and_enchant(item)
    enc = enchant if enchant is not None else enc_from_name
    slug_base = base or item
    if not icon_hash:
        slug = item_slug(slug_base)
        if not slug:
            return f"e{enc}" if enc is not None else None
        return f"{slug}_e{enc}" if enc is not None else slug
    slug = item_slug(slug_base)
    if slug:
        if enc is not None:
            return f"{icon_hash}:{slug}_e{enc}"
        return f"{icon_hash}:{slug}"
    return icon_hash


def load_name_catalog(path: Path = DEFAULT_CATALOG_PATH) -> dict[str, str]:
    """Map item_key → full in-game item name (manual or tooltip-enriched)."""
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(v, str):
            out[str(k)] = v
        elif isinstance(v, dict) and isinstance(v.get("full_name"), str):
            out[str(k)] = v["full_name"]
    return out


def save_name_catalog(catalog: dict[str, str], path: Path = DEFAULT_CATALOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: {"full_name": v} for k, v in sorted(catalog.items())}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_catalog(row: dict[str, Any], catalog: dict[str, str]) -> dict[str, Any]:
    key = row.get("item_key")
    if key and key in catalog:
        row = {**row, "item_full_name": catalog[key], "name_source": "catalog"}
    elif row.get("item_full_name"):
        row.setdefault("name_source", "tooltip")
    else:
        row.setdefault("name_source", "list_truncated")
    return row


def find_ambiguous_groups(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Same icon_hash but different visible item strings → need tooltip or catalog."""
    by_icon: dict[str, set[str]] = {}
    for r in rows:
        icon = r.get("item_icon_hash")
        item = r.get("item")
        if not icon or not item:
            continue
        by_icon.setdefault(icon, set()).add(item)
    return {k: sorted(v) for k, v in by_icon.items() if len(v) > 1}

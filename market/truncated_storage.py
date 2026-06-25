"""Registry of market items whose names truncate in the list view."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from market.name_truncation import is_truncated_market_row
from market.row_fields import sanitize_vendor_nickname

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRUNCATED_ITEMS_PATH = PROJECT_ROOT / "config" / "truncated_items.json"
DEFAULT_TRUNCATED_LISTINGS_PATH = PROJECT_ROOT / "logs" / "truncated_items_listings.jsonl"

SCHEMA_VERSION = 2

IdentityClass = Literal["unique", "ambiguous"]


def normalize_list_prefix(name: str | None) -> str:
    """Normalize visible list text for prefix collision grouping."""
    if not name:
        return ""
    t = name.lower().strip()
    t = re.sub(r"\.{2,}$", "", t).strip(" .")
    return t


@dataclass
class TruncatedItemEntry:
    item_key: str
    visible_name: str | None
    item_icon_hash: str | None
    item_slug: str | None
    enchant: int | None
    min_price_adena: int | None
    max_price_adena: int | None
    listing_count: int
    vendors: list[str] = field(default_factory=list)
    sample_page: int | None = None
    source: str = "bootstrap"
    updated_at: str = ""
    list_prefix: str = ""
    prefix_candidate_count: int = 1
    identity_class: IdentityClass = "ambiguous"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TruncatedItemsStore:
    items: dict[str, TruncatedItemEntry] = field(default_factory=dict)
    updated_at: str = ""
    source: str = "bootstrap"

    def known_keys(self) -> set[str]:
        return set(self.items.keys())

    def get_entry(self, item_key: str | None) -> TruncatedItemEntry | None:
        if not item_key:
            return None
        return self.items.get(str(item_key))

    def is_known_truncated(self, item_key: str | None) -> bool:
        return bool(item_key and item_key in self.items)

    def is_ambiguous(self, item_key: str | None) -> bool:
        entry = self.get_entry(item_key)
        return entry is not None and entry.identity_class == "ambiguous"

    def is_unique_truncated(self, item_key: str | None) -> bool:
        entry = self.get_entry(item_key)
        return entry is not None and entry.identity_class == "unique"

    def recompute_identity_classes(self) -> None:
        """Group by visible list prefix; unique → one item_key, ambiguous → several."""
        by_prefix: dict[str, list[str]] = defaultdict(list)
        for key, entry in self.items.items():
            prefix = normalize_list_prefix(entry.visible_name or entry.item_slug)
            entry.list_prefix = prefix
            if prefix:
                by_prefix[prefix].append(key)
            else:
                entry.prefix_candidate_count = 1
                entry.identity_class = "ambiguous"

        for prefix, keys in by_prefix.items():
            count = len(keys)
            klass: IdentityClass = "unique" if count == 1 else "ambiguous"
            for key in keys:
                entry = self.items[key]
                entry.list_prefix = prefix
                entry.prefix_candidate_count = count
                entry.identity_class = klass

    def identity_summary(self) -> dict[str, int]:
        unique = sum(1 for e in self.items.values() if e.identity_class == "unique")
        ambiguous = sum(1 for e in self.items.values() if e.identity_class == "ambiguous")
        prefixes = {e.list_prefix for e in self.items.values() if e.list_prefix}
        ambig_prefixes = sum(
            1 for p in prefixes if sum(1 for e in self.items.values() if e.list_prefix == p) > 1
        )
        return {
            "item_keys": len(self.items),
            "unique": unique,
            "ambiguous": ambiguous,
            "unique_prefixes": len(prefixes) - ambig_prefixes,
            "ambiguous_prefixes": ambig_prefixes,
        }

    def merge_listing_row(self, row: dict[str, Any], *, source: str = "bootstrap") -> None:
        """Upsert one truncated listing observation."""
        if not is_truncated_market_row(row):
            return
        key = row.get("item_key")
        if not key:
            return
        key = str(key)
        price = row.get("price_adena")
        price_i: int | None
        try:
            price_i = int(price) if price is not None else None
        except (TypeError, ValueError):
            price_i = None

        vendor_raw = row.get("vendor")
        vendor = sanitize_vendor_nickname(vendor_raw) if vendor_raw else None
        now = datetime.now(timezone.utc).isoformat()

        if key not in self.items:
            visible = row.get("item")
            prefix = normalize_list_prefix(visible or row.get("item_slug"))
            self.items[key] = TruncatedItemEntry(
                item_key=key,
                visible_name=visible,
                item_icon_hash=row.get("item_icon_hash"),
                item_slug=row.get("item_slug"),
                enchant=row.get("enchant"),
                min_price_adena=price_i,
                max_price_adena=price_i,
                listing_count=1 if price_i is not None else 0,
                vendors=[vendor] if vendor else [],
                sample_page=row.get("page"),
                source=source,
                updated_at=now,
                list_prefix=prefix,
            )
            return

        entry = self.items[key]
        entry.visible_name = row.get("item") or entry.visible_name
        entry.item_icon_hash = row.get("item_icon_hash") or entry.item_icon_hash
        entry.item_slug = row.get("item_slug") or entry.item_slug
        entry.list_prefix = normalize_list_prefix(entry.visible_name or entry.item_slug)
        if row.get("enchant") is not None:
            entry.enchant = row.get("enchant")
        if price_i is not None:
            entry.listing_count += 1
            if entry.min_price_adena is None or price_i < entry.min_price_adena:
                entry.min_price_adena = price_i
                entry.sample_page = row.get("page")
            if entry.max_price_adena is None or price_i > entry.max_price_adena:
                entry.max_price_adena = price_i
        if vendor and vendor not in entry.vendors:
            entry.vendors.append(vendor)
        entry.updated_at = now

    def merge_rows(self, rows: list[dict[str, Any]], *, source: str = "bootstrap") -> int:
        before = len(self.items)
        for row in rows:
            self.merge_listing_row(row, source=source)
        self.recompute_identity_classes()
        return len(self.items) - before

    def to_dict(self) -> dict[str, Any]:
        summary = self.identity_summary()
        ambig_prefix_counts: dict[str, int] = {}
        by_prefix: dict[str, int] = defaultdict(int)
        for entry in self.items.values():
            if entry.list_prefix:
                by_prefix[entry.list_prefix] = entry.prefix_candidate_count
        for prefix, count in sorted(by_prefix.items()):
            if count > 1:
                ambig_prefix_counts[prefix] = count

        return {
            "schema_version": SCHEMA_VERSION,
            "updated_at": self.updated_at or datetime.now(timezone.utc).isoformat(),
            "source": self.source,
            "item_count": len(self.items),
            "unique_count": summary["unique"],
            "ambiguous_count": summary["ambiguous"],
            "ambiguous_prefixes": ambig_prefix_counts,
            "items": {k: v.to_dict() for k, v in sorted(self.items.items())},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TruncatedItemsStore:
        raw_items = data.get("items") or {}
        items: dict[str, TruncatedItemEntry] = {}
        if isinstance(raw_items, dict):
            for key, val in raw_items.items():
                if isinstance(val, TruncatedItemEntry):
                    items[str(key)] = val
                elif isinstance(val, dict):
                    known = {f.name for f in TruncatedItemEntry.__dataclass_fields__.values()}
                    filtered = {k: v for k, v in val.items() if k in known}
                    items[str(key)] = TruncatedItemEntry(**filtered)
        store = cls(
            items=items,
            updated_at=str(data.get("updated_at") or ""),
            source=str(data.get("source") or "bootstrap"),
        )
        if store.items:
            store.recompute_identity_classes()
        return store


def load_truncated_store(path: Path = DEFAULT_TRUNCATED_ITEMS_PATH) -> TruncatedItemsStore:
    if not path.is_file():
        return TruncatedItemsStore()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return TruncatedItemsStore()
    return TruncatedItemsStore.from_dict(data)


def save_truncated_store(store: TruncatedItemsStore, path: Path = DEFAULT_TRUNCATED_ITEMS_PATH) -> None:
    store.recompute_identity_classes()
    store.updated_at = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def write_truncated_listings_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def apply_bulk_identity_tags(row: dict[str, Any], store: TruncatedItemsStore | None) -> None:
    """Set identity fields on a bulk JSONL row before write."""
    key = row.get("item_key")
    entry = store.get_entry(key) if store else None

    if not is_truncated_market_row(row):
        row.setdefault("identity_status", "unresolved")
        row.setdefault("item_name_source", "ocr_list")
        row.setdefault("name_source", "list_full")
        return

    row.setdefault("item_name_source", "ocr_truncated")
    row.setdefault("name_source", "list_truncated")
    if entry:
        row["list_prefix"] = entry.list_prefix
        row["prefix_candidate_count"] = entry.prefix_candidate_count
        row["identity_class"] = entry.identity_class
        if entry.identity_class == "unique":
            row["identity_status"] = "truncated_unique"
        else:
            row["identity_status"] = "truncated_ambiguous"
    else:
        row.setdefault("identity_status", "truncated_ambiguous")
        row.setdefault("identity_class", "ambiguous")


def should_include_in_bulk(
    row: dict[str, Any],
    store: TruncatedItemsStore | None = None,
) -> bool:
    """
    Bulk keeps full-name rows and truncated rows with a unique list prefix.

    Ambiguous truncated (same visible prefix, multiple item_keys) → search only.
    """
    if not is_truncated_market_row(row):
        return True

    key = row.get("item_key")
    if not store or not key:
        return False

    entry = store.get_entry(key)
    if entry is None:
        return False
    return entry.identity_class == "unique"


def prepare_bulk_row(
    row: dict[str, Any],
    store: TruncatedItemsStore,
    *,
    include_all_truncated: bool = False,
) -> bool:
    """
    Classify a row for bulk output. Mutates ``row`` with identity tags.

    Returns True if the row should be written to bulk JSONL.
    """
    if is_truncated_market_row(row):
        store.merge_listing_row(row, source="bulk_scan")
        store.recompute_identity_classes()

    if include_all_truncated:
        apply_bulk_identity_tags(row, store)
        return True

    if should_include_in_bulk(row, store):
        apply_bulk_identity_tags(row, store)
        return True

    return False

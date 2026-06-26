"""Variant-aware item catalog — stable item_uid with canonical icon + observed aliases."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from market.core.item_id import item_id_from_name
from market.icon_hash import (
    FUZZY_NAME_ACCEPT_MAX,
    color_tag_match,
    dhash_hamming,
)
from market.item_identity import item_slug

PROJECT_ROOT = Path(__file__).resolve().parents[1]

try:
    from market.core.models import DEFAULT_VARIANT_CATALOG_PATH
except ImportError:
    DEFAULT_VARIANT_CATALOG_PATH = PROJECT_ROOT / "config" / "item_variant_catalog.json"

CATALOG_VERSION = 2

FUNGIBLE_CATEGORIES = frozenset({"currency"})


def is_fungible_category(category: str | None) -> bool:
    return (category or "").casefold() in FUNGIBLE_CATEGORIES


def is_fungible_entry(entry: CatalogEntry | None) -> bool:
    return entry is not None and is_fungible_category(entry.category)


def normalize_display_name(name: str | None) -> str:
    """Lowercase, collapse whitespace, strip trailing ellipsis."""
    if not name:
        return ""
    t = name.strip().lower()
    t = re.sub(r"\.{2,}$", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def icon_hash_suffix(icon_hash: str, *, length: int = 16) -> str:
    hex_part = icon_hash.split(":")[0]
    return hex_part[:length]


def make_item_uid(*, base_id: str, icon_hash: str | None) -> str:
    """
    Legacy variant id: ``{base_id}__icon_{hash_prefix}``.

    Prefer ``variant_group`` as stable ``item_uid`` after dedupe; this form is
    still used when first ingesting ambiguous multi-variant search rows.
    """
    base = base_id.strip("_") or "unknown"
    if not icon_hash:
        return base
    return f"{base}__icon_{icon_hash_suffix(icon_hash)}"


def variant_group_from_query(search_query: str) -> str:
    """Group variants that share a search/display family (without icon suffix)."""
    return item_id_from_name(search_query)


@dataclass
class CatalogEntry:
    item_uid: str
    display_name: str
    normalized_name: str
    icon_hash: str | None
    category: str | None = None
    variant_group: str | None = None
    search_query: str | None = None
    source: str = "search_confirmed"
    disambiguation: str | None = None
    first_seen_at: str = ""
    last_seen_at: str = ""
    icon_aliases: list[dict[str, Any]] = field(default_factory=list)

    @property
    def canonical_icon_hash(self) -> str | None:
        return self.icon_hash

    def all_icon_hashes(self) -> list[str]:
        out: list[str] = []
        if self.icon_hash:
            out.append(self.icon_hash)
        for alias in self.icon_aliases:
            h = alias.get("icon_hash")
            if h and h not in out:
                out.append(str(h))
        return out

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if not self.icon_aliases:
            d.pop("icon_aliases", None)
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CatalogEntry:
        aliases = raw.get("icon_aliases") or []
        if not isinstance(aliases, list):
            aliases = []
        return cls(
            item_uid=str(raw["item_uid"]),
            display_name=str(raw.get("display_name") or ""),
            normalized_name=str(raw.get("normalized_name") or ""),
            icon_hash=raw.get("icon_hash") or raw.get("canonical_icon_hash"),
            category=raw.get("category"),
            variant_group=raw.get("variant_group"),
            search_query=raw.get("search_query"),
            source=str(raw.get("source") or "search_confirmed"),
            disambiguation=raw.get("disambiguation"),
            first_seen_at=str(raw.get("first_seen_at") or ""),
            last_seen_at=str(raw.get("last_seen_at") or ""),
            icon_aliases=[a for a in aliases if isinstance(a, dict)],
        )


@dataclass
class VariantCatalog:
    """In-memory catalog keyed by item_uid with icon_hash + alias index."""

    path: Path = field(default_factory=lambda: DEFAULT_VARIANT_CATALOG_PATH)
    entries: dict[str, CatalogEntry] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> VariantCatalog:
        path = path or DEFAULT_VARIANT_CATALOG_PATH
        catalog = cls(path=path.resolve())
        if not path.is_file():
            return catalog
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw.get("entries") if isinstance(raw, dict) else raw
        if isinstance(items, dict):
            for uid, entry_raw in items.items():
                if isinstance(entry_raw, dict):
                    entry = CatalogEntry.from_dict(entry_raw)
                    catalog.entries[entry.item_uid or str(uid)] = entry
        elif isinstance(items, list):
            for entry_raw in items:
                if isinstance(entry_raw, dict):
                    entry = CatalogEntry.from_dict(entry_raw)
                    catalog.entries[entry.item_uid] = entry
        return catalog

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "version": CATALOG_VERSION,
            "updated_at": now,
            "entries": {uid: e.to_dict() for uid, e in sorted(self.entries.items())},
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, item_uid: str) -> CatalogEntry | None:
        return self.entries.get(item_uid)

    def find_by_icon(self, icon_hash: str | None) -> list[CatalogEntry]:
        if not icon_hash:
            return []
        out: list[CatalogEntry] = []
        for entry in self.entries.values():
            if icon_hash in entry.all_icon_hashes():
                out.append(entry)
        return out

    def find_by_variant_group(self, variant_group: str) -> list[CatalogEntry]:
        vg = variant_group.casefold()
        return [e for e in self.entries.values() if (e.variant_group or "").casefold() == vg]

    def add_icon_alias(
        self,
        entry: CatalogEntry,
        *,
        icon_hash: str,
        source: str,
        match_method: str,
        distance_to_canonical: int | None = None,
        scanned_at: str | None = None,
    ) -> None:
        """Record an observed hash linked to ``entry`` (not promoted to canonical)."""
        if icon_hash == entry.icon_hash:
            return
        now = scanned_at or datetime.now(timezone.utc).isoformat()
        for alias in entry.icon_aliases:
            if alias.get("icon_hash") == icon_hash:
                alias["last_seen_at"] = now
                return
        entry.icon_aliases.append(
            {
                "icon_hash": icon_hash,
                "source": source,
                "first_seen_at": now,
                "last_seen_at": now,
                "match_method": match_method,
                "distance_to_canonical": distance_to_canonical,
            }
        )
        entry.last_seen_at = now
        self.entries[entry.item_uid] = entry

    def upsert(self, entry: CatalogEntry) -> CatalogEntry:
        now = datetime.now(timezone.utc).isoformat()
        existing = self.entries.get(entry.item_uid)
        if existing:
            entry.first_seen_at = existing.first_seen_at or entry.first_seen_at or now
            entry.icon_aliases = existing.icon_aliases or entry.icon_aliases
        else:
            entry.first_seen_at = entry.first_seen_at or now
        entry.last_seen_at = now
        self.entries[entry.item_uid] = entry
        return entry

    def upsert_from_search_row(
        self,
        *,
        row: dict[str, Any],
        search_query: str,
        display_name: str,
        item_id: str,
        category: str | None,
        scanned_at: str,
        source: str = "search_confirmed",
    ) -> CatalogEntry | None:
        icon_hash = row.get("item_icon_hash")
        row_display = row.get("item") or row.get("item_display") or display_name
        if not icon_hash:
            return None
        if not row_display and not search_query:
            return None

        base_id = item_id or item_id_from_name(search_query)
        vgroup = variant_group_from_query(search_query)
        normalized = normalize_display_name(row_display) or item_slug(row_display)

        existing = self.find_by_icon(icon_hash)
        if existing:
            entry = existing[0]
            if is_fungible_category(category):
                entry = self._consolidate_fungible_entry(entry, variant_group=vgroup, stable_uid=base_id)
            entry.last_seen_at = scanned_at
            self.entries[entry.item_uid] = entry
            return entry

        if is_fungible_category(category):
            return self._upsert_fungible(
                icon_hash=icon_hash,
                row_display=row_display or display_name,
                normalized=normalized,
                base_id=base_id,
                variant_group=vgroup,
                search_query=search_query,
                category=category,
                scanned_at=scanned_at,
                source=source,
            )

        item_uid = make_item_uid(base_id=base_id, icon_hash=icon_hash)
        entry = CatalogEntry(
            item_uid=item_uid,
            display_name=row_display or display_name,
            normalized_name=normalized,
            icon_hash=icon_hash,
            category=category,
            variant_group=vgroup,
            search_query=search_query,
            source=source,
            disambiguation=self._disambiguation_hint(icon_hash, search_query),
            first_seen_at=scanned_at,
            last_seen_at=scanned_at,
        )
        return self.upsert(entry)

    def resolve_item_uid(
        self,
        *,
        item_id: str,
        icon_hash: str | None,
        category: str | None,
        search_query: str | None = None,
    ) -> str:
        """Stable uid for M+2 price rows — consolidated ``variant_group`` when fungible."""
        if is_fungible_category(category):
            stable_uid = item_id or variant_group_from_query(search_query or "")
            entry = self.get(stable_uid)
            if entry:
                return entry.item_uid
            if icon_hash:
                for candidate in self.find_by_icon(icon_hash):
                    if (candidate.variant_group or "") == stable_uid:
                        return (
                            candidate.item_uid
                            if candidate.item_uid == stable_uid
                            else stable_uid
                        )
            return stable_uid
        if icon_hash:
            matches = self.find_by_icon(icon_hash)
            if matches:
                return matches[0].item_uid
        return make_item_uid(base_id=item_id, icon_hash=icon_hash)

    def _consolidate_fungible_entry(
        self,
        entry: CatalogEntry,
        *,
        variant_group: str,
        stable_uid: str,
    ) -> CatalogEntry:
        """Move legacy ``__icon_`` fungible entries onto ``stable_uid`` when needed."""
        if entry.item_uid == stable_uid:
            return entry
        if (entry.variant_group or "") != variant_group:
            return entry

        stable = self.get(stable_uid)
        if stable is None:
            old_key = entry.item_uid
            entry.item_uid = stable_uid
            entry.variant_group = variant_group
            self.entries.pop(old_key, None)
            self.entries[stable_uid] = entry
            return entry

        if entry.icon_hash and entry.icon_hash != stable.icon_hash:
            dist = dhash_hamming(entry.icon_hash, stable.icon_hash)
            self.add_icon_alias(
                stable,
                icon_hash=entry.icon_hash,
                source="catalog_consolidate",
                match_method="search_confirmed",
                distance_to_canonical=dist,
            )
        for alias in entry.icon_aliases:
            h = alias.get("icon_hash")
            if h:
                self.add_icon_alias(
                    stable,
                    icon_hash=str(h),
                    source=str(alias.get("source") or "catalog_consolidate"),
                    match_method=str(alias.get("match_method") or "search_confirmed"),
                    distance_to_canonical=alias.get("distance_to_canonical"),
                )
        self.entries.pop(entry.item_uid, None)
        return stable

    def _upsert_fungible(
        self,
        *,
        icon_hash: str,
        row_display: str,
        normalized: str,
        base_id: str,
        variant_group: str,
        search_query: str,
        category: str | None,
        scanned_at: str,
        source: str,
    ) -> CatalogEntry:
        stable_uid = base_id or variant_group
        entry = self.get(stable_uid)
        if entry is None:
            group = self.find_by_variant_group(variant_group)
            if len(group) == 1:
                entry = self._consolidate_fungible_entry(
                    group[0], variant_group=variant_group, stable_uid=stable_uid,
                )
            elif len(group) > 1:
                preferred = next((e for e in group if e.item_uid == stable_uid), group[0])
                entry = self._consolidate_fungible_entry(
                    preferred, variant_group=variant_group, stable_uid=stable_uid,
                )

        if entry is None:
            entry = CatalogEntry(
                item_uid=stable_uid,
                display_name=row_display,
                normalized_name=normalized,
                icon_hash=icon_hash,
                category=category,
                variant_group=variant_group,
                search_query=search_query,
                source=source,
                first_seen_at=scanned_at,
                last_seen_at=scanned_at,
            )
            return self.upsert(entry)

        if icon_hash != entry.icon_hash:
            if not entry.icon_hash:
                entry.icon_hash = icon_hash
            else:
                dist = dhash_hamming(icon_hash, entry.icon_hash)
                self.add_icon_alias(
                    entry,
                    icon_hash=icon_hash,
                    source=source,
                    match_method="search_confirmed",
                    distance_to_canonical=dist,
                    scanned_at=scanned_at,
                )
        entry.last_seen_at = scanned_at
        entry.display_name = row_display or entry.display_name
        self.entries[entry.item_uid] = entry
        return entry

    def upsert_from_search_rows(
        self,
        *,
        rows: list[dict[str, Any]],
        search_query: str,
        display_name: str,
        item_id: str,
        category: str | None,
        scanned_at: str,
        source: str = "search_confirmed",
    ) -> list[CatalogEntry]:
        seen_uids: set[str] = set()
        saved: list[CatalogEntry] = []
        for row in rows:
            entry = self.upsert_from_search_row(
                row=row,
                search_query=search_query,
                display_name=display_name,
                item_id=item_id,
                category=category,
                scanned_at=scanned_at,
                source=source,
            )
            if entry is None or entry.item_uid in seen_uids:
                continue
            seen_uids.add(entry.item_uid)
            saved.append(entry)
        return saved

    def fuzzy_icon_candidates(
        self,
        icon_hash: str | None,
        *,
        max_distance: int = FUZZY_NAME_ACCEPT_MAX,
        require_color_match: bool = True,
    ) -> list[tuple[CatalogEntry, int]]:
        """Catalog entries within fuzzy dHash distance of ``icon_hash``."""
        if not icon_hash:
            return []
        hits: list[tuple[CatalogEntry, int]] = []
        for entry in self.entries.values():
            best: int | None = None
            for candidate_hash in entry.all_icon_hashes():
                if require_color_match and not color_tag_match(icon_hash, candidate_hash):
                    continue
                dist = dhash_hamming(icon_hash, candidate_hash)
                if dist is None or dist > max_distance:
                    continue
                best = dist if best is None else min(best, dist)
            if best is not None:
                hits.append((entry, best))
        hits.sort(key=lambda t: t[1])
        return hits

    def _disambiguation_hint(self, icon_hash: str, search_query: str) -> str | None:
        if re.search(r"\bstage\s+\d+\b", search_query, re.I):
            return "search_only"
        matches = self.find_by_icon(icon_hash)
        other_queries = {
            e.search_query for e in matches if e.search_query and e.search_query != search_query
        }
        if matches and other_queries:
            return "search_only"
        return None

    def stats(self) -> dict[str, Any]:
        icons: dict[str, int] = {}
        alias_count = 0
        groups: dict[str, int] = {}
        for e in self.entries.values():
            for h in e.all_icon_hashes():
                icons[h] = icons.get(h, 0) + 1
            alias_count += len(e.icon_aliases)
            if e.variant_group:
                groups[e.variant_group] = groups.get(e.variant_group, 0) + 1
        shared_icons = sum(1 for c in icons.values() if c > 1)
        multi_variant_groups = sum(1 for c in groups.values() if c > 1)
        return {
            "entries": len(self.entries),
            "unique_icons": len(icons),
            "icon_aliases": alias_count,
            "icons_shared_by_multiple_uids": shared_icons,
            "variant_groups": len(groups),
            "groups_with_multiple_uids": multi_variant_groups,
        }

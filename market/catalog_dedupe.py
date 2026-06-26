"""Merge duplicate catalog entries — same variant_group + compatible name + fuzzy icon."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from market.craft.match import _MIN_ACCEPT_SCORE, _match_score
from market.icon_hash import FUZZY_NAME_ACCEPT_MAX, dhash_hamming
from market.variant_catalog import CatalogEntry, VariantCatalog, normalize_display_name


@dataclass
class DedupeStats:
    merged_groups: int = 0
    entries_removed: int = 0
    aliases_added: int = 0
    review_needed: list[str] = field(default_factory=list)


def _names_merge_compatible(a: CatalogEntry, b: CatalogEntry) -> bool:
    if (a.variant_group or "").casefold() != (b.variant_group or "").casefold():
        return False
    if a.variant_group and a.variant_group == b.variant_group:
        if a.search_query and b.search_query:
            if normalize_display_name(a.search_query) == normalize_display_name(b.search_query):
                sa = _match_score(a.display_name, b.display_name)
                sb = _match_score(b.display_name, a.display_name)
                if max(sa, sb) >= _MIN_ACCEPT_SCORE:
                    return True
                if normalize_display_name(a.display_name) == normalize_display_name(b.display_name):
                    return True
                if _match_score(a.display_name, a.search_query or "") >= _MIN_ACCEPT_SCORE and (
                    _match_score(b.display_name, b.search_query or "") >= _MIN_ACCEPT_SCORE
                ):
                    return True
    sa = _match_score(a.display_name, b.display_name)
    return sa >= 85 and _match_score(b.display_name, a.display_name) >= 85


def _icon_merge_compatible(a: CatalogEntry, b: CatalogEntry) -> bool:
    best: int | None = None
    for ha in a.all_icon_hashes():
        for hb in b.all_icon_hashes():
            dist = dhash_hamming(ha, hb)
            if dist is None:
                continue
            best = dist if best is None else min(best, dist)
    return best is not None and best <= FUZZY_NAME_ACCEPT_MAX


def _pick_survivor(entries: list[CatalogEntry]) -> CatalogEntry:
    def sort_key(e: CatalogEntry) -> tuple:
        uid_stable = 0 if "__icon_" not in e.item_uid else 1
        return (uid_stable, e.last_seen_at or "", e.first_seen_at or "")

    return sorted(entries, key=sort_key, reverse=True)[0]


def dedupe_catalog(
    catalog: VariantCatalog,
    *,
    fungible_only: bool = False,
    dry_run: bool = False,
) -> DedupeStats:
    """
    Merge entries in the same ``variant_group`` when names and icons agree.

    When ``fungible_only`` is True, only merge groups where all entries share
    the same search_query family (currency/material style), not multi-SA gear.
    """
    stats = DedupeStats()
    if not dry_run:
        for key in list(catalog.entries):
            entry = catalog.entries[key]
            if key != entry.item_uid:
                catalog.entries.pop(key, None)
                catalog.entries[entry.item_uid] = entry

    by_group: dict[str, list[CatalogEntry]] = {}
    for entry in list(catalog.entries.values()):
        vg = (entry.variant_group or entry.item_uid).casefold()
        by_group.setdefault(vg, []).append(entry)

    for vg, group in by_group.items():
        if len(group) < 2:
            continue

        if fungible_only:
            queries = {normalize_display_name(e.search_query or "") for e in group}
            if len(queries) > 1:
                continue
            names = {normalize_display_name(e.display_name) for e in group}
            if len(names) > 2:
                stats.review_needed.append(vg)
                continue
            if len(group) >= 2 and all(_names_merge_compatible(group[0], e) for e in group[1:]):
                survivor = _pick_survivor(group)
                old_survivor_key = survivor.item_uid
                target_uid = str(survivor.variant_group or survivor.item_uid)
                survivor.item_uid = target_uid
                for other in group:
                    if other is survivor:
                        continue
                    for h in other.all_icon_hashes():
                        if h == survivor.icon_hash:
                            continue
                        dist = dhash_hamming(h, survivor.icon_hash)
                        if not dry_run:
                            catalog.add_icon_alias(
                                survivor,
                                icon_hash=h,
                                source="catalog_dedupe",
                                match_method="fuzzy_icon_name_matched",
                                distance_to_canonical=dist,
                                scanned_at=other.last_seen_at,
                            )
                        stats.aliases_added += 1
                    if not dry_run:
                        catalog.entries.pop(other.item_uid, None)
                    stats.entries_removed += 1
                if not dry_run:
                    if old_survivor_key != target_uid:
                        catalog.entries.pop(old_survivor_key, None)
                    catalog.entries[target_uid] = survivor
                stats.merged_groups += 1
                continue

        clusters: list[list[CatalogEntry]] = []
        for entry in group:
            placed = False
            for cluster in clusters:
                if all(
                    _names_merge_compatible(entry, other) and _icon_merge_compatible(entry, other)
                    for other in cluster
                ):
                    cluster.append(entry)
                    placed = True
                    break
            if not placed:
                clusters.append([entry])

        for cluster in clusters:
            if len(cluster) < 2:
                continue

            survivor = _pick_survivor(cluster)
            target_uid = survivor.variant_group or survivor.item_uid
            old_survivor_key = survivor.item_uid
            if target_uid and "__icon_" in survivor.item_uid:
                survivor.item_uid = str(target_uid)

            for other in cluster:
                if other is survivor:
                    continue
                for h in other.all_icon_hashes():
                    if h == survivor.icon_hash:
                        continue
                    dist = dhash_hamming(h, survivor.icon_hash)
                    if not dry_run:
                        catalog.add_icon_alias(
                            survivor,
                            icon_hash=h,
                            source="catalog_dedupe",
                            match_method="fuzzy_icon_name_matched",
                            distance_to_canonical=dist,
                            scanned_at=other.last_seen_at,
                        )
                    stats.aliases_added += 1
                if not dry_run:
                    catalog.entries.pop(other.item_uid, None)
                stats.entries_removed += 1

            if not dry_run:
                if old_survivor_key != survivor.item_uid:
                    catalog.entries.pop(old_survivor_key, None)
                catalog.entries[survivor.item_uid] = survivor
            stats.merged_groups += 1

    if not dry_run and stats.merged_groups:
        catalog.save()

    return stats


def print_dedupe_summary(stats: DedupeStats, *, dry_run: bool) -> None:
    mode = "dry-run" if dry_run else "applied"
    print(f"[catalog dedupe] {mode}", flush=True)
    print(f"  merged groups: {stats.merged_groups}", flush=True)
    print(f"  entries removed: {stats.entries_removed}", flush=True)
    print(f"  aliases added: {stats.aliases_added}", flush=True)
    if stats.review_needed:
        print(f"  review needed ({len(stats.review_needed)} groups):", flush=True)
        for g in stats.review_needed[:10]:
            print(f"    - {g}", flush=True)

"""Resolve UI-truncated market names to canonical labels and item ids."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from market.canonical_status import (
    CANONICAL_ALIAS,
    CANONICAL_AMBIGUOUS_PREFIX,
    CANONICAL_EXACT,
    CANONICAL_UNIQUE_PREFIX,
    HIGH_TRUST_TIERS,
    SOURCE_TIER_ALIAS,
    SOURCE_TIER_CATALOG,
    SOURCE_TIER_ITEMS_DB,
    SOURCE_TIER_ORDER,
    SOURCE_TIER_RECIPE,
    SOURCE_TIER_TARGET,
    SOURCE_TIER_TRUNCATED,
    TRUSTED_HINT_NO,
    TRUSTED_HINT_WARNING,
    TRUSTED_HINT_YES,
    UNRESOLVED,
)
from market.catalog import DEFAULT_TARGET_LISTS, load_target_list_refs
from market.core.item_id import item_id_from_name
from market.craft.recipe_db import DEFAULT_RECIPES_DIR, load_all_recipes
from market.items_db import DEFAULT_ITEMS_DB, load_item_entries
from market.name_aliases import DEFAULT_ALIASES_PATH, load_name_aliases
from market.name_truncation import is_truncated_display_name
from market.truncated_storage import (
    DEFAULT_TRUNCATED_ITEMS_PATH,
    load_truncated_store,
    normalize_list_prefix,
)
from market.variant_catalog import VariantCatalog, normalize_display_name

# ``Recipe: Draconic Bow (6`` — cut before closing ``)``
_INCOMPLETE_PAREN = re.compile(r"\(\d+$")


@dataclass(frozen=True)
class _KnownEntry:
    display: str
    item_id: str
    source: str
    tier: str


@dataclass(frozen=True)
class CanonicalNameHit:
    display_name: str
    item_id: str
    truncated_visible: str | None = None
    match_source: str = "prefix"
    status: str = CANONICAL_UNIQUE_PREFIX
    source: str = ""
    tier: str = ""
    trusted_hint: str = TRUSTED_HINT_YES


@dataclass(frozen=True)
class CanonicalResolutionResult:
    input_name: str
    status: str
    display_name: str | None = None
    item_id: str | None = None
    source: str | None = None
    tier: str | None = None
    trusted_hint: str = TRUSTED_HINT_NO
    candidates: tuple[tuple[str, str, str], ...] = field(default_factory=tuple)
    suggestion: str | None = None

    @property
    def trusted(self) -> bool:
        return self.trusted_hint == TRUSTED_HINT_YES


def _is_truncated_catalog_label(name: str) -> bool:
    """Skip truncated OCR labels when building the canonical name index."""
    if is_truncated_display_name(name):
        return True
    n = name.strip()
    if n.startswith("Recipe:") and not n.endswith(")"):
        return True
    if _INCOMPLETE_PAREN.search(n):
        return True
    return False


def _prefix_matches(prefix: str, canonical_norm: str) -> bool:
    if not prefix or not canonical_norm:
        return False
    if canonical_norm.startswith(prefix):
        return len(prefix) >= max(8, int(len(canonical_norm) * 0.5))
    if prefix.startswith(canonical_norm[: min(len(prefix), len(canonical_norm))]):
        return len(prefix) >= 8
    return False


def _trusted_hint(status: str, tier: str | None) -> str:
    if status == UNRESOLVED or status == CANONICAL_AMBIGUOUS_PREFIX:
        return TRUSTED_HINT_NO
    if status == CANONICAL_ALIAS:
        return TRUSTED_HINT_YES
    if tier in HIGH_TRUST_TIERS:
        return TRUSTED_HINT_YES
    if tier == SOURCE_TIER_ITEMS_DB:
        return TRUSTED_HINT_WARNING
    return TRUSTED_HINT_NO


def _tier_rank(tier: str) -> int:
    return SOURCE_TIER_ORDER.get(tier, 99)


class CanonicalNameIndex:
    """Map truncated list prefixes and shorthands → canonical (display_name, item_id)."""

    def __init__(self, known: list[_KnownEntry]) -> None:
        self._known: list[_KnownEntry] = []
        seen: set[tuple[str, str]] = set()
        for entry in known:
            key = (normalize_display_name(entry.display), entry.item_id)
            if key in seen:
                continue
            seen.add(key)
            self._known.append(entry)

        self._exact: dict[str, _KnownEntry] = {}
        for entry in self._known:
            norm = normalize_display_name(entry.display)
            prev = self._exact.get(norm)
            if prev is None or _tier_rank(entry.tier) < _tier_rank(prev.tier):
                self._exact[norm] = entry

        self._by_prefix: dict[str, CanonicalNameHit] = {}
        self._unique_truncated_prefixes: set[str] = set()

    @classmethod
    def load(
        cls,
        *,
        catalog: VariantCatalog | None = None,
        target_lists: Path = DEFAULT_TARGET_LISTS,
        recipes_dir: Path = DEFAULT_RECIPES_DIR,
        truncated_registry: Path = DEFAULT_TRUNCATED_ITEMS_PATH,
        items_database: Path = DEFAULT_ITEMS_DB,
        aliases_path: Path = DEFAULT_ALIASES_PATH,
    ) -> CanonicalNameIndex:
        known: list[_KnownEntry] = []
        seen: set[tuple[str, str, str]] = set()

        def add(display: str, item_id: str, *, source: str, tier: str) -> None:
            display = display.strip()
            if not display:
                return
            if tier != SOURCE_TIER_ALIAS and _is_truncated_catalog_label(display):
                return
            key = (normalize_display_name(display), item_id, tier)
            if key in seen:
                return
            seen.add(key)
            known.append(_KnownEntry(display=display, item_id=item_id, source=source, tier=tier))

        for alias in load_name_aliases(aliases_path):
            add(alias.canonical_name, alias.item_id, source=alias.source, tier=SOURCE_TIER_ALIAS)
            add(alias.alias, alias.item_id, source=alias.source, tier=SOURCE_TIER_ALIAS)

        for recipe in load_all_recipes(recipes_dir=recipes_dir):
            recipe_src = f"recipes/{recipe.recipe_id}.json"
            add(
                recipe.search_name,
                item_id_from_name(recipe.search_name),
                source=recipe_src,
                tier=SOURCE_TIER_RECIPE,
            )
            for comp in recipe.components:
                add(comp.search_name, comp.item_id, source=recipe_src, tier=SOURCE_TIER_RECIPE)
                for q in comp.effective_search_queries():
                    add(q, comp.item_id, source=recipe_src, tier=SOURCE_TIER_RECIPE)

        if target_lists.is_file():
            for ref in load_target_list_refs(target_lists):
                add(ref.search_name, ref.item_id, source="target_lists.yaml", tier=SOURCE_TIER_TARGET)

        cat = catalog or VariantCatalog.load()
        for entry in cat.entries.values():
            item_id = entry.variant_group or entry.item_uid
            if entry.search_query:
                add(entry.search_query, item_id, source="item_variant_catalog.json", tier=SOURCE_TIER_CATALOG)
            if entry.display_name:
                add(entry.display_name, item_id, source="item_variant_catalog.json", tier=SOURCE_TIER_CATALOG)

        if items_database.is_file():
            for db_entry in load_item_entries(items_database):
                add(
                    db_entry.search_name,
                    db_entry.item_id,
                    source="items_database.txt",
                    tier=SOURCE_TIER_ITEMS_DB,
                )

        idx = cls(known)

        if truncated_registry.is_file():
            store = load_truncated_store(truncated_registry)
            store.recompute_identity_classes()
            for entry in store.items.values():
                if entry.identity_class != "unique" or not entry.visible_name:
                    continue
                prefix = entry.list_prefix or normalize_list_prefix(entry.visible_name)
                idx._unique_truncated_prefixes.add(prefix)
                hit = idx.resolve(entry.visible_name)
                if hit is not None:
                    idx._by_prefix[prefix] = hit

        return idx

    def is_truncated_visible(self, name: str | None) -> bool:
        """True when ``name`` is a truncated list-view label worth canonicalizing."""
        if not name:
            return False
        if is_truncated_display_name(name):
            return True
        n = name.strip()
        if n.startswith("Recipe:") and not n.endswith(")"):
            return True
        if _INCOMPLETE_PAREN.search(n):
            return True
        prefix = normalize_list_prefix(n)
        return prefix in self._unique_truncated_prefixes

    def resolve_name(self, visible_name: str) -> CanonicalResolutionResult:
        """Resolve any input — alias, exact, unique prefix, ambiguous, or unresolved."""
        raw = visible_name.strip()
        if not raw:
            return CanonicalResolutionResult(input_name=visible_name, status=UNRESOLVED)

        from market.name_aliases import alias_lookup

        alias = alias_lookup(raw)
        if alias is not None:
            hint = _trusted_hint(CANONICAL_ALIAS, SOURCE_TIER_ALIAS)
            return CanonicalResolutionResult(
                input_name=raw,
                status=CANONICAL_ALIAS,
                display_name=alias.canonical_name,
                item_id=alias.item_id,
                source=alias.source,
                tier=SOURCE_TIER_ALIAS,
                trusted_hint=hint,
            )

        norm = normalize_display_name(raw)
        exact = self._exact.get(norm)
        if exact is not None:
            prefix = normalize_list_prefix(raw)
            longer_prefix = [
                m
                for m in self._prefix_matches_for(raw, prefix)
                if len(normalize_display_name(m.display)) > len(norm)
            ]
            if not longer_prefix:
                status = CANONICAL_EXACT
                hint = _trusted_hint(status, exact.tier)
                return CanonicalResolutionResult(
                    input_name=raw,
                    status=status,
                    display_name=exact.display,
                    item_id=exact.item_id,
                    source=exact.source,
                    tier=exact.tier,
                    trusted_hint=hint,
                )

        if not self._should_try_prefix(raw):
            return CanonicalResolutionResult(
                input_name=raw,
                status=UNRESOLVED,
                suggestion=self._suggest_unresolved(raw),
            )

        prefix = normalize_list_prefix(raw)
        cached = self._by_prefix.get(prefix)
        if cached is not None:
            hint = _trusted_hint(cached.status, cached.tier)
            return CanonicalResolutionResult(
                input_name=raw,
                status=cached.status,
                display_name=cached.display_name,
                item_id=cached.item_id,
                source=cached.source,
                tier=cached.tier,
                trusted_hint=hint,
            )

        matches = self._prefix_matches_for(raw, prefix)
        if not matches:
            return CanonicalResolutionResult(
                input_name=raw,
                status=UNRESOLVED,
                suggestion=self._suggest_unresolved(raw),
            )

        chosen = self._pick_best_entry(raw, matches)
        if chosen is None:
            cands = tuple((m.display, m.item_id, m.source) for m in matches[:12])
            return CanonicalResolutionResult(
                input_name=raw,
                status=CANONICAL_AMBIGUOUS_PREFIX,
                candidates=cands,
                trusted_hint=TRUSTED_HINT_NO,
            )

        status = CANONICAL_UNIQUE_PREFIX
        hint = _trusted_hint(status, chosen.tier)
        hit = CanonicalNameHit(
            display_name=chosen.display,
            item_id=chosen.item_id,
            truncated_visible=raw,
            match_source="prefix",
            status=status,
            source=chosen.source,
            tier=chosen.tier,
            trusted_hint=hint,
        )
        self._by_prefix[prefix] = hit
        return CanonicalResolutionResult(
            input_name=raw,
            status=status,
            display_name=chosen.display,
            item_id=chosen.item_id,
            source=chosen.source,
            tier=chosen.tier,
            trusted_hint=hint,
        )

    def resolve(self, visible_name: str) -> CanonicalNameHit | None:
        """Return canonical name when uniquely resolved (backward compatible)."""
        if not self.is_truncated_visible(visible_name):
            return None
        result = self.resolve_name(visible_name)
        if result.status not in (CANONICAL_UNIQUE_PREFIX, CANONICAL_ALIAS, CANONICAL_EXACT):
            return None
        if result.display_name is None or result.item_id is None:
            return None
        return CanonicalNameHit(
            display_name=result.display_name,
            item_id=result.item_id,
            truncated_visible=visible_name.strip(),
            match_source="prefix" if result.status == CANONICAL_UNIQUE_PREFIX else result.status,
            status=result.status,
            source=result.source or "",
            tier=result.tier or "",
            trusted_hint=result.trusted_hint,
        )

    def _should_try_prefix(self, raw: str) -> bool:
        if self.is_truncated_visible(raw):
            return True
        prefix = normalize_list_prefix(raw)
        if len(prefix) >= 8:
            return True
        return raw.startswith("Recipe:")

    def _prefix_matches_for(self, raw: str, prefix: str) -> list[_KnownEntry]:
        matches: list[_KnownEntry] = []
        for entry in self._known:
            norm = normalize_display_name(entry.display)
            if _prefix_matches(prefix, norm):
                matches.append(entry)
        return matches

    @staticmethod
    def _pick_best_entry(
        visible_name: str,
        matches: list[_KnownEntry],
    ) -> _KnownEntry | None:
        if not matches:
            return None

        distinct_ids = {m.item_id for m in matches}
        if len(distinct_ids) > 1:
            return None

        raw_norm = normalize_display_name(visible_name)
        exact_matches = [m for m in matches if normalize_display_name(m.display) == raw_norm]
        if len(exact_matches) == 1:
            return exact_matches[0]

        if len(matches) == 1:
            return matches[0]

        visible_l = visible_name.casefold()
        if visible_l.startswith("recipe:"):
            recipe = [m for m in matches if m.item_id.startswith("recipe_")]
            if len(recipe) == 1:
                return recipe[0]
            if recipe:
                return max(recipe, key=lambda m: len(m.display))

        by_tier = sorted(matches, key=lambda m: (_tier_rank(m.tier), -len(m.display)))
        best_tier = by_tier[0].tier
        tier_matches = [m for m in by_tier if m.tier == best_tier]

        if len(tier_matches) == 1:
            return tier_matches[0]

        by_len = sorted(tier_matches, key=lambda m: len(m.display), reverse=True)
        if len(by_len) >= 2 and len(by_len[0].display) == len(by_len[1].display):
            return None
        return by_len[0]

    @staticmethod
    def _suggest_unresolved(raw: str) -> str | None:
        text = raw.casefold()
        if text in ("gemstone s", "gemstone a"):
            grade = text[-1].upper()
            return f'add alias "{raw}" → "Gemstone ({grade}-grade)" in config/aliases.yaml'
        return None


def is_likely_ui_truncated(name: str | None) -> bool:
    """Quick check without loading the full index (recipe / ellipsis patterns only)."""
    if not name:
        return False
    return _is_truncated_catalog_label(name)


def format_resolution_report(result: CanonicalResolutionResult) -> str:
    """Human-readable multi-line report for CLI / debug."""
    lines = [f"Input: {result.input_name}"]
    lines.append(f"Status: {result.status}")
    if result.display_name:
        lines.append(f"Resolved: {result.display_name}")
    if result.item_id:
        lines.append(f"Item ID: {result.item_id}")
    if result.source:
        lines.append(f"Source: {result.source}")
    if result.tier:
        lines.append(f"Tier: {result.tier}")
    if result.candidates:
        lines.append("Candidates:")
        for display, item_id, source in result.candidates:
            lines.append(f"  - {display}  ({item_id})  [{source}]")
    if result.suggestion:
        lines.append(f"Suggestion: {result.suggestion}")
    lines.append(f"Trusted: {result.trusted_hint}")
    return "\n".join(lines)

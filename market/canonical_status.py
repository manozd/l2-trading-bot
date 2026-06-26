"""Canonical name resolution statuses and trust hints."""

from __future__ import annotations

CANONICAL_EXACT = "canonical_exact"
CANONICAL_ALIAS = "canonical_alias"
CANONICAL_UNIQUE_PREFIX = "canonical_unique_prefix"
CANONICAL_AMBIGUOUS_PREFIX = "canonical_ambiguous_prefix"
UNRESOLVED = "unresolved"

TRUSTED_HINT_YES = "yes"
TRUSTED_HINT_NO = "no"
TRUSTED_HINT_WARNING = "usable_with_warning"

# Lower number = higher priority when the same display name appears in multiple sources.
SOURCE_TIER_ALIAS = "alias"
SOURCE_TIER_RECIPE = "recipe"
SOURCE_TIER_TARGET = "target"
SOURCE_TIER_CATALOG = "catalog"
SOURCE_TIER_TRUNCATED = "truncated"
SOURCE_TIER_ITEMS_DB = "items_database"

SOURCE_TIER_ORDER: dict[str, int] = {
    SOURCE_TIER_ALIAS: 0,
    SOURCE_TIER_RECIPE: 1,
    SOURCE_TIER_TARGET: 2,
    SOURCE_TIER_CATALOG: 3,
    SOURCE_TIER_TRUNCATED: 4,
    SOURCE_TIER_ITEMS_DB: 5,
}

HIGH_TRUST_TIERS = frozenset(
    {
        SOURCE_TIER_ALIAS,
        SOURCE_TIER_RECIPE,
        SOURCE_TIER_TARGET,
        SOURCE_TIER_CATALOG,
        SOURCE_TIER_TRUNCATED,
    }
)

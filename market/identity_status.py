"""Identity resolution statuses — shared across bulk resolve, catalog, and price aggregation."""

from __future__ import annotations

from typing import Literal

IdentityResolveStatus = Literal[
    "unresolved",
    "search_confirmed",
    "manual_confirmed",
    "icon_name_matched",
    "prefix_icon_matched",
    "exact_icon_name_matched",
    "exact_icon_prefix_matched",
    "fuzzy_icon_name_matched",
    "fuzzy_icon_prefix_matched",
    "fuzzy_icon_candidate",
    "ambiguous_fuzzy_icon",
    "icon_only_candidate",
    "ambiguous_icon",
    "ambiguous_icon_name",
    "ambiguous_prefix_icon",
    "name_candidates",
    # Legacy aliases kept for older rows/tools
    "icon_name_confirmed",
    "matched",
]

TRUSTED_IDENTITY_STATUSES = frozenset(
    {
        "search_confirmed",
        "manual_confirmed",
        "icon_name_matched",
        "prefix_icon_matched",
        "exact_icon_name_matched",
        "exact_icon_prefix_matched",
        "fuzzy_icon_name_matched",
        "fuzzy_icon_prefix_matched",
        # Legacy
        "icon_name_confirmed",
        "matched",
    }
)


def is_trusted_identity(status: str | None) -> bool:
    return status in TRUSTED_IDENTITY_STATUSES

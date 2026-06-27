"""P0.1 — untrusted bulk resolve rows must not carry final item_uid."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from market.resolve_bulk import apply_resolved_identity


def _entry(
    *,
    item_uid: str = "recipe_draconic_bow_60",
    variant_group: str = "recipe_draconic_bow_60",
    display_name: str = "Recipe: Draconic Bow (60%)",
    search_query: str = "Recipe: Draconic Bow (60%)",
    icon_hash: str = "abc:111",
):
    ent = MagicMock()
    ent.item_uid = item_uid
    ent.variant_group = variant_group
    ent.display_name = display_name
    ent.search_query = search_query
    ent.icon_hash = icon_hash
    return ent


class ApplyResolvedIdentityTests(unittest.TestCase):
    def test_untrusted_fuzzy_candidate_clears_final_uid(self) -> None:
        identity = apply_resolved_identity(
            {},
            status="fuzzy_icon_candidate",
            entry=_entry(),
            possible_uids=["recipe_draconic_bow_60"],
            icon_distance=35,
        )
        self.assertFalse(identity["trusted"])
        self.assertIsNone(identity["item_uid"])
        self.assertIsNone(identity["item_id"])
        self.assertIsNone(identity["item_name"])
        self.assertNotIn("catalog_search_query", identity)
        self.assertEqual(identity["possible_item_uids"], ["recipe_draconic_bow_60"])
        self.assertEqual(identity["icon_distance"], 35)

    def test_trusted_exact_match_sets_canonical_fields(self) -> None:
        identity = apply_resolved_identity(
            {},
            status="exact_icon_name_matched",
            entry=_entry(),
            possible_uids=["recipe_draconic_bow_60"],
            icon_distance=0,
        )
        self.assertTrue(identity["trusted"])
        self.assertEqual(identity["item_uid"], "recipe_draconic_bow_60")
        self.assertEqual(identity["catalog_search_query"], "Recipe: Draconic Bow (60%)")

    def test_ambiguous_trusted_status_clears_final_uid(self) -> None:
        identity = apply_resolved_identity(
            {},
            status="ambiguous_icon_name",
            entry=_entry(),
            possible_uids=["a", "b"],
        )
        self.assertFalse(identity["trusted"])
        self.assertIsNone(identity["item_uid"])
        self.assertEqual(identity["possible_item_uids"], ["a", "b"])

    def test_strips_stale_catalog_fields_when_downgrading(self) -> None:
        identity = apply_resolved_identity(
            {
                "item_uid": "recipe_draconic_bow_60",
                "catalog_search_query": "Recipe: Draconic Bow (60%)",
                "canonical_icon_hash": "old:111",
            },
            status="fuzzy_icon_candidate",
            entry=_entry(),
            possible_uids=["recipe_draconic_bow_60"],
        )
        self.assertIsNone(identity["item_uid"])
        self.assertNotIn("catalog_search_query", identity)


if __name__ == "__main__":
    unittest.main()

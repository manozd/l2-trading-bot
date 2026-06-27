"""P0.2 / P0.3 — vendor-name-first resolve and icon contradiction rejection."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from market.resolve_bulk import (
    _icon_contradicts_vendor,
    _is_merged_ocr_name,
    _list_ocr_unreliable,
    _make_name_candidate,
    resolve_observation,
)
from market.variant_catalog import CatalogEntry, VariantCatalog

ROOT = Path(__file__).resolve().parents[1]


def _catalog_entry(
    *,
    item_uid: str,
    display_name: str,
    search_query: str | None = None,
    variant_group: str | None = None,
    icon_hash: str = "abc:111",
    source: str = "search_confirmed",
) -> CatalogEntry:
    return CatalogEntry(
        item_uid=item_uid,
        display_name=display_name,
        normalized_name=display_name.casefold(),
        icon_hash=icon_hash,
        variant_group=variant_group or item_uid,
        search_query=search_query or display_name,
        source=source,
    )


def _mock_catalog(*entries: CatalogEntry) -> VariantCatalog:
    cat = MagicMock(spec=VariantCatalog)
    cat.entries = {e.item_uid: e for e in entries}
    cat.find_by_icon.return_value = []
    cat.fuzzy_icon_candidates.return_value = []
    return cat


class ListOcrReliabilityTests(unittest.TestCase):
    def test_detects_merged_list_label(self) -> None:
        self.assertTrue(
            _is_merged_ocr_name("Blessed Spiritshot (C-g... Red Seal Stone")
        )
        self.assertTrue(
            _is_merged_ocr_name("Sealed Tateossian Ring ... Black Ore Ring Gemstone")
        )

    def test_single_item_not_merged(self) -> None:
        self.assertFalse(_is_merged_ocr_name("Blessed Spiritshot (C-grade)"))
        self.assertFalse(_is_merged_ocr_name("Recipe: Draconic Bow (60%)"))

    def test_null_list_ocr_is_unreliable(self) -> None:
        obs = {"list_context": {"visible_name_ocr": None}, "vendor_rows": []}
        self.assertTrue(_list_ocr_unreliable(obs, []))

    def test_vendor_agrees_with_list_is_reliable(self) -> None:
        obs = {
            "list_context": {"visible_name_ocr": "Gemstone (A-grade)"},
            "vendor_rows": [
                {"raw_text": "Gemstone (A-grade) In stock: 10 units Vendor: X Price per unit: 3 Adena"}
            ],
        }
        vendor_names = ["Gemstone (A-grade)"]
        self.assertFalse(_list_ocr_unreliable(obs, vendor_names))


class IconContradictionTests(unittest.TestCase):
    def test_rejects_recipe_when_vendor_says_gloves(self) -> None:
        recipe = _catalog_entry(
            item_uid="recipe_draconic_bow_60",
            display_name="Recipe: Draconic Bow (60%)",
        )
        gloves = _catalog_entry(
            item_uid="sealed_dark_crystal_gloves",
            display_name="Sealed Dark Crystal Gloves",
        )
        cat = _mock_catalog(recipe, gloves)
        candidate = _make_name_candidate(recipe, name_score=0, prefix=False)
        vendor = ["Sealed Dark Crystal Glo... In stock: i26 units"]
        self.assertTrue(_icon_contradicts_vendor(candidate, vendor, cat))

    def test_keeps_entry_vendor_prefix_matches(self) -> None:
        recipe = _catalog_entry(
            item_uid="recipe_draconic_bow_60",
            display_name="Recipe: Draconic Bow (60%)",
        )
        cat = _mock_catalog(recipe)
        candidate = _make_name_candidate(recipe, name_score=95, prefix=True)
        vendor = ["Recipe: Draconic Bow (60%) In stock: 1 units"]
        self.assertFalse(_icon_contradicts_vendor(candidate, vendor, cat))


class ResolveObservationIntegrationTests(unittest.TestCase):
    def test_kosyan_row_rejects_wrong_recipe_guess(self) -> None:
        recipe = _catalog_entry(
            item_uid="recipe_draconic_bow_60",
            display_name="Recipe: Draconic Bow (60%)",
            icon_hash="wrong:111",
        )
        cat = _mock_catalog(recipe)
        cat.fuzzy_icon_candidates.return_value = [(recipe, 35)]

        obs = {
            "list_context": {
                "visible_name_ocr": None,
                "icon_hash": "5000500050015041121f1cef137f125b17ff15bf15bf151b153712cf164f1:111",
            },
            "vendor_rows": [
                {
                    "raw_text": "Sealed Dark Crystal Glo... In stock: i26 units Vendor: Kosyan Price per unit: 23,887 Adena"
                }
            ],
        }
        status, entry, possible_uids, _dist = resolve_observation(obs, cat)
        self.assertEqual(status, "unresolved")
        self.assertIsNone(entry)
        self.assertNotIn("recipe_draconic_bow_60", possible_uids)

    def test_tateossian_row_rejects_icon_fuzzy_guesses(self) -> None:
        leather = _catalog_entry(
            item_uid="draconic_leather_armor__icon_4d3015cff563e56b",
            display_name="Draconic Leather Armor",
            variant_group="draconic_leather_armor",
        )
        gem = _catalog_entry(
            item_uid="gemstone_a",
            display_name="Gemstone (A-grade)",
            variant_group="gemstone_a",
        )
        cat = _mock_catalog(leather, gem)
        cat.fuzzy_icon_candidates.return_value = [(leather, 40), (gem, 42)]

        obs = {
            "list_context": {
                "visible_name_ocr": "Sealed Tateossian Ring ... Black Ore Ring Gemstone",
                "icon_hash": "1100050005001131e57de5cfe567e533e593e5b7e599e599e51fe59fe57ee5:111",
            },
            "vendor_rows": [
                {"raw_text": "Sealed Tateossian Ring ... In stock: 4 units Vendor: WTSELL Price per unit: 7,000 Adena"},
                {"raw_text": "Sealed Tateossian Ring ... In stock: 62 units Vendor: Icl Price per unit: 8,699 Adena"},
            ],
        }
        status, entry, possible_uids, _dist = resolve_observation(obs, cat)
        self.assertEqual(status, "unresolved")
        self.assertIsNone(entry)
        self.assertEqual(possible_uids, [])

    def test_black_ore_recipe_not_trusted_as_draconic_bow(self) -> None:
        recipe = _catalog_entry(
            item_uid="recipe_draconic_bow_60",
            display_name="Recipe: Draconic Bow (6",
            search_query="Recipe: Draconic Bow (60%)",
        )
        cat = _mock_catalog(recipe)
        cat.fuzzy_icon_candidates.return_value = [(recipe, 20)]

        obs = {
            "list_context": {
                "visible_name_ocr": "Recipe: Black Ore Ring",
                "icon_hash": "500050015c1e55ce52de583e533659365b365b765b3656365cbe5c6e59ce5:111",
            },
            "vendor_rows": [
                {"raw_text": "Recipe: Black Ore Ring In stock: 9 units Vendor: CrafterMan Price per unit: 195,000 Adena"},
            ],
        }
        status, entry, possible_uids, _dist = resolve_observation(obs, cat)
        self.assertEqual(status, "unresolved")
        self.assertIsNone(entry)
        self.assertNotIn("recipe_draconic_bow_60", possible_uids)

    def test_merged_list_not_flagged_for_black_ore_ring(self) -> None:
        self.assertFalse(_is_merged_ocr_name("Recipe: Black Ore Ring"))
        self.assertFalse(_is_merged_ocr_name("Black Ore Earring"))

    def test_vendor_name_first_when_list_merged_and_catalog_match(self) -> None:
        gem = _catalog_entry(
            item_uid="gemstone_a",
            display_name="Gemstone (A-grade)",
            variant_group="gemstone_a",
        )
        cat = _mock_catalog(gem)
        obs = {
            "list_context": {
                "visible_name_ocr": "Blessed Spiritshot (C-g... Red Seal Stone",
                "icon_hash": "deadbeef:111",
            },
            "vendor_rows": [
                {"raw_text": "Gemstone (A-grade) In stock: 10 units Vendor: X Price per unit: 3 Adena"}
            ],
        }
        status, entry, _possible, _dist = resolve_observation(obs, cat)
        self.assertEqual(entry.item_uid, "gemstone_a")
        self.assertIn(status, {"fuzzy_icon_name_matched", "fuzzy_icon_prefix_matched"})


class RealBulkFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.resolved_path = ROOT / "logs" / "market_all_items_resolved.jsonl"
        if not cls.resolved_path.is_file():
            cls.obs_by_id = {}
            return
        cls.obs_by_id = {}
        for line in cls.resolved_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            cid = row.get("bulk_context_id")
            if cid:
                cls.obs_by_id[cid] = row

    def test_line_p2_r6_merged_list_stays_unresolved_without_catalog(self) -> None:
        obs = self.obs_by_id.get("20260627-784c59f9:p2:r6")
        if obs is None:
            self.skipTest("fixture row not in resolved jsonl")
        raw = obs.get("list_context", {}).get("visible_name_ocr") or ""
        self.assertIn("Red Seal Stone", raw)
        vendor_names = [
            (vr.get("raw_text") or "").split(" In stock:", 1)[0]
            for vr in obs.get("vendor_rows") or []
        ]
        self.assertTrue(all("Blessed Spiritshot" in v for v in vendor_names))


if __name__ == "__main__":
    unittest.main()

"""P0.4 — user-facing prices from grouped trusted CSV only."""

from __future__ import annotations

import unittest

from market.trusted_prices import (
    AVAILABILITY_NOT_FOUND,
    GroupedTrustedPriceRow,
    SOURCE_BULK,
    SOURCE_M2,
)
from market.user_prices import (
    collapse_user_price_rows,
    filter_user_prices,
    format_user_price_line,
    grouped_row_to_user,
    is_user_displayable_name,
    source_label,
)


def _row(
    *,
    group_key: str,
    display_name: str,
    variant_group: str | None = None,
    fungible: bool = False,
    min_price: int = 100,
    source: str = SOURCE_M2,
    is_stale: bool = False,
    availability: str = "available",
) -> GroupedTrustedPriceRow:
    return GroupedTrustedPriceRow(
        group_key=group_key,
        variant_group=variant_group or group_key,
        display_name=display_name,
        fungible=fungible,
        min_price=min_price,
        median_price=min_price,
        vendor="TestVendor",
        units=10,
        source=source,
        identity_status="search_confirmed",
        last_seen_at="2026-06-27T12:00:00+00:00",
        confidence="high",
        selected_source=source,
        is_stale=is_stale,
        availability=availability,
    )


class UserDisplayableNameTests(unittest.TestCase):
    def test_rejects_ocr_glue(self) -> None:
        self.assertFalse(is_user_displayable_name("Vendor: Esika Min. price per 1: 100 Adena"))
        self.assertFalse(is_user_displayable_name("On market: 64 units"))

    def test_accepts_catalog_name(self) -> None:
        self.assertTrue(is_user_displayable_name("Recipe: Draconic Bow (60%)"))
        self.assertTrue(is_user_displayable_name("Gemstone (A-grade)"))


class CollapseUserPriceRowsTests(unittest.TestCase):
    def test_collapses_gear_variants_to_one_row(self) -> None:
        rows = [
            _row(group_key="bow_a", display_name="Draconic Bow +0", variant_group="draconic_bow", min_price=600_000_000, is_stale=True),
            _row(group_key="bow_b", display_name="Draconic Bow +0", variant_group="draconic_bow", min_price=580_000_000),
        ]
        out = collapse_user_price_rows(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].min_price, 580_000_000)

    def test_keeps_fungible_rows(self) -> None:
        rows = [
            _row(group_key="gemstone_a", display_name="Gemstone (A-grade)", fungible=True),
            _row(group_key="gemstone_s", display_name="Gemstone (S-grade)", fungible=True),
        ]
        self.assertEqual(len(collapse_user_price_rows(rows)), 2)

    def test_all_variants_shows_every_gear_row(self) -> None:
        rows = [
            _row(group_key="bow_a", display_name="Draconic Bow +0", variant_group="draconic_bow", min_price=600_000_000),
            _row(group_key="bow_b", display_name="Draconic Bow +0", variant_group="draconic_bow", min_price=580_000_000),
        ]
        self.assertEqual(len(collapse_user_price_rows(rows, all_variants=True)), 2)


class FormatUserPriceLineTests(unittest.TestCase):
    def test_formats_available_row(self) -> None:
        user = grouped_row_to_user(_row(group_key="recipe", display_name="Recipe: Draconic Bow (60%)"))
        line = format_user_price_line(user)
        self.assertIn("Recipe: Draconic Bow (60%)", line)
        self.assertIn("100 adena", line)
        self.assertIn("M+2", line)
        self.assertIn("high", line)

    def test_formats_not_found(self) -> None:
        user = grouped_row_to_user(
            _row(
                group_key="missing",
                display_name="Durable Metal Plate",
                availability=AVAILABILITY_NOT_FOUND,
                min_price=0,
            )
        )
        self.assertIn("not on market", format_user_price_line(user))


class SourceLabelTests(unittest.TestCase):
    def test_bulk_source(self) -> None:
        row = _row(group_key="x", display_name="Crystal (A-grade)", source=SOURCE_BULK)
        self.assertEqual(source_label(row), "bulk")


class FilterUserPricesTests(unittest.TestCase):
    def test_name_filter(self) -> None:
        rows = [
            grouped_row_to_user(_row(group_key="a", display_name="Gemstone (A-grade)", fungible=True)),
            grouped_row_to_user(_row(group_key="b", display_name="Draconic Bow +0")),
        ]
        hits = filter_user_prices(rows, name_query="Draconic Bow")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].display_name, "Draconic Bow +0")


if __name__ == "__main__":
    unittest.main()

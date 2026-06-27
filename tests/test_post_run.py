"""P0.5 — post-run trusted rollup hooks."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from market.post_run import run_post_m2_hooks, run_trusted_prices_rollup

ROOT = Path(__file__).resolve().parents[1]


class PostRunHooksTests(unittest.TestCase):
    def test_trusted_rollup_on_fixture_files(self) -> None:
        bulk = ROOT / "logs" / "market_all_items_resolved.jsonl"
        search = ROOT / "logs" / "market_search_prices.jsonl"
        if not bulk.is_file() or not search.is_file():
            self.skipTest("fixture logs missing")
        result = run_trusted_prices_rollup(
            bulk_resolved_path=bulk,
            search_prices_path=search,
            out_jsonl=ROOT / "logs" / "_test_trusted.jsonl",
            out_csv=ROOT / "logs" / "_test_trusted.csv",
            out_grouped_csv=ROOT / "logs" / "_test_trusted_grouped.csv",
        )
        self.assertGreater(result.item_uid_count, 0)
        self.assertGreater(result.grouped_count, 0)
        for path in (result.trusted_jsonl, result.trusted_csv, result.grouped_csv):
            path.unlink(missing_ok=True)

    def test_m2_hook_skips_missing_search_jsonl(self) -> None:
        with patch("market.post_run.run_trusted_prices_rollup") as rollup:
            out = run_post_m2_hooks(
                search_prices_path=Path("logs/does_not_exist_search.jsonl"),
            )
        self.assertIsNone(out)
        rollup.assert_not_called()


if __name__ == "__main__":
    unittest.main()

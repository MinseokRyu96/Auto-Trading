from __future__ import annotations

import unittest

from gostop.strategy import MomentumStrategy, StrategyConfig


class SmallAccountAllocationTest(unittest.TestCase):
    def test_small_account_concentrates_into_top_candidates(self) -> None:
        strategy = object.__new__(MomentumStrategy)
        strategy.config = StrategyConfig()
        selected = [
            candidate("000001", 0.90),
            candidate("000002", 0.80),
            candidate("000003", 0.70),
            candidate("000004", 0.60),
            candidate("000005", 0.50),
        ]
        quotes = {item["symbol"]: 10_000 for item in selected}

        targets = strategy._whole_share_target_weights(selected, 1.0, 500_000, quotes, {})

        self.assertEqual(set(targets), {"000001", "000002", "000003"})
        for symbol, weight in targets.items():
            self.assertGreaterEqual(weight * 500_000, strategy.config.small_account_min_position_value)
            self.assertGreaterEqual(round(weight * 500_000 / quotes[symbol]), 5)

    def test_small_account_skips_candidates_that_are_too_large_for_position_cap(self) -> None:
        strategy = object.__new__(MomentumStrategy)
        strategy.config = StrategyConfig()
        selected = [
            candidate("000001", 0.90),
            candidate("000002", 0.80),
            candidate("000003", 0.70),
            candidate("000004", 0.60),
        ]
        quotes = {
            "000001": 300_000,
            "000002": 10_000,
            "000003": 10_000,
            "000004": 10_000,
        }

        targets = strategy._whole_share_target_weights(selected, 1.0, 500_000, quotes, {})

        self.assertNotIn("000001", targets)
        self.assertEqual(set(targets), {"000002", "000003", "000004"})


def candidate(symbol: str, score: float) -> dict[str, object]:
    return {
        "symbol": symbol,
        "score": score,
        "segment": "core",
        "source": "momentum",
    }


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from gostop.runner import MarketRunner, is_filled_order_event


class OrderNoticeThrottleTest(unittest.TestCase):
    def test_filled_order_events_are_immediate(self) -> None:
        self.assertTrue(is_filled_order_event({"status": "filled"}))
        self.assertTrue(is_filled_order_event({"status": "partial_filled"}))
        self.assertFalse(is_filled_order_event({"status": "submitted"}))

    def test_non_fill_order_events_are_throttled(self) -> None:
        runner = object.__new__(MarketRunner)
        runner.settings = SimpleNamespace(telegram_order_notice_minutes=20)
        runner.notifier = FakeNotifier()
        runner._last_non_fill_order_notice_at = None
        now = datetime(2026, 7, 10, 9, 0, 0)

        runner._send_order_events_with_throttle(now, [{"status": "submitted", "symbol": "005930"}])
        runner._send_order_events_with_throttle(
            now + timedelta(minutes=5),
            [{"status": "open", "symbol": "005930"}],
        )
        runner._send_order_events_with_throttle(
            now + timedelta(minutes=6),
            [{"status": "filled", "symbol": "005930"}],
        )
        runner._send_order_events_with_throttle(
            now + timedelta(minutes=20),
            [{"status": "open", "symbol": "005930"}],
        )

        self.assertEqual(
            [[row["status"] for row in batch] for batch in runner.notifier.batches],
            [["submitted"], ["filled"], ["open"]],
        )


class FakeNotifier:
    def __init__(self) -> None:
        self.batches: list[list[dict[str, object]]] = []

    def send_order_events(self, rows: list[dict[str, object]]) -> None:
        self.batches.append(rows)


if __name__ == "__main__":
    unittest.main()

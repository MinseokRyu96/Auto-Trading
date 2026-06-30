from __future__ import annotations

import unittest

from gostop.account_sync import execution_row, order_status


class AccountSyncParsingTest(unittest.TestCase):
    def test_execution_row_parses_filled_buy(self) -> None:
        row = {
            "ord_dt": "20260630",
            "ord_tmd": "091523",
            "odno": "12345",
            "sll_buy_dvsn_cd": "02",
            "pdno": "005930",
            "prdt_name": "삼성전자",
            "tot_ccld_qty": "3",
            "avg_prvs": "70000",
            "tot_ccld_amt": "210000",
        }

        parsed = execution_row(row, "2026-06-30")

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["side"], "buy")
        self.assertEqual(parsed["quantity"], 3)
        self.assertEqual(parsed["amount"], 210000)
        self.assertEqual(parsed["execution_time"], "2026-06-30T09:15:23+09:00")

    def test_order_status_parses_partial_and_filled(self) -> None:
        self.assertEqual(
            order_status({"ord_qty": "5", "tot_ccld_qty": "2", "rmn_qty": "3"}),
            "partial_filled",
        )
        self.assertEqual(
            order_status({"ord_qty": "5", "tot_ccld_qty": "5", "rmn_qty": "0"}),
            "filled",
        )


if __name__ == "__main__":
    unittest.main()

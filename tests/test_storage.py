from __future__ import annotations

import unittest

from gostop.storage import account_cash_amount


class AccountCashAmountTest(unittest.TestCase):
    def test_prefers_equity_consistent_cash_over_deposit_total(self) -> None:
        row = {
            "dnca_tot_amt": "90950",
            "nxdy_excc_amt": "39595",
            "prvs_rcdl_excc_amt": "39595",
            "scts_evlu_amt": "452700",
            "tot_evlu_amt": "492295",
            "nass_amt": "492295",
        }

        self.assertEqual(account_cash_amount(row), 39595)

    def test_falls_back_to_settlement_cash_when_totals_are_missing(self) -> None:
        row = {
            "dnca_tot_amt": "90950",
            "nxdy_excc_amt": "39595",
            "prvs_rcdl_excc_amt": "39595",
        }

        self.assertEqual(account_cash_amount(row), 39595)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from .config import Settings
from .kis_client import KisClient, KisApiError
from .storage import Store


ORDER_CASH_PATH = "/uapi/domestic-stock/v1/trading/order-cash"


@dataclass(frozen=True)
class ExecutionResult:
    submitted: int
    skipped: int
    mode: str


class TradingExecutor:
    def __init__(self, settings: Settings, client: KisClient, store: Store):
        self.settings = settings
        self.client = client
        self.store = store

    def execute_latest_plan(self, live: bool = False, confirm_live: bool = False) -> ExecutionResult:
        if live:
            self._assert_live_allowed(confirm_live)

        orders = self._load_latest_orders()
        if not orders:
            return ExecutionResult(submitted=0, skipped=0, mode="live" if live else "dry-run")
        if live and not self.store.is_trading_enabled():
            return ExecutionResult(submitted=0, skipped=len(orders), mode="paused")

        total_order_value = 0.0
        submitted = 0
        skipped = 0
        for order in orders:
            if live and self._has_same_day_submission(order):
                self._record_guard_skip(order, "same-day duplicate order blocked")
                skipped += 1
                continue
            order_value = float(order["order_value"] or 0)
            if order_value <= 0:
                skipped += 1
                continue
            if order_value > self.settings.max_single_order_value:
                self._record_guard_skip(order, f"single order limit exceeded: {order_value:.0f}")
                skipped += 1
                continue
            if total_order_value + order_value > self.settings.max_daily_order_value:
                self._record_guard_skip(order, "daily order limit exceeded")
                skipped += 1
                continue
            total_order_value += order_value

            if live:
                self._place_order(order)
            else:
                self._record_dry_run(order)
            submitted += 1

        return ExecutionResult(submitted=submitted, skipped=skipped, mode="live" if live else "dry-run")

    def _has_same_day_submission(self, order: dict[str, Any]) -> bool:
        with self.store.connect() as conn:
            row = conn.execute(
                """
                select 1
                from order_events
                where trade_date = ?
                  and symbol = ?
                  and side = ?
                  and status in ('submitting', 'submitted')
                limit 1
                """,
                (date.today().isoformat(), str(order["symbol"]), str(order["side"])),
            ).fetchone()
            return row is not None

    def _assert_live_allowed(self, confirm_live: bool) -> None:
        if not self.settings.is_real:
            raise RuntimeError("Live execution requires KIS_ENV=real.")
        if not self.settings.allow_live_trading:
            raise RuntimeError("Set GOSTOP_ALLOW_LIVE_TRADING=true to allow live execution.")
        if not confirm_live:
            raise RuntimeError("Live execution requires --confirm-live.")
        if not self.settings.cano or not self.settings.acnt_prdt_cd:
            raise RuntimeError("KIS account number is required for live execution.")

    def _load_latest_orders(self) -> list[dict[str, Any]]:
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                select run_at, as_of_date, strategy_name, symbol, name, side, quantity,
                       price, current_value, target_value, order_value, raw_json
                from rebalance_plan_orders
                where run_at = (select max(run_at) from rebalance_plan_orders)
                order by side desc, order_value desc
                """
            ).fetchall()
        orders: list[dict[str, Any]] = []
        for row in rows:
            order = dict(row)
            raw = order.pop("raw_json", None)
            if raw:
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {}
                order["reason"] = payload.get("reason") or ""
            orders.append(order)
        return orders

    def _place_order(self, order: dict[str, Any]) -> None:
        tr_id = self._tr_id(order["side"])
        body = self._order_body(order)
        self.store.insert_order_event(
            {
                "trade_date": date.today().isoformat(),
                "symbol": order["symbol"],
                "name": order.get("name"),
                "side": order["side"],
                "order_type": "limit",
                "quantity": order["quantity"],
                "price": order["price"],
                "status": "submitting",
                "reason": order.get("reason"),
            }
        )
        try:
            response = self.client.post(ORDER_CASH_PATH, tr_id, body)
        except KisApiError as exc:
            self.store.insert_order_event(
                {
                    "trade_date": date.today().isoformat(),
                    "symbol": order["symbol"],
                    "name": order.get("name"),
                    "side": order["side"],
                    "order_type": "limit",
                    "quantity": order["quantity"],
                    "price": order["price"],
                    "status": f"failed: {exc}",
                    "reason": order.get("reason"),
                }
            )
            raise

        output = response.get("output") or {}
        self.store.save_raw(ORDER_CASH_PATH, tr_id, {"CANO": "***", **{k: v for k, v in body.items() if k != "CANO"}}, response)
        self.store.insert_order_event(
            {
                "trade_date": date.today().isoformat(),
                "order_id": output.get("ODNO"),
                "symbol": order["symbol"],
                "name": order.get("name"),
                "side": order["side"],
                "order_type": "limit",
                "quantity": order["quantity"],
                "price": order["price"],
                "status": "submitted",
                "reason": order.get("reason"),
            }
        )

    def _record_dry_run(self, order: dict[str, Any]) -> None:
        self.store.insert_order_event(
            {
                "trade_date": date.today().isoformat(),
                "symbol": order["symbol"],
                "name": order.get("name"),
                "side": order["side"],
                "order_type": "limit",
                "quantity": order["quantity"],
                "price": order["price"],
                "status": "dry_run",
                "reason": order.get("reason"),
            }
        )

    def _record_guard_skip(self, order: dict[str, Any], reason: str) -> None:
        self.store.insert_order_event(
            {
                "trade_date": date.today().isoformat(),
                "symbol": order["symbol"],
                "name": order.get("name"),
                "side": order["side"],
                "order_type": "limit",
                "quantity": order["quantity"],
                "price": order["price"],
                "status": f"guard_skip: {reason}",
                "reason": order.get("reason"),
            }
        )

    def _tr_id(self, side: str) -> str:
        if self.settings.is_real:
            return "TTTC0012U" if side == "buy" else "TTTC0011U"
        return "VTTC0012U" if side == "buy" else "VTTC0011U"

    def _order_body(self, order: dict[str, Any]) -> dict[str, str]:
        if not self.settings.cano or not self.settings.acnt_prdt_cd:
            raise RuntimeError("KIS account number is required.")
        return {
            "CANO": self.settings.cano,
            "ACNT_PRDT_CD": self.settings.acnt_prdt_cd,
            "PDNO": str(order["symbol"]),
            "ORD_DVSN": "00",
            "ORD_QTY": str(int(float(order["quantity"]))),
            "ORD_UNPR": str(int(float(order["price"]))),
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "01" if order["side"] == "sell" else "",
            "CNDT_PRIC": "",
        }

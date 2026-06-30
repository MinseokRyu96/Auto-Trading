from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .config import Settings
from .kis_client import KisClient
from .storage import Store, normalize_trade_date, to_float


ORDERABLE_PATH = "/uapi/domestic-stock/v1/trading/inquire-psbl-order"
DAILY_CCLD_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"


@dataclass(frozen=True)
class OrderableCash:
    amount: float
    quantity: int
    raw: dict[str, Any]


@dataclass(frozen=True)
class ExecutionSyncResult:
    executions: int
    new_executions: int
    status_events: int


class AccountSync:
    def __init__(self, settings: Settings, client: KisClient, store: Store):
        self.settings = settings
        self.client = client
        self.store = store

    def inquire_orderable_cash(self, symbol: str, price: float, order_type: str = "01") -> OrderableCash:
        if not self.settings.cano or not self.settings.acnt_prdt_cd:
            raise RuntimeError("KIS account number is required.")

        tr_id = "TTTC8908R" if self.settings.is_real else "VTTC8908R"
        params = {
            "CANO": self.settings.cano,
            "ACNT_PRDT_CD": self.settings.acnt_prdt_cd,
            "PDNO": symbol,
            "ORD_UNPR": str(int(price)),
            "ORD_DVSN": order_type,
            "CMA_EVLU_AMT_ICLD_YN": "N",
            "OVRS_ICLD_YN": "N",
        }
        response = self.client.get(ORDERABLE_PATH, tr_id, params)
        self.store.save_raw(ORDERABLE_PATH, tr_id, {**params, "CANO": "***"}, response)
        output = response.get("output") or {}
        if isinstance(output, list):
            output = output[0] if output else {}

        amount = first_number(output, ("nrcvb_buy_amt", "ord_psbl_cash", "max_buy_amt"))
        quantity = int(first_number(output, ("nrcvb_buy_qty", "max_buy_qty", "ord_psbl_qty")) or 0)
        return OrderableCash(amount=max(amount, 0.0), quantity=max(quantity, 0), raw=output)

    def sync_daily_executions(self, trade_date: str | None = None) -> ExecutionSyncResult:
        if not self.settings.cano or not self.settings.acnt_prdt_cd:
            raise RuntimeError("KIS account number is required.")

        normalized = normalize_trade_date(trade_date or date.today().isoformat())
        compact = normalized.replace("-", "")
        tr_id = "TTTC0081R" if self.settings.is_real else "VTTC0081R"
        params = {
            "CANO": self.settings.cano,
            "ACNT_PRDT_CD": self.settings.acnt_prdt_cd,
            "INQR_STRT_DT": compact,
            "INQR_END_DT": compact,
            "SLL_BUY_DVSN_CD": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "INQR_DVSN": "00",
            "INQR_DVSN_3": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
            "EXCG_ID_DVSN_CD": "KRX",
        }
        response = self.client.get(DAILY_CCLD_PATH, tr_id, params)
        self.store.save_raw(DAILY_CCLD_PATH, tr_id, {**params, "CANO": "***"}, response)

        rows = response.get("output1") or []
        if isinstance(rows, dict):
            rows = [rows]

        executions = 0
        new_executions = 0
        status_events = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            status = order_status(row)
            if status:
                status_events += self._record_status_if_changed(row, normalized, status)

            execution = execution_row(row, normalized)
            if not execution:
                continue
            executions += 1
            if self.store.upsert_trade_execution(execution):
                new_executions += 1

        return ExecutionSyncResult(executions=executions, new_executions=new_executions, status_events=status_events)

    def _record_status_if_changed(self, row: dict[str, Any], trade_date: str, status: str) -> int:
        order_id = clean_text(row.get("odno"))
        if not order_id or self.store.latest_order_status(order_id) == status:
            return 0
        symbol = clean_text(row.get("pdno"))
        side = side_from_row(row)
        quantity = first_number(row, ("ord_qty", "tot_ord_qty", "tot_ccld_qty"))
        price = first_number(row, ("ord_unpr", "avg_prvs"))
        if not symbol or not side or quantity <= 0:
            return 0
        self.store.insert_order_event(
            {
                "trade_date": trade_date,
                "order_id": order_id,
                "symbol": symbol,
                "name": clean_text(row.get("prdt_name")) or None,
                "side": side,
                "order_type": clean_text(row.get("ord_dvsn_name")) or "limit",
                "quantity": quantity,
                "price": price,
                "status": status,
                "source": "daily_ccld_sync",
                "raw": row,
            }
        )
        return 1


def execution_row(row: dict[str, Any], trade_date: str) -> dict[str, Any] | None:
    quantity = first_number(row, ("tot_ccld_qty", "ccld_qty"))
    if quantity <= 0:
        return None

    symbol = clean_text(row.get("pdno"))
    side = side_from_row(row)
    price = first_number(row, ("avg_prvs", "ccld_unpr", "ord_unpr"))
    amount = first_number(row, ("tot_ccld_amt", "ccld_amt"))
    if not symbol or not side or price <= 0:
        return None
    if amount <= 0:
        amount = quantity * price

    fee = first_number(row, ("fee", "trad_fee", "prsm_tlex_smtl"))
    tax = first_number(row, ("tax", "tr_tax", "stex_tax"))
    realized_pnl = first_number(row, ("realized_pnl", "rlzt_pfls", "evlu_pfls_amt", "trad_pfls_amt"))
    if side == "sell" and realized_pnl == 0:
        purchase_price = first_number(row, ("pchs_avg_pric", "buy_avg_pric"))
        if purchase_price > 0:
            realized_pnl = quantity * (price - purchase_price) - fee - tax

    return {
        "execution_time": execution_time(row, trade_date),
        "trade_date": trade_date,
        "order_id": clean_text(row.get("odno")) or None,
        "symbol": symbol,
        "name": clean_text(row.get("prdt_name")) or None,
        "side": side,
        "quantity": quantity,
        "price": price,
        "amount": amount,
        "fee": fee,
        "tax": tax,
        "realized_pnl": realized_pnl,
        "source": "daily_ccld_sync",
        "raw": row,
    }


def order_status(row: dict[str, Any]) -> str:
    order_qty = first_number(row, ("ord_qty", "tot_ord_qty"))
    filled_qty = first_number(row, ("tot_ccld_qty", "ccld_qty"))
    remaining_qty = first_number(row, ("rmn_qty",))
    rejected_qty = first_number(row, ("rjct_qty",))
    cancel_flag = clean_text(row.get("cncl_yn")).upper()

    if rejected_qty > 0:
        return "rejected"
    if cancel_flag == "Y" and filled_qty <= 0:
        return "cancelled"
    if filled_qty > 0 and (remaining_qty <= 0 or order_qty <= filled_qty):
        return "filled"
    if filled_qty > 0:
        return "partial_filled"
    if remaining_qty > 0:
        return "open"
    return ""


def side_from_row(row: dict[str, Any]) -> str:
    code = clean_text(row.get("sll_buy_dvsn_cd"))
    name = clean_text(row.get("sll_buy_dvsn_cd_name"))
    if code == "02" or "매수" in name:
        return "buy"
    if code == "01" or "매도" in name:
        return "sell"
    return ""


def execution_time(row: dict[str, Any], trade_date: str) -> str:
    raw_time = clean_text(row.get("ord_tmd"))
    if len(raw_time) >= 6 and raw_time[:6].isdigit():
        return f"{trade_date}T{raw_time[:2]}:{raw_time[2:4]}:{raw_time[4:6]}+09:00"
    return f"{trade_date}T00:00:00+09:00"


def first_number(row: dict[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        value = to_float(row.get(key))
        if value is not None:
            return value
    return 0.0


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()

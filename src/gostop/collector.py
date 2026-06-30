from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .kis_client import KisClient
from .storage import Store


@dataclass(frozen=True)
class Endpoint:
    path: str
    tr_id: str


INQUIRE_PRICE = Endpoint("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100")
DAILY_ITEM_CHART = Endpoint("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100")
VOLUME_RANK = Endpoint("/uapi/domestic-stock/v1/quotations/volume-rank", "FHPST01710000")
MARKET_CAP = Endpoint("/uapi/domestic-stock/v1/ranking/market-cap", "FHPST01740000")
CHK_HOLIDAY = Endpoint("/uapi/domestic-stock/v1/quotations/chk-holiday", "CTCA0903R")
INQUIRE_BALANCE_REAL = Endpoint("/uapi/domestic-stock/v1/trading/inquire-balance", "TTTC8434R")
INQUIRE_BALANCE_DEMO = Endpoint("/uapi/domestic-stock/v1/trading/inquire-balance", "VTTC8434R")


class DataCollector:
    def __init__(self, client: KisClient, store: Store, settings: Settings, pause_seconds: float = 0.7):
        self.client = client
        self.store = store
        self.settings = settings
        self.pause_seconds = pause_seconds

    def collect_current_quotes(self, symbols: list[str]) -> int:
        count = 0
        for symbol in symbols:
            params = {
                "FID_COND_MRKT_DIV_CODE": self.settings.market_div,
                "FID_INPUT_ISCD": symbol,
            }
            response = self.client.get(INQUIRE_PRICE.path, INQUIRE_PRICE.tr_id, params)
            self.store.save_raw(INQUIRE_PRICE.path, INQUIRE_PRICE.tr_id, params, response)
            output = response.get("output") or {}
            self.store.insert_current_quote(symbol, output)
            count += 1
            time.sleep(self.pause_seconds)
        return count

    def collect_daily_bars(self, symbols: list[str], start: str, end: str, adjusted: bool = True) -> int:
        total = 0
        for symbol in symbols:
            params = {
                "FID_COND_MRKT_DIV_CODE": self.settings.market_div,
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": start,
                "FID_INPUT_DATE_2": end,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0" if adjusted else "1",
            }
            response = self.client.get(DAILY_ITEM_CHART.path, DAILY_ITEM_CHART.tr_id, params)
            self.store.save_raw(DAILY_ITEM_CHART.path, DAILY_ITEM_CHART.tr_id, params, response)
            total += self.store.upsert_daily_bars(symbol, response.get("output2") or [])
            time.sleep(self.pause_seconds)
        return total

    def collect_volume_rank(self) -> int:
        params = {
            "FID_COND_MRKT_DIV_CODE": self.settings.market_div,
            "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": "0000",
            "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "3",
            "FID_TRGT_CLS_CODE": "111111111",
            "FID_TRGT_EXLS_CLS_CODE": "0000000000",
            "FID_INPUT_PRICE_1": "0",
            "FID_INPUT_PRICE_2": "1000000",
            "FID_VOL_CNT": "0",
            "FID_INPUT_DATE_1": "",
        }
        response = self.client.get(VOLUME_RANK.path, VOLUME_RANK.tr_id, params)
        self.store.save_raw(VOLUME_RANK.path, VOLUME_RANK.tr_id, params, response)
        return self.store.insert_ranking_rows("volume_rank", response.get("output") or [])

    def collect_market_cap(self) -> int:
        params = {
            "fid_input_price_2": "",
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20174",
            "fid_div_cls_code": "0",
            "fid_input_iscd": "0000",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_input_price_1": "",
            "fid_vol_cnt": "",
        }
        response = self.client.get(MARKET_CAP.path, MARKET_CAP.tr_id, params)
        self.store.save_raw(MARKET_CAP.path, MARKET_CAP.tr_id, params, response)
        return self.store.insert_ranking_rows("market_cap", response.get("output") or [])

    def collect_holidays(self, base_date: str) -> int:
        params = {
            "BASS_DT": base_date,
            "CTX_AREA_FK": "",
            "CTX_AREA_NK": "",
        }
        response = self.client.get(CHK_HOLIDAY.path, CHK_HOLIDAY.tr_id, params)
        self.store.save_raw(CHK_HOLIDAY.path, CHK_HOLIDAY.tr_id, params, response)
        output = response.get("output") or []
        if isinstance(output, dict):
            output = [output]
        return self.store.upsert_holidays(output)

    def collect_balance(self) -> int:
        if not self.settings.cano or not self.settings.acnt_prdt_cd:
            raise ValueError("KIS_ACCOUNT_NO or KIS_CANO/KIS_ACNT_PRDT_CD is required for balance snapshots.")

        endpoint = INQUIRE_BALANCE_REAL if self.settings.is_real else INQUIRE_BALANCE_DEMO
        params: dict[str, Any] = {
            "CANO": self.settings.cano,
            "ACNT_PRDT_CD": self.settings.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        response = self.client.get(endpoint.path, endpoint.tr_id, params)
        redacted = {**params, "CANO": "***"}
        self.store.save_raw(endpoint.path, endpoint.tr_id, redacted, response)
        output2 = response.get("output2") or []
        if isinstance(output2, dict):
            output2 = [output2]
        self.store.insert_account_summary(output2)
        return self.store.insert_balance_rows(response.get("output1") or [])

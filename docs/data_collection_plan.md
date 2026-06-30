# Quant Data Collection Plan

This project starts with domestic stock data from Korea Investment & Securities Open API.

## Phase 1 Dataset

| Dataset | KIS endpoint | TR ID | Why it matters |
| --- | --- | --- | --- |
| Current quote | `/uapi/domestic-stock/v1/quotations/inquire-price` | `FHKST01010100` | Intraday snapshot, sanity checks before trading |
| Daily OHLCV | `/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice` | `FHKST03010100` | Backtesting, momentum, volatility, liquidity filters |
| Volume rank | `/uapi/domestic-stock/v1/quotations/volume-rank` | `FHPST01710000` | Universe discovery and liquidity screening |
| Market cap rank | `/uapi/domestic-stock/v1/ranking/market-cap` | `FHPST01740000` | Universe discovery and size filters |
| Trading calendar | `/uapi/domestic-stock/v1/quotations/chk-holiday` | `CTCA0903R` | Scheduler, trading-day checks |
| Account balance | `/uapi/domestic-stock/v1/trading/inquire-balance` | `TTTC8434R` real, `VTTC8434R` demo | Portfolio sync before order generation |

## Useful Phase 2 Dataset

| Dataset | Example directory in official repo |
| --- | --- |
| Investor by stock/day | `examples_llm/domestic_stock/investor_trade_by_stock_daily` |
| Program trading | `examples_llm/domestic_stock/program_trade_by_stock_daily` |
| Index daily chart | `examples_llm/domestic_stock/inquire_daily_indexchartprice` |
| Asking price / expected execution | `examples_llm/domestic_stock/inquire_asking_price_exp_ccn` |
| Market time/status | `examples_llm/domestic_stock/market_time`, `market_status_krx` |

## First Run

1. Copy `.env.example` to `.env`.
2. Fill in `KIS_APP_KEY` and `KIS_APP_SECRET`.
3. Keep `KIS_ENV=demo` until collection and account sync are stable.
4. Run:

```bash
PYTHONPATH=src python3 -m gostop.cli init-db
PYTHONPATH=src python3 -m gostop.cli collect-daily --symbols 005930,000660 --start 20250101
PYTHONPATH=src python3 -m gostop.cli collect-current --symbols 005930,000660
PYTHONPATH=src python3 -m gostop.cli collect-volume-rank
PYTHONPATH=src python3 -m gostop.cli collect-market-cap
```

The collector stores normalized rows plus raw API responses in SQLite.

## Notes

- Do not commit `.env` or token cache files.
- The holiday endpoint should be called sparingly; the official sample warns against frequent calls.
- Daily item chart calls return up to 100 rows per request in the official sample, so long backfills should be chunked later.

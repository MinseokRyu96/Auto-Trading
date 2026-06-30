# GoStop

Korea Investment & Securities Open API based quant trading data collector.

## Setup

```bash
cp .env.example .env
PYTHONPATH=src python3 -m gostop.cli init-db
```

Fill `.env` with your KIS app key and secret. Do not paste secrets into chat or commit them.

## Collect Data

```bash
PYTHONPATH=src python3 -m gostop.cli collect-daily --symbols 005930,000660 --start 20250101
PYTHONPATH=src python3 -m gostop.cli collect-current --symbols 005930,000660
PYTHONPATH=src python3 -m gostop.cli collect-volume-rank
PYTHONPATH=src python3 -m gostop.cli collect-market-cap
```

Account snapshot, after setting `KIS_ACCOUNT_NO`:

```bash
PYTHONPATH=src python3 -m gostop.cli collect-balance
```

Data is saved to `data/gostop.sqlite3` by default.

## Dashboard

Run the local operating dashboard:

```bash
PYTHONPATH=src python3 -m gostop.cli dashboard
```

Then open http://127.0.0.1:8765.

Until automated order execution is connected, you can record fills manually:

```bash
PYTHONPATH=src python3 -m gostop.cli record-trade --symbol 005930 --name "Samsung Electronics" --side buy --quantity 10 --price 75000
PYTHONPATH=src python3 -m gostop.cli record-order --symbol 005930 --side buy --quantity 10 --price 75000 --status filled
```

## Strategy

Research note: `docs/trading_strategy_research.md`

Run the current strategy and save signals plus rebalance previews:

```bash
PYTHONPATH=src python3 -m gostop.cli run-strategy
```

The current implementation is `liquidity_momentum_v1`: liquidity-filtered 20/60 day momentum with drawdown/volatility risk overlay and target-weight rebalancing.

Run one autonomous cycle in dry-run mode:

```bash
PYTHONPATH=src python3 -m gostop.cli autopilot
```

Dry-run records the orders that would have been submitted. Live order submission requires both `GOSTOP_ALLOW_LIVE_TRADING=true` and:

```bash
PYTHONPATH=src python3 -m gostop.cli autopilot --live --confirm-live
```

## End-of-Day Learning Loop

After the market closes, run a daily review cycle:

```bash
PYTHONPATH=src python3 -m gostop.cli end-of-day-review
```

This collects the latest account/market snapshots, refreshes the strategy plan, stores a daily performance review, and records parameter recommendations in the dashboard Learning section. To analyze only the data already saved locally:

```bash
PYTHONPATH=src python3 -m gostop.cli end-of-day-review --skip-collect --skip-strategy
```

Recommendations are not applied to the next strategy run unless explicitly enabled:

```bash
PYTHONPATH=src python3 -m gostop.cli end-of-day-review --apply-suggestions --min-confidence 0.70
```

Applied recommendations are stored as adaptive strategy overrides and loaded automatically by future `run-strategy` and `autopilot` runs that do not pass an explicit strategy config.

## Telegram Notifications

Set these values in `.env` to receive runner updates and order notifications:

```bash
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

Send a test message:

```bash
PYTHONPATH=src python3 -m gostop.cli test-telegram --message "[GoStop] test"
```

The market runner sends start, cycle summary, submitted/skipped order, and error updates when Telegram is configured.

## Sources

- KIS Developers: https://apiportal.koreainvestment.com
- Official sample repository: https://github.com/koreainvestment/open-trading-api

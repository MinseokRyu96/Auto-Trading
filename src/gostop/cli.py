from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from pathlib import Path

from .collector import DataCollector
from .config import load_settings
from .dashboard import run_dashboard
from .eod_review import EndOfDayReview
from .execution import TradingExecutor
from .kis_client import KisClient
from .news import NewsCrawler
from .notify import TelegramNotifier
from .runner import MarketRunner, MarketRunnerConfig
from .storage import Store
from .strategy import MomentumStrategy, StrategyConfig


def main() -> None:
    parser = argparse.ArgumentParser(prog="gostop", description="KIS Open API data collector")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db")

    p_current = sub.add_parser("collect-current")
    p_current.add_argument("--symbols", default="")
    p_current.add_argument("--symbols-file", default="config/symbols.csv")

    p_daily = sub.add_parser("collect-daily")
    p_daily.add_argument("--symbols", default="")
    p_daily.add_argument("--symbols-file", default="config/symbols.csv")
    p_daily.add_argument("--start", required=True)
    p_daily.add_argument("--end", default=date.today().strftime("%Y%m%d"))
    p_daily.add_argument("--raw-price", action="store_true")

    sub.add_parser("collect-volume-rank")
    sub.add_parser("collect-market-cap")
    p_news = sub.add_parser("collect-news")
    p_news.add_argument("--symbols-file", default="config/symbols.csv")

    p_holiday = sub.add_parser("collect-holidays")
    p_holiday.add_argument("--base-date", default=date.today().strftime("%Y%m%d"))

    sub.add_parser("collect-balance")

    p_all = sub.add_parser("collect-all")
    p_all.add_argument("--symbols", default="")
    p_all.add_argument("--symbols-file", default="config/symbols.csv")
    p_all.add_argument("--days", type=int, default=120)

    p_trade = sub.add_parser("record-trade")
    p_trade.add_argument("--symbol", required=True)
    p_trade.add_argument("--side", required=True, choices=["buy", "sell", "매수", "매도"])
    p_trade.add_argument("--quantity", required=True, type=float)
    p_trade.add_argument("--price", required=True, type=float)
    p_trade.add_argument("--name", default="")
    p_trade.add_argument("--fee", default=0, type=float)
    p_trade.add_argument("--tax", default=0, type=float)
    p_trade.add_argument("--realized-pnl", default=0, type=float)
    p_trade.add_argument("--order-id", default="")
    p_trade.add_argument("--time", default="")
    p_trade.add_argument("--date", default="")

    p_order = sub.add_parser("record-order")
    p_order.add_argument("--symbol", required=True)
    p_order.add_argument("--side", required=True, choices=["buy", "sell", "매수", "매도"])
    p_order.add_argument("--quantity", required=True, type=float)
    p_order.add_argument("--price", default=None, type=float)
    p_order.add_argument("--status", required=True)
    p_order.add_argument("--name", default="")
    p_order.add_argument("--order-type", default="")
    p_order.add_argument("--order-id", default="")
    p_order.add_argument("--time", default="")
    p_order.add_argument("--date", default="")

    p_dashboard = sub.add_parser("dashboard")
    p_dashboard.add_argument("--host", default="127.0.0.1")
    p_dashboard.add_argument("--port", default=8765, type=int)

    p_telegram = sub.add_parser("test-telegram")
    p_telegram.add_argument("--message", default="[GoStop] 텔레그램 알림 테스트")

    p_strategy = sub.add_parser("run-strategy")
    p_strategy.add_argument("--capital", default=None, type=float)
    p_strategy.add_argument("--max-positions", default=5, type=int)
    p_strategy.add_argument("--gross-exposure", default=0.70, type=float)
    p_strategy.add_argument("--max-position-weight", default=0.20, type=float)
    p_strategy.add_argument("--min-order-amount", default=50_000, type=float)
    p_strategy.add_argument("--min-avg-trade-value", default=1_000_000_000, type=float)
    p_strategy.add_argument("--max-annual-volatility", default=0.85, type=float)
    p_strategy.add_argument("--risk-off-return-20d", default=-0.06, type=float)
    p_strategy.add_argument("--crash-return-20d", default=-0.12, type=float)
    p_strategy.add_argument("--risk-off-drawdown-60d", default=-0.10, type=float)
    p_strategy.add_argument("--crash-drawdown-60d", default=-0.18, type=float)

    p_execute = sub.add_parser("execute-plan")
    p_execute.add_argument("--live", action="store_true")
    p_execute.add_argument("--confirm-live", action="store_true")

    p_autopilot = sub.add_parser("autopilot")
    p_autopilot.add_argument("--symbols", default="")
    p_autopilot.add_argument("--symbols-file", default="config/symbols.csv")
    p_autopilot.add_argument("--days", type=int, default=120)
    p_autopilot.add_argument("--capital", default=None, type=float)
    p_autopilot.add_argument("--live", action="store_true")
    p_autopilot.add_argument("--confirm-live", action="store_true")
    p_autopilot.add_argument("--skip-collect", action="store_true")

    p_runner = sub.add_parser("market-runner")
    p_runner.add_argument("--symbols-file", default="config/symbols.csv")
    p_runner.add_argument("--interval-minutes", type=int, default=15)
    p_runner.add_argument("--sleep-seconds", type=int, default=60)
    p_runner.add_argument("--start-time", default="09:05")
    p_runner.add_argument("--end-time", default="15:20")
    p_runner.add_argument("--live", action="store_true")
    p_runner.add_argument("--confirm-live", action="store_true")
    p_runner.add_argument("--once", action="store_true")

    p_eod = sub.add_parser("end-of-day-review")
    p_eod.add_argument("--symbols", default="")
    p_eod.add_argument("--symbols-file", default="config/symbols.csv")
    p_eod.add_argument("--date", default=date.today().isoformat())
    p_eod.add_argument("--days", type=int, default=140)
    p_eod.add_argument("--skip-collect", action="store_true")
    p_eod.add_argument("--skip-strategy", action="store_true")
    p_eod.add_argument("--apply-suggestions", action="store_true")
    p_eod.add_argument("--min-confidence", type=float, default=0.70)

    args = parser.parse_args()
    settings = load_settings()
    store = Store(settings.db_path)
    store.init()
    client = KisClient(settings)

    if args.command == "init-db":
        print(f"initialized {settings.db_path}")
        return

    collector = DataCollector(client, store, settings)

    if args.command == "collect-current":
        symbols = read_symbols(args.symbols, args.symbols_file)
        print(f"current_quotes={collector.collect_current_quotes(symbols)}")
    elif args.command == "collect-daily":
        symbols = read_symbols(args.symbols, args.symbols_file)
        count = collector.collect_daily_bars(symbols, args.start, args.end, adjusted=not args.raw_price)
        print(f"daily_bars={count}")
    elif args.command == "collect-volume-rank":
        print(f"volume_rank_rows={collector.collect_volume_rank()}")
    elif args.command == "collect-market-cap":
        print(f"market_cap_rows={collector.collect_market_cap()}")
    elif args.command == "collect-news":
        result = NewsCrawler(settings, store).collect(args.symbols_file)
        print(f"news_queries={result.queries} news_rows={result.rows}")
    elif args.command == "collect-holidays":
        print(f"holiday_rows={collector.collect_holidays(args.base_date)}")
    elif args.command == "collect-balance":
        print(f"balance_rows={collector.collect_balance()}")
    elif args.command == "collect-all":
        symbols = read_symbols(args.symbols, args.symbols_file)
        end = date.today()
        start = end - timedelta(days=args.days)
        print(f"daily_bars={collector.collect_daily_bars(symbols, start.strftime('%Y%m%d'), end.strftime('%Y%m%d'))}")
        print(f"current_quotes={collector.collect_current_quotes(symbols)}")
        print(f"volume_rank_rows={collector.collect_volume_rank()}")
        print(f"market_cap_rows={collector.collect_market_cap()}")
    elif args.command == "record-trade":
        store.insert_trade_execution(
            {
                "execution_time": args.time or None,
                "trade_date": args.date or None,
                "order_id": args.order_id or None,
                "symbol": args.symbol,
                "name": args.name or None,
                "side": args.side,
                "quantity": args.quantity,
                "price": args.price,
                "fee": args.fee,
                "tax": args.tax,
                "realized_pnl": args.realized_pnl,
            }
        )
        print("recorded trade")
    elif args.command == "record-order":
        store.insert_order_event(
            {
                "event_time": args.time or None,
                "trade_date": args.date or None,
                "order_id": args.order_id or None,
                "symbol": args.symbol,
                "name": args.name or None,
                "side": args.side,
                "order_type": args.order_type or None,
                "quantity": args.quantity,
                "price": args.price,
                "status": args.status,
            }
        )
        print("recorded order")
    elif args.command == "dashboard":
        run_dashboard(host=args.host, port=args.port)
    elif args.command == "test-telegram":
        notifier = TelegramNotifier.from_settings(settings)
        if not notifier.enabled:
            raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env first.")
        sent = notifier.send(args.message)
        print(f"telegram_sent={sent}")
    elif args.command == "run-strategy":
        strategy = MomentumStrategy(
            settings,
            store,
            StrategyConfig(
                max_positions=args.max_positions,
                gross_exposure=args.gross_exposure,
                max_position_weight=args.max_position_weight,
                min_order_amount=args.min_order_amount,
                min_avg_trade_value=args.min_avg_trade_value,
                max_annual_volatility=args.max_annual_volatility,
                risk_off_return_20d=args.risk_off_return_20d,
                crash_return_20d=args.crash_return_20d,
                risk_off_drawdown_60d=args.risk_off_drawdown_60d,
                crash_drawdown_60d=args.crash_drawdown_60d,
            ),
        )
        plan = strategy.build_plan(capital=args.capital)
        print(
            "strategy={strategy} as_of={as_of} regime={regime} exposure={exposure:.2f} signals={signals} orders={orders}".format(
                strategy=plan["strategy_name"],
                as_of=plan["as_of_date"] or "n/a",
                regime=plan["risk_state"]["regime"],
                exposure=plan["risk_state"]["exposure_multiplier"],
                signals=len(plan["signals"]),
                orders=len(plan["orders"]),
            )
        )
    elif args.command == "execute-plan":
        executor = TradingExecutor(settings, client, store)
        result = executor.execute_latest_plan(live=args.live, confirm_live=args.confirm_live)
        print(f"execution mode={result.mode} submitted={result.submitted} skipped={result.skipped}")
    elif args.command == "end-of-day-review":
        symbols = read_symbols(args.symbols, args.symbols_file)
        review_day = parse_cli_date(args.date)
        if not args.skip_collect:
            start = review_day - timedelta(days=args.days)
            print(f"collect_balance={collector.collect_balance()}")
            print(f"collect_current={collector.collect_current_quotes(symbols)}")
            print(f"collect_daily={collector.collect_daily_bars(symbols, start.strftime('%Y%m%d'), review_day.strftime('%Y%m%d'))}")
            print(f"volume_rank_rows={collector.collect_volume_rank()}")
            print(f"market_cap_rows={collector.collect_market_cap()}")
        if not args.skip_strategy:
            plan = MomentumStrategy(settings, store).build_plan()
            print(
                "strategy={strategy} as_of={as_of} regime={regime} exposure={exposure:.2f} signals={signals} orders={orders}".format(
                    strategy=plan["strategy_name"],
                    as_of=plan["as_of_date"] or "n/a",
                    regime=plan["risk_state"]["regime"],
                    exposure=plan["risk_state"]["exposure_multiplier"],
                    signals=len(plan["signals"]),
                    orders=len(plan["orders"]),
                )
            )
        result = EndOfDayReview(store).run(
            review_day.isoformat(),
            apply_suggestions=args.apply_suggestions,
            min_confidence=args.min_confidence,
        )
        review = result["review"]
        print(
            "eod_review date={date} quality={quality:.1f} suggestions={suggestions} applied={applied} summary={summary}".format(
                date=review["trade_date"],
                quality=review["quality_score"],
                suggestions=len(result["suggestions"]),
                applied=len(result["applied"]),
                summary=review["summary"],
            )
        )
    elif args.command == "autopilot":
        symbols = read_symbols(args.symbols, args.symbols_file)
        if not args.skip_collect:
            end = date.today()
            start = end - timedelta(days=args.days)
            print(f"collect_balance={collector.collect_balance()}")
            print(f"collect_current={collector.collect_current_quotes(symbols)}")
            print(f"collect_daily={collector.collect_daily_bars(symbols, start.strftime('%Y%m%d'), end.strftime('%Y%m%d'))}")
        strategy = MomentumStrategy(settings, store)
        plan = strategy.build_plan(capital=args.capital)
        print(
            "strategy={strategy} as_of={as_of} regime={regime} exposure={exposure:.2f} signals={signals} orders={orders}".format(
                strategy=plan["strategy_name"],
                as_of=plan["as_of_date"] or "n/a",
                regime=plan["risk_state"]["regime"],
                exposure=plan["risk_state"]["exposure_multiplier"],
                signals=len(plan["signals"]),
                orders=len(plan["orders"]),
            )
        )
        executor = TradingExecutor(settings, client, store)
        result = executor.execute_latest_plan(live=args.live, confirm_live=args.confirm_live)
        print(f"execution mode={result.mode} submitted={result.submitted} skipped={result.skipped}")
    elif args.command == "market-runner":
        runner = MarketRunner(
            settings,
            client,
            store,
            MarketRunnerConfig(
                live=args.live,
                confirm_live=args.confirm_live,
                symbols_file=args.symbols_file,
                interval_minutes=args.interval_minutes,
                sleep_seconds=args.sleep_seconds,
                start_time=args.start_time,
                end_time=args.end_time,
                once=args.once,
            ),
        )
        runner.run_forever()


def read_symbols(symbols_arg: str, symbols_file: str) -> list[str]:
    if symbols_arg:
        return [item.strip() for item in symbols_arg.split(",") if item.strip()]

    path = Path(symbols_file)
    with path.open(newline="", encoding="utf-8") as fp:
        return [row["symbol"].strip() for row in csv.DictReader(fp) if row.get("symbol")]


def parse_cli_date(value: str) -> date:
    text = value.strip()
    if len(text) == 8 and text.isdigit():
        return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}")
    return date.fromisoformat(text)


if __name__ == "__main__":
    main()

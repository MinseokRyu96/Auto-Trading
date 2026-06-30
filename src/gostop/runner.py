from __future__ import annotations

import csv
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .account_sync import AccountSync, ExecutionSyncResult
from .collector import DataCollector
from .config import Settings
from .execution import TradingExecutor
from .kis_client import KisClient
from .news import NewsCrawler
from .notify import TelegramNotifier
from .storage import Store, utc_now
from .strategy import MomentumStrategy


KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class MarketRunnerConfig:
    live: bool = False
    confirm_live: bool = False
    symbols_file: str = "config/symbols.csv"
    interval_minutes: int = 15
    sleep_seconds: int = 60
    start_time: str = "09:05"
    end_time: str = "15:20"
    once: bool = False


class MarketRunner:
    def __init__(
        self,
        settings: Settings,
        client: KisClient,
        store: Store,
        config: MarketRunnerConfig,
    ):
        self.settings = settings
        self.client = client
        self.store = store
        self.config = config
        self.collector = DataCollector(client, store, settings, pause_seconds=0.35)
        self.news_crawler = NewsCrawler(settings, store)
        self.executor = TradingExecutor(settings, client, store)
        self.account_sync = AccountSync(settings, client, store)
        self.notifier = TelegramNotifier.from_settings(settings)
        self._last_cycle_at: datetime | None = None
        self._last_rank_date: date | None = None
        self._last_news_at: datetime | None = None

    def run_forever(self) -> None:
        print(
            "market_runner started mode={mode} window={start}-{end} interval={interval}m".format(
                mode="live" if self.config.live else "dry-run",
                start=self.config.start_time,
                end=self.config.end_time,
                interval=self.config.interval_minutes,
            ),
            flush=True,
        )
        self.notifier.send_runner_started(
            self.config.live,
            self.config.start_time,
            self.config.end_time,
            self.config.interval_minutes,
        )
        while True:
            now = datetime.now(KST)
            if not self._is_market_session(now):
                print(f"[{now.isoformat(timespec='seconds')}] market closed; waiting", flush=True)
                if self.config.once:
                    return
                time.sleep(self.config.sleep_seconds)
                continue

            if self._should_run_cycle(now):
                try:
                    self._run_cycle(now)
                except Exception as exc:
                    message = f"cycle failed: {exc}"
                    print(message, flush=True)
                    self.notifier.send_error(message)
                self._last_cycle_at = now
                if self.config.once:
                    return

            time.sleep(self.config.sleep_seconds)

    def _should_run_cycle(self, now: datetime) -> bool:
        if self._last_cycle_at is None:
            return True
        return now - self._last_cycle_at >= timedelta(minutes=self.config.interval_minutes)

    def _run_cycle(self, now: datetime) -> None:
        print(f"[{now.isoformat(timespec='seconds')}] cycle start", flush=True)
        cycle_started_at = utc_now()
        if self._last_rank_date != now.date():
            volume_rows = self.collector.collect_volume_rank()
            market_cap_rows = self.collector.collect_market_cap()
            self._last_rank_date = now.date()
            print(f"rankings volume={volume_rows} market_cap={market_cap_rows}", flush=True)

        pre_sync = self._sync_executions_if_possible(now)
        news_rows = self._collect_news_if_due(now)
        symbols = load_runner_symbols(self.store, self.config.symbols_file)
        balance_rows = self.collector.collect_balance()
        quote_rows = self.collector.collect_current_quotes(symbols)
        plan = MomentumStrategy(self.settings, self.store).build_plan()
        result = self.executor.execute_latest_plan(
            live=self.config.live,
            confirm_live=self.config.confirm_live,
        )
        post_sync = self._sync_executions_if_possible(now)
        account = self._latest_account()
        order_events = self._order_events_since(cycle_started_at)
        print(
            "cycle done balance={balance} quotes={quotes} news={news} executions={executions} new_executions={new_executions} regime={regime} orders={orders} submitted={submitted} skipped={skipped}".format(
                balance=balance_rows,
                quotes=quote_rows,
                news=news_rows,
                executions=(pre_sync.executions + post_sync.executions),
                new_executions=(pre_sync.new_executions + post_sync.new_executions),
                regime=plan["risk_state"]["regime"],
                orders=len(plan["orders"]),
                submitted=result.submitted,
                skipped=result.skipped,
            ),
            flush=True,
        )
        self.notifier.send_cycle_summary(
            {
                "cash_amount": account.get("cash_amount") if account else None,
                "total_eval_amount": account.get("total_eval_amount") if account else None,
                "regime": plan["risk_state"]["regime"],
                "trading_enabled": self.store.is_trading_enabled(),
                "orders": len(plan["orders"]),
                "submitted": result.submitted,
                "skipped": result.skipped,
                "news_rows": news_rows,
                "executions": pre_sync.executions + post_sync.executions,
                "new_executions": pre_sync.new_executions + post_sync.new_executions,
            }
        )
        self.notifier.send_order_events(order_events)

    def _sync_executions_if_possible(self, now: datetime) -> ExecutionSyncResult:
        if not self.settings.cano or not self.settings.acnt_prdt_cd:
            return ExecutionSyncResult(executions=0, new_executions=0, status_events=0)
        try:
            result = self.account_sync.sync_daily_executions(now.date().isoformat())
        except Exception as exc:
            print(f"execution sync failed: {exc}", flush=True)
            return ExecutionSyncResult(executions=0, new_executions=0, status_events=0)
        if result.executions or result.status_events:
            print(
                f"execution sync executions={result.executions} new={result.new_executions} status_events={result.status_events}",
                flush=True,
            )
        return result

    def _collect_news_if_due(self, now: datetime) -> int:
        if not self.settings.news_enabled:
            return 0
        if self._last_news_at is not None:
            elapsed = now - self._last_news_at
            if elapsed < timedelta(minutes=self.settings.news_refresh_minutes):
                return 0
        try:
            result = self.news_crawler.collect(self.config.symbols_file)
        except Exception as exc:
            print(f"news collection failed: {exc}", flush=True)
            return 0
        self._last_news_at = now
        print(f"news queries={result.queries} rows={result.rows}", flush=True)
        return result.rows

    def _latest_account(self) -> dict[str, float] | None:
        with self.store.connect() as conn:
            row = conn.execute(
                """
                select total_eval_amount, net_asset_amount, cash_amount
                from account_snapshots
                order by collected_at desc
                limit 1
                """
            ).fetchone()
        return dict(row) if row else None

    def _order_events_since(self, since: str) -> list[dict[str, object]]:
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                select event_time, symbol, name, side, quantity, price, status, raw_json
                from order_events
                where event_time >= ?
                order by event_time desc
                limit 8
                """,
                (since,),
            ).fetchall()
        events: list[dict[str, object]] = []
        for row in rows:
            event = dict(row)
            raw = event.pop("raw_json", None)
            if raw:
                try:
                    payload = json.loads(str(raw))
                except json.JSONDecodeError:
                    payload = {}
                event["reason"] = payload.get("reason") or ""
            events.append(event)
        return events

    def _is_market_session(self, now: datetime) -> bool:
        if now.weekday() >= 5:
            return False
        if self._is_holiday(now.date()):
            return False
        return parse_hhmm(self.config.start_time) <= now.time() <= parse_hhmm(self.config.end_time)

    def _is_holiday(self, day: date) -> bool:
        keys = (day.isoformat(), day.strftime("%Y%m%d"))
        with self.store.connect() as conn:
            row = conn.execute(
                """
                select open_yn, business_day_yn
                from holidays
                where date in (?, ?)
                limit 1
                """,
                keys,
            ).fetchone()
        if row is None:
            return False
        open_yn = str(row["open_yn"] or "").upper()
        business_day_yn = str(row["business_day_yn"] or "").upper()
        return open_yn in {"N", "0", "FALSE"} or business_day_yn in {"N", "0", "FALSE"}


def parse_hhmm(value: str) -> clock_time:
    hour, minute = value.split(":", 1)
    return clock_time(int(hour), int(minute))


def load_runner_symbols(store: Store, symbols_file: str) -> list[str]:
    symbols = set(load_symbols_file(symbols_file))
    with store.connect() as conn:
        rows = conn.execute(
            """
            select distinct symbol
            from current_quotes
            where symbol is not null
            union
            select distinct symbol
            from strategy_signals
            where run_at = (select max(run_at) from strategy_signals)
              and symbol is not null
            union
            select distinct symbol
            from ranking_snapshots
            where collected_at in (
                select max(collected_at)
                from ranking_snapshots
                group by source
            )
              and symbol is not null
            """
        ).fetchall()
    for row in rows:
        symbol = str(row["symbol"])
        if symbol.isdigit():
            symbols.add(symbol)
    return sorted(symbols)


def load_symbols_file(path: str | Path) -> list[str]:
    csv_path = Path(path)
    if not csv_path.exists():
        return []
    with csv_path.open(newline="", encoding="utf-8") as fp:
        return [row["symbol"].strip() for row in csv.DictReader(fp) if row.get("symbol")]

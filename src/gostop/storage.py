from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma journal_mode=WAL")
        conn.execute("pragma busy_timeout=30000")
        conn.execute("pragma foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                create table if not exists raw_api_responses (
                    id integer primary key autoincrement,
                    collected_at text not null,
                    endpoint text not null,
                    tr_id text not null,
                    request_json text not null,
                    response_json text not null
                );

                create table if not exists current_quotes (
                    collected_at text not null,
                    symbol text not null,
                    last_price real,
                    change_rate real,
                    accumulated_volume real,
                    raw_json text not null,
                    primary key (collected_at, symbol)
                );

                create table if not exists daily_bars (
                    symbol text not null,
                    trade_date text not null,
                    open real,
                    high real,
                    low real,
                    close real,
                    volume real,
                    trade_value real,
                    raw_json text not null,
                    primary key (symbol, trade_date)
                );

                create table if not exists ranking_snapshots (
                    collected_at text not null,
                    source text not null,
                    rank_no integer,
                    symbol text,
                    name text,
                    price real,
                    volume real,
                    trade_value real,
                    market_cap real,
                    raw_json text not null
                );

                create table if not exists index_bars (
                    index_code text not null,
                    trade_date text not null,
                    open real,
                    high real,
                    low real,
                    close real,
                    volume real,
                    raw_json text not null,
                    primary key (index_code, trade_date)
                );

                create table if not exists holidays (
                    date text primary key,
                    open_yn text,
                    business_day_yn text,
                    raw_json text not null
                );

                create table if not exists balance_snapshots (
                    collected_at text not null,
                    symbol text,
                    name text,
                    quantity real,
                    avg_price real,
                    eval_amount real,
                    pnl real,
                    raw_json text not null
                );

                create table if not exists account_snapshots (
                    collected_at text not null,
                    total_eval_amount real,
                    net_asset_amount real,
                    cash_amount real,
                    securities_eval_amount real,
                    purchase_amount real,
                    valuation_pnl real,
                    today_buy_amount real,
                    today_sell_amount real,
                    raw_json text not null
                );

                create table if not exists order_events (
                    event_time text not null,
                    trade_date text not null,
                    order_id text,
                    symbol text not null,
                    name text,
                    side text not null,
                    order_type text,
                    quantity real not null,
                    price real,
                    status text not null,
                    raw_json text not null
                );

                create table if not exists trade_executions (
                    execution_time text not null,
                    trade_date text not null,
                    order_id text,
                    symbol text not null,
                    name text,
                    side text not null,
                    quantity real not null,
                    price real not null,
                    amount real not null,
                    fee real default 0,
                    tax real default 0,
                    realized_pnl real default 0,
                    raw_json text not null
                );

                create table if not exists strategy_signals (
                    run_at text not null,
                    as_of_date text not null,
                    strategy_name text not null,
                    symbol text not null,
                    name text,
                    score real not null,
                    target_weight real not null,
                    action text not null,
                    reason text not null,
                    raw_json text not null
                );

                create table if not exists rebalance_plan_orders (
                    run_at text not null,
                    as_of_date text not null,
                    strategy_name text not null,
                    symbol text not null,
                    name text,
                    side text not null,
                    quantity real not null,
                    price real not null,
                    current_value real not null,
                    target_value real not null,
                    order_value real not null,
                    raw_json text not null
                );

                create table if not exists strategy_risk_states (
                    run_at text not null,
                    as_of_date text not null,
                    strategy_name text not null,
                    regime text not null,
                    exposure_multiplier real not null,
                    market_return_20d real,
                    market_drawdown_60d real,
                    market_volatility_20d real,
                    reason text not null,
                    raw_json text not null
                );

                create table if not exists daily_learning_reviews (
                    review_at text not null,
                    trade_date text not null,
                    strategy_name text not null,
                    account_start real,
                    account_end real,
                    account_delta real,
                    realized_pnl real,
                    buy_amount real,
                    sell_amount real,
                    trade_count integer,
                    win_rate real,
                    target_count integer,
                    target_avg_return real,
                    watch_avg_return real,
                    signal_excess_return real,
                    risk_regime text,
                    exposure_multiplier real,
                    quality_score real,
                    summary text not null,
                    recommendations_json text not null,
                    raw_json text not null,
                    primary key (trade_date, strategy_name)
                );

                create table if not exists strategy_parameter_suggestions (
                    review_at text not null,
                    trade_date text not null,
                    strategy_name text not null,
                    parameter text not null,
                    current_value real,
                    suggested_value real,
                    reason text not null,
                    confidence real not null,
                    applied integer default 0,
                    raw_json text not null
                );

                create table if not exists strategy_parameter_overrides (
                    strategy_name text not null,
                    parameter text not null,
                    value real not null,
                    applied_at text not null,
                    source_trade_date text not null,
                    reason text not null,
                    raw_json text not null,
                    primary key (strategy_name, parameter)
                );

                create table if not exists runtime_controls (
                    key text primary key,
                    value text not null,
                    updated_at text not null
                );

                create table if not exists stock_news (
                    collected_at text not null,
                    published_at text,
                    symbol text,
                    name text,
                    title text not null,
                    source text,
                    url text not null,
                    query text,
                    sentiment real not null default 0,
                    reason text,
                    raw_json text not null,
                    unique(url, symbol)
                );

                create index if not exists idx_order_events_order_id
                    on order_events(order_id, event_time);

                create index if not exists idx_order_events_trade_symbol_side
                    on order_events(trade_date, symbol, side, status);

                create index if not exists idx_trade_executions_trade_order
                    on trade_executions(trade_date, order_id, symbol, side);

                create index if not exists idx_account_snapshots_collected_at
                    on account_snapshots(collected_at);

                create index if not exists idx_balance_snapshots_collected_at
                    on balance_snapshots(collected_at);
                """
            )

    def get_runtime_control(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute(
                "select value from runtime_controls where key = ?",
                (key,),
            ).fetchone()
        return str(row["value"]) if row else default

    def set_runtime_control(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into runtime_controls (key, value, updated_at)
                values (?, ?, ?)
                on conflict(key) do update set
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, utc_now()),
            )

    def is_trading_enabled(self) -> bool:
        return self.get_runtime_control("trading_enabled", "true").lower() == "true"

    def set_trading_enabled(self, enabled: bool) -> None:
        self.set_runtime_control("trading_enabled", "true" if enabled else "false")

    def save_raw(self, endpoint: str, tr_id: str, request_payload: dict[str, Any], response: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into raw_api_responses
                (collected_at, endpoint, tr_id, request_json, response_json)
                values (?, ?, ?, ?, ?)
                """,
                (
                    utc_now(),
                    endpoint,
                    tr_id,
                    json.dumps(request_payload, ensure_ascii=False),
                    json.dumps(response, ensure_ascii=False),
                ),
            )

    def upsert_daily_bars(self, symbol: str, rows: Iterable[dict[str, Any]]) -> int:
        count = 0
        with self.connect() as conn:
            for row in rows:
                trade_date = row.get("stck_bsop_date") or row.get("bsop_date")
                if not trade_date:
                    continue
                conn.execute(
                    """
                    insert or replace into daily_bars
                    (symbol, trade_date, open, high, low, close, volume, trade_value, raw_json)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol,
                        trade_date,
                        to_float(row.get("stck_oprc")),
                        to_float(row.get("stck_hgpr")),
                        to_float(row.get("stck_lwpr")),
                        to_float(row.get("stck_clpr")),
                        to_float(row.get("acml_vol")),
                        to_float(row.get("acml_tr_pbmn")),
                        json.dumps(row, ensure_ascii=False),
                    ),
                )
                count += 1
        return count

    def insert_current_quote(self, symbol: str, row: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert or replace into current_quotes
                (collected_at, symbol, last_price, change_rate, accumulated_volume, raw_json)
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now(),
                    symbol,
                    to_float(row.get("stck_prpr")),
                    to_float(row.get("prdy_ctrt")),
                    to_float(row.get("acml_vol")),
                    json.dumps(row, ensure_ascii=False),
                ),
            )

    def insert_ranking_rows(self, source: str, rows: Iterable[dict[str, Any]]) -> int:
        collected_at = utc_now()
        count = 0
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    insert into ranking_snapshots
                    (collected_at, source, rank_no, symbol, name, price, volume, trade_value, market_cap, raw_json)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        collected_at,
                        source,
                        to_int(row.get("data_rank") or row.get("rank")),
                        row.get("mksc_shrn_iscd") or row.get("stck_shrn_iscd"),
                        row.get("hts_kor_isnm") or row.get("stck_prpr_name"),
                        to_float(row.get("stck_prpr")),
                        to_float(row.get("acml_vol")),
                        to_float(row.get("acml_tr_pbmn")),
                        to_float(row.get("stck_avls")),
                        json.dumps(row, ensure_ascii=False),
                    ),
                )
                count += 1
        return count

    def upsert_holidays(self, rows: Iterable[dict[str, Any]]) -> int:
        count = 0
        with self.connect() as conn:
            for row in rows:
                date = row.get("bass_dt") or row.get("bzdy")
                if not date:
                    continue
                conn.execute(
                    """
                    insert or replace into holidays (date, open_yn, business_day_yn, raw_json)
                    values (?, ?, ?, ?)
                    """,
                    (
                        date,
                        row.get("opnd_yn"),
                        row.get("bzdy_yn"),
                        json.dumps(row, ensure_ascii=False),
                    ),
                )
                count += 1
        return count

    def insert_balance_rows(self, rows: Iterable[dict[str, Any]]) -> int:
        collected_at = utc_now()
        count = 0
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    insert into balance_snapshots
                    (collected_at, symbol, name, quantity, avg_price, eval_amount, pnl, raw_json)
                    values (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        collected_at,
                        row.get("pdno"),
                        row.get("prdt_name"),
                        to_float(row.get("hldg_qty")),
                        to_float(row.get("pchs_avg_pric")),
                        to_float(row.get("evlu_amt")),
                        to_float(row.get("evlu_pfls_amt")),
                        json.dumps(row, ensure_ascii=False),
                    ),
                )
                count += 1
        return count

    def insert_account_summary(self, rows: Iterable[dict[str, Any]]) -> int:
        collected_at = utc_now()
        count = 0
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    insert into account_snapshots
                    (collected_at, total_eval_amount, net_asset_amount, cash_amount,
                     securities_eval_amount, purchase_amount, valuation_pnl,
                     today_buy_amount, today_sell_amount, raw_json)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        collected_at,
                        to_float(row.get("tot_evlu_amt")),
                        to_float(row.get("nass_amt")),
                        to_float(row.get("dnca_tot_amt")),
                        to_float(row.get("scts_evlu_amt")),
                        to_float(row.get("pchs_amt_smtl_amt")),
                        to_float(row.get("evlu_pfls_smtl_amt")),
                        to_float(row.get("thdt_buy_amt")),
                        to_float(row.get("thdt_sll_amt")),
                        json.dumps(row, ensure_ascii=False),
                    ),
                )
                count += 1
        return count

    def insert_news_rows(self, rows: Iterable[dict[str, Any]]) -> int:
        collected_at = utc_now()
        count = 0
        with self.connect() as conn:
            for row in rows:
                url = str(row.get("url") or "")
                title = str(row.get("title") or "")
                if not url or not title:
                    continue
                conn.execute(
                    """
                    insert or ignore into stock_news
                    (collected_at, published_at, symbol, name, title, source, url,
                     query, sentiment, reason, raw_json)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        collected_at,
                        row.get("published_at"),
                        row.get("symbol"),
                        row.get("name"),
                        title,
                        row.get("source"),
                        url,
                        row.get("query"),
                        to_float(row.get("sentiment")) or 0,
                        row.get("reason") or "",
                        json.dumps(row, ensure_ascii=False),
                    ),
                )
                count += 1
        return count

    def insert_order_event(self, row: dict[str, Any]) -> None:
        event_time = row.get("event_time") or utc_now()
        trade_date = normalize_trade_date(row.get("trade_date") or event_time)
        raw = json.dumps(row, ensure_ascii=False)
        with self.connect() as conn:
            conn.execute(
                """
                insert into order_events
                (event_time, trade_date, order_id, symbol, name, side, order_type, quantity, price, status, raw_json)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_time,
                    trade_date,
                    row.get("order_id"),
                    str(row["symbol"]),
                    row.get("name"),
                    normalize_side(row["side"]),
                    row.get("order_type"),
                    to_float(row["quantity"]) or 0,
                    to_float(row.get("price")),
                    str(row.get("status") or "submitted"),
                    raw,
                ),
            )

    def insert_trade_execution(self, row: dict[str, Any]) -> None:
        execution_time = row.get("execution_time") or utc_now()
        trade_date = normalize_trade_date(row.get("trade_date") or execution_time)
        quantity = to_float(row["quantity"]) or 0
        price = to_float(row["price"]) or 0
        amount = to_float(row.get("amount"))
        if amount is None:
            amount = quantity * price
        raw = json.dumps(row, ensure_ascii=False)
        with self.connect() as conn:
            conn.execute(
                """
                insert into trade_executions
                (execution_time, trade_date, order_id, symbol, name, side, quantity, price, amount, fee, tax, realized_pnl, raw_json)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_time,
                    trade_date,
                    row.get("order_id"),
                    str(row["symbol"]),
                    row.get("name"),
                    normalize_side(row["side"]),
                    quantity,
                    price,
                    amount,
                    to_float(row.get("fee")) or 0,
                    to_float(row.get("tax")) or 0,
                    to_float(row.get("realized_pnl")) or 0,
                    raw,
                ),
            )

    def upsert_trade_execution(self, row: dict[str, Any]) -> bool:
        execution_time = row.get("execution_time") or utc_now()
        trade_date = normalize_trade_date(row.get("trade_date") or execution_time)
        order_id = row.get("order_id")
        symbol = str(row["symbol"])
        side = normalize_side(row["side"])
        quantity = to_float(row["quantity"]) or 0
        price = to_float(row["price"]) or 0
        amount = to_float(row.get("amount"))
        if amount is None:
            amount = quantity * price
        raw = json.dumps(row, ensure_ascii=False)

        with self.connect() as conn:
            existing = None
            if order_id:
                existing = conn.execute(
                    """
                    select raw_json
                    from trade_executions
                    where trade_date = ?
                      and order_id = ?
                      and symbol = ?
                      and side = ?
                    limit 1
                    """,
                    (trade_date, order_id, symbol, side),
                ).fetchone()
                conn.execute(
                    """
                    delete from trade_executions
                    where trade_date = ?
                      and order_id = ?
                      and symbol = ?
                      and side = ?
                    """,
                    (trade_date, order_id, symbol, side),
                )

            conn.execute(
                """
                insert into trade_executions
                (execution_time, trade_date, order_id, symbol, name, side, quantity, price, amount, fee, tax, realized_pnl, raw_json)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_time,
                    trade_date,
                    order_id,
                    symbol,
                    row.get("name"),
                    side,
                    quantity,
                    price,
                    amount,
                    to_float(row.get("fee")) or 0,
                    to_float(row.get("tax")) or 0,
                    to_float(row.get("realized_pnl")) or 0,
                    raw,
                ),
            )
        return existing is None

    def latest_order_status(self, order_id: str) -> str | None:
        if not order_id:
            return None
        with self.connect() as conn:
            row = conn.execute(
                """
                select status
                from order_events
                where order_id = ?
                order by event_time desc
                limit 1
                """,
                (order_id,),
            ).fetchone()
        return str(row["status"]) if row else None

    def replace_strategy_plan(
        self,
        strategy_name: str,
        as_of_date: str,
        signals: Iterable[dict[str, Any]],
        orders: Iterable[dict[str, Any]],
        risk_state: dict[str, Any] | None = None,
    ) -> tuple[int, int]:
        run_at = utc_now()
        signal_count = 0
        order_count = 0
        with self.connect() as conn:
            conn.execute(
                "delete from strategy_signals where strategy_name = ? and as_of_date = ?",
                (strategy_name, as_of_date),
            )
            conn.execute(
                "delete from rebalance_plan_orders where strategy_name = ? and as_of_date = ?",
                (strategy_name, as_of_date),
            )
            conn.execute(
                "delete from strategy_risk_states where strategy_name = ? and as_of_date = ?",
                (strategy_name, as_of_date),
            )
            if risk_state:
                payload = {**risk_state, "run_at": run_at}
                conn.execute(
                    """
                    insert into strategy_risk_states
                    (run_at, as_of_date, strategy_name, regime, exposure_multiplier,
                     market_return_20d, market_drawdown_60d, market_volatility_20d,
                     reason, raw_json)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_at,
                        as_of_date,
                        strategy_name,
                        risk_state.get("regime") or "unknown",
                        to_float(risk_state.get("exposure_multiplier")) or 0,
                        to_float(risk_state.get("market_return_20d")),
                        to_float(risk_state.get("market_drawdown_60d")),
                        to_float(risk_state.get("market_volatility_20d")),
                        risk_state.get("reason") or "",
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
            for signal in signals:
                payload = {**signal, "run_at": run_at}
                conn.execute(
                    """
                    insert into strategy_signals
                    (run_at, as_of_date, strategy_name, symbol, name, score, target_weight, action, reason, raw_json)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_at,
                        as_of_date,
                        strategy_name,
                        signal["symbol"],
                        signal.get("name"),
                        to_float(signal.get("score")) or 0,
                        to_float(signal.get("target_weight")) or 0,
                        signal.get("action") or "hold",
                        signal.get("reason") or "",
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
                signal_count += 1
            for order in orders:
                payload = {**order, "run_at": run_at}
                conn.execute(
                    """
                    insert into rebalance_plan_orders
                    (run_at, as_of_date, strategy_name, symbol, name, side, quantity, price,
                     current_value, target_value, order_value, raw_json)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_at,
                        as_of_date,
                        strategy_name,
                        order["symbol"],
                        order.get("name"),
                        normalize_side(order["side"]),
                        to_float(order.get("quantity")) or 0,
                        to_float(order.get("price")) or 0,
                        to_float(order.get("current_value")) or 0,
                        to_float(order.get("target_value")) or 0,
                        to_float(order.get("order_value")) or 0,
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
                order_count += 1
        return signal_count, order_count

    def replace_daily_learning_review(
        self,
        trade_date: str,
        strategy_name: str,
        review: dict[str, Any],
        suggestions: Iterable[dict[str, Any]],
    ) -> int:
        review_at = utc_now()
        normalized_date = normalize_trade_date(trade_date)
        suggestion_rows = list(suggestions)
        with self.connect() as conn:
            conn.execute(
                "delete from daily_learning_reviews where trade_date = ? and strategy_name = ?",
                (normalized_date, strategy_name),
            )
            conn.execute(
                "delete from strategy_parameter_suggestions where trade_date = ? and strategy_name = ?",
                (normalized_date, strategy_name),
            )
            conn.execute(
                """
                insert into daily_learning_reviews
                (review_at, trade_date, strategy_name, account_start, account_end, account_delta,
                 realized_pnl, buy_amount, sell_amount, trade_count, win_rate, target_count,
                 target_avg_return, watch_avg_return, signal_excess_return, risk_regime,
                 exposure_multiplier, quality_score, summary, recommendations_json, raw_json)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_at,
                    normalized_date,
                    strategy_name,
                    to_float(review.get("account_start")),
                    to_float(review.get("account_end")),
                    to_float(review.get("account_delta")),
                    to_float(review.get("realized_pnl")) or 0,
                    to_float(review.get("buy_amount")) or 0,
                    to_float(review.get("sell_amount")) or 0,
                    int(to_float(review.get("trade_count")) or 0),
                    to_float(review.get("win_rate")) or 0,
                    int(to_float(review.get("target_count")) or 0),
                    to_float(review.get("target_avg_return")),
                    to_float(review.get("watch_avg_return")),
                    to_float(review.get("signal_excess_return")),
                    review.get("risk_regime"),
                    to_float(review.get("exposure_multiplier")),
                    to_float(review.get("quality_score")) or 0,
                    review.get("summary") or "",
                    json.dumps(review.get("recommendations") or [], ensure_ascii=False),
                    json.dumps({**review, "review_at": review_at}, ensure_ascii=False),
                ),
            )
            for suggestion in suggestion_rows:
                payload = {**suggestion, "review_at": review_at}
                conn.execute(
                    """
                    insert into strategy_parameter_suggestions
                    (review_at, trade_date, strategy_name, parameter, current_value,
                     suggested_value, reason, confidence, applied, raw_json)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review_at,
                        normalized_date,
                        strategy_name,
                        str(suggestion["parameter"]),
                        to_float(suggestion.get("current_value")),
                        to_float(suggestion.get("suggested_value")),
                        suggestion.get("reason") or "",
                        to_float(suggestion.get("confidence")) or 0,
                        1 if suggestion.get("applied") else 0,
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
        return len(suggestion_rows)

    def apply_parameter_suggestions(
        self,
        trade_date: str,
        strategy_name: str,
        suggestions: Iterable[dict[str, Any]],
        min_confidence: float = 0.70,
    ) -> list[str]:
        applied_at = utc_now()
        normalized_date = normalize_trade_date(trade_date)
        applied: list[str] = []
        with self.connect() as conn:
            for suggestion in suggestions:
                parameter = str(suggestion.get("parameter") or "")
                suggested_value = to_float(suggestion.get("suggested_value"))
                confidence = to_float(suggestion.get("confidence")) or 0
                if not parameter or parameter == "no_change" or suggested_value is None:
                    continue
                if confidence < min_confidence:
                    continue
                payload = {**suggestion, "applied_at": applied_at}
                conn.execute(
                    """
                    insert or replace into strategy_parameter_overrides
                    (strategy_name, parameter, value, applied_at, source_trade_date, reason, raw_json)
                    values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        strategy_name,
                        parameter,
                        suggested_value,
                        applied_at,
                        normalized_date,
                        suggestion.get("reason") or "",
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
                applied.append(parameter)
        return applied

    def load_parameter_overrides(self, strategy_name: str) -> dict[str, float]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select parameter, value
                from strategy_parameter_overrides
                where strategy_name = ?
                """,
                (strategy_name,),
            ).fetchall()
        return {str(row["parameter"]): float(row["value"]) for row in rows}


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except ValueError:
        return None


def normalize_trade_date(value: Any) -> str:
    text = str(value)
    if "T" in text:
        text = text.split("T", 1)[0]
    if " " in text:
        text = text.split(" ", 1)[0]
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def normalize_side(value: Any) -> str:
    side = str(value).strip().lower()
    if side in {"b", "buy", "매수"}:
        return "buy"
    if side in {"s", "sell", "매도"}:
        return "sell"
    raise ValueError("side must be buy/sell")

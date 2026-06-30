from __future__ import annotations

import json
import sqlite3
import csv
from contextlib import closing
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import parse

from .account_sync import AccountSync
from .collector import DataCollector
from .config import load_settings
from .kis_client import KisClient
from .notify import TelegramNotifier
from .storage import Store
from .strategy import MomentumStrategy


STATIC_DIR = Path(__file__).with_name("dashboard_static")


def run_dashboard(host: str = "127.0.0.1", port: int = 8765) -> None:
    settings = load_settings()
    store = Store(settings.db_path)
    store.init()

    class Handler(DashboardHandler):
        db_path = settings.db_path

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"GoStop dashboard: http://{host}:{port}")
    print(f"SQLite DB: {settings.db_path}")
    server.serve_forever()


class DashboardHandler(SimpleHTTPRequestHandler):
    db_path: Path

    def translate_path(self, path: str) -> str:
        clean_path = parse.urlparse(path).path
        if clean_path == "/":
            clean_path = "/index.html"
        if clean_path.startswith("/static/"):
            clean_path = clean_path.removeprefix("/static")
        return str(STATIC_DIR / clean_path.lstrip("/"))

    def do_GET(self) -> None:
        url = parse.urlparse(self.path)
        if url.path == "/api/overview":
            params = parse.parse_qs(url.query)
            trade_date = params.get("date", [date.today().isoformat()])[0]
            self.send_json(build_overview(self.db_path, trade_date))
            return
        if url.path == "/health":
            self.send_json({"ok": True})
            return
        super().do_GET()

    def do_POST(self) -> None:
        url = parse.urlparse(self.path)
        if url.path == "/api/refresh":
            params = parse.parse_qs(url.query)
            trade_date = params.get("date", [date.today().isoformat()])[0]
            try:
                result = refresh_live_data(self.db_path)
                payload = build_overview(self.db_path, trade_date)
                payload["refresh"] = result
                self.send_json(payload)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=500)
            return
        if url.path == "/api/trading-control":
            try:
                payload = self.read_json_body()
                enabled = bool(payload.get("enabled"))
                store = Store(self.db_path)
                store.init()
                store.set_trading_enabled(enabled)
                settings = load_settings()
                TelegramNotifier.from_settings(settings).send(
                    f"[GoStop] {'🟢 매매기능 ON' if enabled else '🛑 매매기능 OFF'}\n"
                    f"{'자동 주문 제출을 다시 허용했습니다.' if enabled else '자동 주문 제출을 중지했습니다. 시세/전략 갱신은 계속됩니다.'}"
                )
                self.send_json({"ok": True, "trading_enabled": enabled})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=500)
            return
        self.send_json({"ok": False, "error": "not found"}, status=404)

    def end_headers(self) -> None:
        self.send_header("cache-control", "no-store")
        super().end_headers()

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[dashboard] {format % args}")


def build_overview(db_path: Path, trade_date: str) -> dict[str, Any]:
    settings = load_settings()
    store = Store(db_path)
    store.init()
    trading_enabled = store.is_trading_enabled()
    symbol_names = load_symbol_names()
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        symbol_names.update(load_ranking_symbol_names(conn))
        summary = query_one(
            conn,
            """
            select
                count(*) as trade_count,
                coalesce(sum(case when side = 'buy' then amount else 0 end), 0) as buy_amount,
                coalesce(sum(case when side = 'sell' then amount else 0 end), 0) as sell_amount,
                coalesce(sum(fee), 0) as fee,
                coalesce(sum(tax), 0) as tax,
                coalesce(sum(realized_pnl), 0) as realized_pnl,
                coalesce(sum(case when realized_pnl > 0 then 1 else 0 end), 0) as wins
            from trade_executions
            where trade_date = ?
            """,
            (trade_date,),
        )
        trades = query_all(
            conn,
            """
            select execution_time, trade_date, order_id, symbol, name, side, quantity, price,
                   amount, fee, tax, realized_pnl
            from trade_executions
            where trade_date = ?
            order by execution_time desc
            limit 200
            """,
            (trade_date,),
        )
        orders = query_all(
            conn,
            """
            select event_time, trade_date, order_id, symbol, name, side, order_type,
                   quantity, price, status, json_extract(raw_json, '$.reason') as reason
            from order_events
            where trade_date = ?
            order by event_time desc
            limit 200
            """,
            (trade_date,),
        )
        realized_estimated = False
        if not trades:
            derived_trades = derive_trade_rows_from_orders(orders)
            if derived_trades:
                trades = derived_trades
                summary = summarize_trade_rows(trades)
                realized_estimated = True
        holdings = query_all(
            conn,
            """
            select symbol, name, quantity, avg_price, eval_amount, pnl, collected_at
            from balance_snapshots
            where collected_at = (select max(collected_at) from balance_snapshots)
              and coalesce(quantity, 0) != 0
            order by eval_amount desc
            """,
        )
        account = query_optional(
            conn,
            """
            select collected_at, total_eval_amount, net_asset_amount, cash_amount,
                   securities_eval_amount, purchase_amount, valuation_pnl,
                   today_buy_amount, today_sell_amount
            from account_snapshots
            order by collected_at desc
            limit 1
            """,
        )
        quote_rows = query_all(
            conn,
            """
            select q.symbol, q.last_price, q.change_rate, q.accumulated_volume, q.collected_at
            from current_quotes q
            join (
                select symbol, max(collected_at) as collected_at
                from current_quotes
                group by symbol
            ) latest
            on q.symbol = latest.symbol and q.collected_at = latest.collected_at
            order by q.symbol
            """,
        )
        pnl_series = query_all(
            conn,
            """
            select trade_date, round(sum(realized_pnl), 2) as realized_pnl
            from trade_executions
            group by trade_date
            order by trade_date desc
            limit 30
            """,
        )
        if realized_estimated and not pnl_series:
            pnl_series = [{"trade_date": trade_date, "realized_pnl": summary["realized_pnl"]}]
        equity_series = query_all(
            conn,
            """
            select collected_at,
                   round(coalesce(total_eval_amount, net_asset_amount, cash_amount, 0), 2) as equity,
                   round(coalesce(securities_eval_amount, 0), 2) as securities_value,
                   round(coalesce(cash_amount, 0), 2) as cash_amount
            from account_snapshots
            order by collected_at desc
            limit 60
            """,
        )
        rankings = {
            "volume_rank": latest_ranking(conn, "volume_rank"),
            "market_cap": latest_ranking(conn, "market_cap"),
        }
        strategy_signals = query_all(
            conn,
            """
            select run_at, as_of_date, strategy_name, symbol, name, score,
                   target_weight, action, reason
            from strategy_signals
            where run_at = (select max(run_at) from strategy_signals)
            order by
                case action
                    when 'target' then 0
                    when 'exit' then 1
                    else 2
                end,
                score desc
            limit 20
            """,
        )
        rebalance_orders = query_all(
            conn,
            """
            select run_at, as_of_date, strategy_name, symbol, name, side, quantity,
                   price, current_value, target_value, order_value,
                   json_extract(raw_json, '$.reason') as reason
            from rebalance_plan_orders
            where run_at = (select max(run_at) from rebalance_plan_orders)
            order by order_value desc
            limit 20
            """,
        )
        risk_state = query_optional(
            conn,
            """
            select run_at, as_of_date, strategy_name, regime, exposure_multiplier,
                   market_return_20d, market_drawdown_60d, market_volatility_20d,
                   reason
            from strategy_risk_states
            order by run_at desc
            limit 1
            """,
        )
        last_raw = query_optional(
            conn,
            """
            select collected_at, endpoint
            from raw_api_responses
            order by collected_at desc
            limit 1
            """,
        )
        learning_review = query_optional(
            conn,
            """
            select review_at, trade_date, strategy_name, account_delta, realized_pnl,
                   target_avg_return, watch_avg_return, signal_excess_return,
                   risk_regime, exposure_multiplier, quality_score, summary
            from daily_learning_reviews
            order by review_at desc
            limit 1
            """,
        )
        learning_suggestions = query_all(
            conn,
            """
            select review_at, trade_date, strategy_name, parameter, current_value,
                   suggested_value, reason, confidence, applied
            from strategy_parameter_suggestions
            where trade_date = coalesce(?, trade_date)
            order by confidence desc, review_at desc
            limit 8
            """,
            ((learning_review or {}).get("trade_date"),),
        )

    for row in quote_rows:
        row["name"] = symbol_names.get(str(row["symbol"]), "")

    trade_count = int(summary["trade_count"] or 0)
    wins = int(summary["wins"] or 0)
    win_rate = round((wins / trade_count) * 100, 2) if trade_count else 0
    return {
        "date": trade_date,
        "summary": {
            "trade_count": trade_count,
            "buy_amount": summary["buy_amount"],
            "sell_amount": summary["sell_amount"],
            "fee": summary["fee"],
            "tax": summary["tax"],
            "realized_pnl": summary["realized_pnl"],
            "net_cash_flow": summary["sell_amount"] - summary["buy_amount"] - summary["fee"] - summary["tax"],
            "win_rate": win_rate,
            "realized_estimated": realized_estimated,
        },
        "trades": trades,
        "orders": orders,
        "holdings": holdings,
        "account": account,
        "quotes": quote_rows,
        "series": {
            "pnl": list(reversed(pnl_series)),
            "equity": list(reversed(equity_series)),
        },
        "rankings": rankings,
        "strategy": {
            "signals": strategy_signals,
            "orders": rebalance_orders,
            "risk": risk_state,
        },
        "learning": {
            "review": learning_review,
            "suggestions": learning_suggestions,
        },
        "meta": {
            "env": settings.env,
            "live_configured": settings.allow_live_trading,
            "trading_enabled": trading_enabled,
            "live_trading": settings.allow_live_trading and trading_enabled,
            "last_sync": (last_raw or {}).get("collected_at") or (account or {}).get("collected_at"),
            "last_endpoint": (last_raw or {}).get("endpoint"),
        },
    }


def refresh_live_data(db_path: Path) -> dict[str, Any]:
    settings = load_settings()
    store = Store(db_path)
    store.init()
    client = KisClient(settings)
    collector = DataCollector(client, store, settings, pause_seconds=0.35)

    symbols = latest_quote_symbols(db_path)
    sync_result = (
        AccountSync(settings, client, store).sync_daily_executions()
        if settings.cano and settings.acnt_prdt_cd
        else None
    )
    balance_rows = collector.collect_balance()
    quote_rows = collector.collect_current_quotes(symbols) if symbols else 0
    plan = MomentumStrategy(settings, store).build_plan()

    return {
        "ok": True,
        "balance_rows": balance_rows,
        "quote_rows": quote_rows,
        "executions": sync_result.executions if sync_result else 0,
        "new_executions": sync_result.new_executions if sync_result else 0,
        "strategy_signals": len(plan["signals"]),
        "strategy_orders": len(plan["orders"]),
    }


def latest_quote_symbols(db_path: Path) -> list[str]:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
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
            order by symbol
            """
        ).fetchall()
    return [str(row["symbol"]) for row in rows if row["symbol"]]


def latest_ranking(conn: sqlite3.Connection, source: str) -> list[dict[str, Any]]:
    return query_all(
        conn,
        """
        select rank_no, symbol, name, price, volume, trade_value, market_cap, collected_at
        from ranking_snapshots
        where source = ?
          and collected_at = (
            select max(collected_at) from ranking_snapshots where source = ?
          )
        order by coalesce(rank_no, 999999), trade_value desc
        limit 15
        """,
        (source, source),
    )


def load_ranking_symbol_names(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        """
        select symbol, name
        from ranking_snapshots
        where collected_at in (
            select max(collected_at)
            from ranking_snapshots
            group by source
        )
          and symbol is not null
          and name is not null
        """
    ).fetchall()
    return {str(row["symbol"]): str(row["name"]) for row in rows}


def query_one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise RuntimeError("query_one returned no rows")
    return row


def query_optional(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def query_all(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def derive_trade_rows_from_orders(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    submitted = [row for row in orders if str(row.get("status") or "").lower() == "submitted"]
    submitted.sort(key=lambda row: str(row.get("event_time") or ""))
    lots: dict[str, list[dict[str, float]]] = {}
    trades: list[dict[str, Any]] = []
    for row in submitted:
        symbol = str(row.get("symbol") or "")
        side = str(row.get("side") or "")
        quantity = float(row.get("quantity") or 0)
        price = float(row.get("price") or 0)
        amount = quantity * price
        realized_pnl = 0.0
        if not symbol or quantity <= 0 or price <= 0:
            continue
        if side == "buy":
            lots.setdefault(symbol, []).append({"quantity": quantity, "price": price})
        elif side == "sell":
            realized_pnl = estimate_sell_pnl(lots.setdefault(symbol, []), quantity, price)
        trades.append(
            {
                "execution_time": row.get("event_time"),
                "trade_date": row.get("trade_date"),
                "order_id": row.get("order_id"),
                "symbol": symbol,
                "name": row.get("name"),
                "side": side,
                "quantity": quantity,
                "price": price,
                "amount": amount,
                "fee": 0.0,
                "tax": 0.0,
                "realized_pnl": round(realized_pnl, 2),
                "estimated": True,
            }
        )
    return sorted(trades, key=lambda row: str(row.get("execution_time") or ""), reverse=True)


def estimate_sell_pnl(lots: list[dict[str, float]], sell_quantity: float, sell_price: float) -> float:
    remaining = sell_quantity
    pnl = 0.0
    while remaining > 0 and lots:
        lot = lots[0]
        matched = min(remaining, lot["quantity"])
        pnl += matched * (sell_price - lot["price"])
        lot["quantity"] -= matched
        remaining -= matched
        if lot["quantity"] <= 0:
            lots.pop(0)
    return pnl


def summarize_trade_rows(trades: list[dict[str, Any]]) -> dict[str, float]:
    buy_amount = sum(float(row.get("amount") or 0) for row in trades if row.get("side") == "buy")
    sell_amount = sum(float(row.get("amount") or 0) for row in trades if row.get("side") == "sell")
    fee = sum(float(row.get("fee") or 0) for row in trades)
    tax = sum(float(row.get("tax") or 0) for row in trades)
    realized_pnl = sum(float(row.get("realized_pnl") or 0) for row in trades)
    wins = sum(1 for row in trades if row.get("side") == "sell" and float(row.get("realized_pnl") or 0) > 0)
    return {
        "trade_count": len(trades),
        "buy_amount": round(buy_amount, 2),
        "sell_amount": round(sell_amount, 2),
        "fee": round(fee, 2),
        "tax": round(tax, 2),
        "realized_pnl": round(realized_pnl, 2),
        "wins": wins,
    }


def load_symbol_names(path: str | Path = "config/symbols.csv") -> dict[str, str]:
    csv_path = Path(path)
    if not csv_path.exists():
        return {}
    with csv_path.open(newline="", encoding="utf-8") as fp:
        return {
            row["symbol"].strip(): row["name"].strip()
            for row in csv.DictReader(fp)
            if row.get("symbol") and row.get("name")
        }

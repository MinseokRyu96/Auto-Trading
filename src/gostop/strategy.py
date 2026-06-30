from __future__ import annotations

import csv
import math
import sqlite3
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import pstdev
from typing import Any

from .config import Settings
from .storage import Store


STRATEGY_NAME = "liquidity_momentum_v1"


@dataclass(frozen=True)
class StrategyConfig:
    max_positions: int = 5
    core_positions: int = 3
    satellite_positions: int = 2
    max_mega_cap_positions: int = 2
    gross_exposure: float = 0.70
    max_position_weight: float = 0.20
    cash_buffer: float = 0.10
    min_order_amount: float = 50_000
    min_price: float = 1_000
    min_avg_trade_value: float = 1_000_000_000
    max_annual_volatility: float = 0.85
    short_lookback: int = 20
    long_lookback: int = 60
    skip_recent_days: int = 1
    risk_lookback: int = 60
    risk_short_lookback: int = 20
    risk_off_return_20d: float = -0.06
    crash_return_20d: float = -0.12
    risk_off_drawdown_60d: float = -0.10
    crash_drawdown_60d: float = -0.18
    high_volatility_20d: float = 0.35
    risk_off_multiplier: float = 0.35
    high_vol_multiplier: float = 0.50
    crash_multiplier: float = 0.0
    affordability_buffer: float = 0.98
    small_account_threshold: float = 1_000_000
    small_account_cash_buffer: float = 0.002
    news_sentiment_weight: float = 0.10
    news_lookback_hours: int = 6
    news_cash_sweep_enabled: bool = True
    news_cash_sweep_min_sentiment: float = 0.05
    news_cash_sweep_min_count: int = 2
    news_cash_sweep_max_positions: int = 3
    small_account_min_order_amount: float = 5_000
    min_holding_days_before_sell: int = 2
    mega_cap_rank_cutoff: int = 20
    mid_cap_rank_cutoff: int = 200
    long_momentum_weight: float = 0.40
    short_momentum_weight: float = 0.30
    liquidity_weight: float = 0.10
    volatility_weight: float = 0.10
    size_diversification_weight: float = 0.10
    excluded_name_keywords: tuple[str, ...] = (
        "KODEX",
        "TIGER",
        "SOL ",
        "HANARO",
        "ACE ",
        "KBSTAR",
        "레버리지",
        "인버스",
        "선물",
        "ETN",
    )


class MomentumStrategy:
    def __init__(self, settings: Settings, store: Store, config: StrategyConfig | None = None):
        self.settings = settings
        self.store = store
        self.config = config or load_adaptive_config(store)
        self.symbol_names = load_symbol_names()

    def build_plan(self, capital: float | None = None, as_of_date: str | None = None) -> dict[str, Any]:
        with self.store.connect() as conn:
            universe = self._load_universe(conn)
            bars = self._load_bars(conn, universe)
            quotes = self._load_latest_quotes(conn)
            ranking_meta = self._load_ranking_meta(conn)
            holdings = self._load_latest_holdings(conn)
            account = self._load_latest_account(conn)
            news_scores = self._load_news_scores(conn)

        if capital is None:
            capital = account_value(account)
        if not capital:
            capital = sum(position["value"] for position in holdings.values())

        metrics = [
            self._score_symbol(symbol, rows, quotes.get(symbol), ranking_meta.get(symbol, {}), news_scores.get(symbol, {}))
            for symbol, rows in bars.items()
        ]
        metrics = [metric for metric in metrics if metric is not None]
        risk_state = self._market_risk_state(bars)
        self._apply_affordability_filter(metrics, capital or 0, risk_state["exposure_multiplier"], holdings)
        metrics = self._rank_and_score(metrics)
        selected = self._select(metrics)
        news_sweep = self._news_cash_sweep_candidates(
            metrics,
            quotes,
            ranking_meta,
            news_scores,
            holdings,
            capital or 0,
            risk_state,
        )
        if news_sweep:
            metrics = sorted([*metrics, *news_sweep], key=lambda item: item.get("score", 0), reverse=True)
            selected = self._merge_selected(selected, news_sweep)
        targets = self._target_weights(selected, risk_state["exposure_multiplier"], capital or 0, quotes, holdings)
        symbol_names = {str(metric["symbol"]): str(metric.get("name") or "") for metric in metrics}
        metrics_by_symbol = {str(metric["symbol"]): metric for metric in metrics}
        order_reasons = self._order_reasons(selected, metrics_by_symbol, holdings, risk_state)
        orders = self._build_orders(targets, holdings, quotes, capital or 0, symbol_names, order_reasons, risk_state)

        if as_of_date is None:
            as_of_date = max((metric["as_of_date"] for metric in metrics), default="")

        signals = []
        selected_symbols = {item["symbol"] for item in selected}
        target_symbols = {symbol for symbol, weight in targets.items() if weight > 0}
        for metric in metrics:
            symbol = metric["symbol"]
            target_weight = targets.get(symbol, 0.0)
            segment_text = {
                "mega_cap": "초대형 코어",
                "core": "코어",
                "satellite": "탐색",
                "news_sweep": "뉴스 잔여예산",
            }.get(metric.get("segment"), "코어")
            if symbol in target_symbols:
                action = "target"
                reason = self._target_reason(metric, risk_state)
            elif symbol in selected_symbols:
                action = "watch"
                reason = "전체 예산 배분 후 잔여 예산 부족"
            elif symbol in holdings:
                action = "exit"
                reason = "선정 종목에서 제외되어 축소 후보"
            else:
                action = "watch"
                if metric.get("reject_reason"):
                    reason = metric["reject_reason"]
                elif metric.get("segment") == "mega_cap" and count_segment(selected, "mega_cap") >= self.config.max_mega_cap_positions:
                    reason = "초대형주 편입 한도"
                elif metric.get("segment") == "satellite" and count_segment(selected, "satellite") >= self.config.satellite_positions:
                    reason = "탐색 슬롯 한도"
                else:
                    reason = "점수 순위 밖"
            if action != "target" and risk_state["regime"] != "risk_on":
                reason = f"{reason}; 시장 리스크 {risk_state['regime']} - {risk_state['reason']}"
            signals.append({**metric, "target_weight": target_weight, "action": action, "reason": reason})

        self.store.replace_strategy_plan(STRATEGY_NAME, as_of_date, signals, orders, risk_state)
        return {
            "strategy_name": STRATEGY_NAME,
            "as_of_date": as_of_date,
            "capital": capital,
            "risk_state": risk_state,
            "signals": signals,
            "orders": orders,
        }

    def _load_universe(self, conn: sqlite3.Connection) -> list[str]:
        rows = conn.execute(
            """
            select distinct symbol
            from ranking_snapshots
            where symbol is not null
            order by symbol
            """
        ).fetchall()
        symbols = [str(row["symbol"]) for row in rows if row["symbol"]]
        symbols.extend(self.symbol_names.keys())
        return sorted(set(symbols))

    def _load_bars(self, conn: sqlite3.Connection, universe: list[str]) -> dict[str, list[sqlite3.Row]]:
        if not universe:
            return {}
        placeholders = ",".join("?" for _ in universe)
        rows = conn.execute(
            f"""
            select symbol, trade_date, close, volume, trade_value
            from daily_bars
            where symbol in ({placeholders})
              and close is not null
            order by symbol, trade_date
            """,
            tuple(universe),
        ).fetchall()
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(str(row["symbol"]), []).append(row)
        return grouped

    def _load_latest_quotes(self, conn: sqlite3.Connection) -> dict[str, float]:
        rows = conn.execute(
            """
            select q.symbol, q.last_price
            from current_quotes q
            join (
                select symbol, max(collected_at) as collected_at
                from current_quotes
                group by symbol
            ) latest
            on q.symbol = latest.symbol and q.collected_at = latest.collected_at
            """
        ).fetchall()
        return {str(row["symbol"]): float(row["last_price"]) for row in rows if row["last_price"]}

    def _load_ranking_meta(self, conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        rows = conn.execute(
            """
            select source, rank_no, symbol, name, market_cap, trade_value
            from ranking_snapshots
            where collected_at in (
                select max(collected_at)
                from ranking_snapshots
                group by source
            )
              and symbol is not null
            """
        ).fetchall()
        meta: dict[str, dict[str, Any]] = {}
        for row in rows:
            symbol = str(row["symbol"])
            item = meta.setdefault(symbol, {})
            if row["name"] and "name" not in item:
                item["name"] = str(row["name"])
            source = str(row["source"])
            if source == "market_cap":
                item["market_cap_rank"] = int(row["rank_no"]) if row["rank_no"] is not None else None
                item["market_cap"] = float(row["market_cap"] or 0) or None
            elif source == "volume_rank":
                item["volume_rank"] = int(row["rank_no"]) if row["rank_no"] is not None else None
                item["rank_trade_value"] = float(row["trade_value"] or 0) or None
        return meta

    def _load_latest_holdings(self, conn: sqlite3.Connection) -> dict[str, dict[str, float]]:
        latest = conn.execute("select max(collected_at) as ts from balance_snapshots").fetchone()["ts"]
        if not latest:
            return {}
        rows = conn.execute(
            """
            select symbol, name, quantity, eval_amount
            from balance_snapshots
            where collected_at = ?
            """,
            (latest,),
        ).fetchall()
        buy_rows = conn.execute(
            """
            select symbol, max(event_time) as last_buy_at
            from order_events
            where side = 'buy'
              and status = 'submitted'
            group by symbol
            """
        ).fetchall()
        last_buy_by_symbol = {str(row["symbol"]): str(row["last_buy_at"]) for row in buy_rows if row["last_buy_at"]}
        holdings: dict[str, dict[str, Any]] = {}
        for row in rows:
            symbol = str(row["symbol"])
            quantity = float(row["quantity"] or 0)
            value = float(row["eval_amount"] or 0)
            if quantity <= 0 and value <= 0:
                continue
            holdings[symbol] = {
                "name": str(row["name"] or ""),
                "quantity": quantity,
                "value": value,
                "last_buy_at": last_buy_by_symbol.get(symbol, ""),
            }
        return holdings

    def _load_latest_account(self, conn: sqlite3.Connection) -> dict[str, Any] | None:
        row = conn.execute(
            """
            select total_eval_amount, net_asset_amount, cash_amount
            from account_snapshots
            order by collected_at desc
            limit 1
            """
        ).fetchone()
        return dict(row) if row else None

    def _score_symbol(
        self,
        symbol: str,
        rows: list[sqlite3.Row],
        latest_quote: float | None,
        ranking_meta: dict[str, Any],
        news_meta: dict[str, Any],
    ) -> dict[str, Any] | None:
        required = self.config.long_lookback + self.config.skip_recent_days + 1
        if len(rows) < required:
            return None

        usable = rows[: -self.config.skip_recent_days] if self.config.skip_recent_days else rows
        latest = usable[-1]
        close = float(latest["close"])
        price = latest_quote or close
        name = self.symbol_names.get(symbol) or ranking_meta.get("name") or ""
        if self._is_excluded_instrument(symbol, name):
            return None
        if price < self.config.min_price:
            return None

        closes = [float(row["close"]) for row in usable]
        trade_values = [float(row["trade_value"] or 0) for row in usable[-20:]]
        avg_trade_value = sum(trade_values) / len(trade_values)
        if avg_trade_value < self.config.min_avg_trade_value:
            return None

        short_return = total_return(closes, self.config.short_lookback)
        long_return = total_return(closes, self.config.long_lookback)
        daily_returns = pct_changes(closes[-self.config.long_lookback :])
        annual_vol = pstdev(daily_returns) * math.sqrt(252) if len(daily_returns) >= 2 else 9.99
        market_cap_rank = ranking_meta.get("market_cap_rank")
        volume_rank = ranking_meta.get("volume_rank")
        segment = self._segment_for_symbol(market_cap_rank, volume_rank)

        reject_reason = ""
        if short_return <= 0 or long_return <= 0:
            reject_reason = "20/60일 모멘텀이 양수가 아님"
        if annual_vol > self.config.max_annual_volatility:
            reject_reason = "변동성 한도 초과"

        return {
            "symbol": symbol,
            "name": name,
            "as_of_date": latest["trade_date"],
            "price": price,
            "short_return": short_return,
            "long_return": long_return,
            "annual_volatility": annual_vol,
            "avg_trade_value": avg_trade_value,
            "market_cap_rank": market_cap_rank,
            "volume_rank": volume_rank,
            "market_cap": ranking_meta.get("market_cap"),
            "segment": segment,
            "source": "momentum",
            "size_diversification_score": self._size_diversification_score(market_cap_rank, volume_rank),
            "news_sentiment": float(news_meta.get("sentiment") or 0),
            "news_count": int(news_meta.get("count") or 0),
            "news_reason": news_meta.get("reason") or "",
            "news_title": news_meta.get("title") or "",
            "reject_reason": reject_reason,
        }

    def _load_news_scores(self, conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=self.config.news_lookback_hours)).isoformat(timespec="seconds")
        rows = conn.execute(
            """
            select symbol,
                   group_concat(name, '; ') as names,
                   avg(sentiment) as sentiment,
                   count(*) as news_count,
                   group_concat(reason, '; ') as reasons,
                   group_concat(title, ' | ') as titles
            from stock_news
            where symbol is not null
              and collected_at >= ?
            group by symbol
            """,
            (cutoff,),
        ).fetchall()
        return {
            str(row["symbol"]): {
                "name": first_non_empty(str(row["names"] or ""), "; "),
                "sentiment": float(row["sentiment"] or 0),
                "count": int(row["news_count"] or 0),
                "reason": first_non_empty(str(row["reasons"] or ""), "; "),
                "title": first_non_empty(str(row["titles"] or ""), " | "),
            }
            for row in rows
        }

    def _news_cash_sweep_candidates(
        self,
        metrics: list[dict[str, Any]],
        quotes: dict[str, float],
        ranking_meta: dict[str, dict[str, Any]],
        news_scores: dict[str, dict[str, Any]],
        holdings: dict[str, dict[str, float]],
        capital: float,
        risk_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not self.config.news_cash_sweep_enabled or not self._is_small_account(capital):
            return []
        if risk_state["regime"] == "crash":
            return []

        deployable_budget = capital * self._deployable_weight(capital, risk_state["exposure_multiplier"])
        invested = sum(float(position.get("value") or 0) for position in holdings.values())
        remaining = max(0.0, deployable_budget - invested)
        if remaining < self.config.small_account_min_order_amount:
            return []

        by_symbol = {item["symbol"]: item for item in metrics}
        candidates: list[dict[str, Any]] = []
        for symbol, news in news_scores.items():
            if symbol in holdings:
                continue
            price = float(quotes.get(symbol) or 0)
            if price < self.config.min_price or price > remaining:
                continue
            sentiment = float(news.get("sentiment") or 0)
            news_count = int(news.get("count") or 0)
            if sentiment < self.config.news_cash_sweep_min_sentiment or news_count < self.config.news_cash_sweep_min_count:
                continue

            base = by_symbol.get(symbol, {})
            meta = ranking_meta.get(symbol, {})
            name = self.symbol_names.get(symbol) or str(base.get("name") or meta.get("name") or news.get("name") or "")
            if not name or self._is_excluded_instrument(symbol, name):
                continue
            if base.get("source") == "momentum" and not base.get("reject_reason"):
                continue

            score = min(1.0, 0.45 + 0.35 * sentiment + min(news_count, 10) * 0.02)
            candidates.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "as_of_date": str(base.get("as_of_date") or ""),
                    "price": price,
                    "short_return": float(base.get("short_return") or 0),
                    "long_return": float(base.get("long_return") or 0),
                    "annual_volatility": float(base.get("annual_volatility") or 0),
                    "avg_trade_value": float(base.get("avg_trade_value") or 0),
                    "market_cap_rank": meta.get("market_cap_rank") or base.get("market_cap_rank"),
                    "volume_rank": meta.get("volume_rank") or base.get("volume_rank"),
                    "market_cap": meta.get("market_cap") or base.get("market_cap"),
                    "segment": "news_sweep",
                    "source": "news_sweep",
                    "size_diversification_score": 1.0,
                    "news_sentiment": sentiment,
                    "news_count": news_count,
                    "news_reason": news.get("reason") or "",
                    "news_title": news.get("title") or "",
                    "score": round(score, 6),
                    "reject_reason": "",
                }
            )

        return sorted(candidates, key=lambda item: item.get("score", 0), reverse=True)[: self.config.news_cash_sweep_max_positions]

    def _merge_selected(self, selected: list[dict[str, Any]], news_sweep: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged = list(selected)
        symbols = {item["symbol"] for item in merged}
        for item in news_sweep:
            if item["symbol"] in symbols:
                continue
            merged.append(item)
            symbols.add(item["symbol"])
        return merged

    def _apply_affordability_filter(
        self,
        metrics: list[dict[str, Any]],
        capital: float,
        exposure_multiplier: float,
        holdings: dict[str, dict[str, float]],
    ) -> None:
        if capital <= 0:
            return
        affordable_limit = self._candidate_affordability_limit(capital, exposure_multiplier)
        for metric in metrics:
            symbol = metric["symbol"]
            if symbol in holdings:
                continue
            if metric.get("reject_reason"):
                continue
            price = float(metric.get("price") or 0)
            if price > capital:
                metric["reject_reason"] = "현재 계좌 규모로 1주 매수 불가"
            elif price > affordable_limit:
                metric["reject_reason"] = "전체 사용 가능 예산으로 1주 매수 불가"

    def _candidate_affordability_limit(self, capital: float, exposure_multiplier: float) -> float:
        return capital * self._deployable_weight(capital, exposure_multiplier) * self.config.affordability_buffer

    def _rank_and_score(self, metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        positive = [m for m in metrics if not m["reject_reason"]]
        if not positive:
            for metric in metrics:
                metric["score"] = 0.0
            return sorted(metrics, key=lambda item: item["symbol"])

        assign_rank_score(positive, "long_return", reverse=True, output="long_rank_score")
        assign_rank_score(positive, "short_return", reverse=True, output="short_rank_score")
        assign_rank_score(positive, "avg_trade_value", reverse=True, output="liquidity_rank_score")
        assign_rank_score(positive, "annual_volatility", reverse=False, output="volatility_rank_score")

        positive_by_symbol = {item["symbol"]: item for item in positive}
        for metric in metrics:
            ranked = positive_by_symbol.get(metric["symbol"])
            if not ranked:
                metric["score"] = 0.0
                continue
            metric["score"] = round(
                self.config.long_momentum_weight * ranked["long_rank_score"]
                + self.config.short_momentum_weight * ranked["short_rank_score"]
                + self.config.liquidity_weight * ranked["liquidity_rank_score"]
                + self.config.volatility_weight * ranked["volatility_rank_score"]
                + self.config.size_diversification_weight * ranked["size_diversification_score"],
                6,
            )
            if metric.get("news_count"):
                adjusted = metric["score"] + self.config.news_sentiment_weight * float(metric.get("news_sentiment") or 0)
                metric["score"] = round(max(0.0, min(1.0, adjusted)), 6)
        return sorted(metrics, key=lambda item: item.get("score", 0), reverse=True)

    def _select(self, metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        positive = [item for item in metrics if not item.get("reject_reason")]
        selected: list[dict[str, Any]] = []
        selected_symbols: set[str] = set()

        def add_candidates(candidates: list[dict[str, Any]], limit: int) -> None:
            nonlocal selected, selected_symbols
            for item in candidates:
                if len(selected) >= self.config.max_positions:
                    return
                if item["symbol"] in selected_symbols:
                    continue
                if count_segment(selected, "mega_cap") >= self.config.max_mega_cap_positions and item["segment"] == "mega_cap":
                    continue
                selected.append(item)
                selected_symbols.add(item["symbol"])
                if len([row for row in selected if row["symbol"] in {candidate["symbol"] for candidate in candidates}]) >= limit:
                    return

        core = [item for item in positive if item["segment"] in {"mega_cap", "core"}]
        satellite = [item for item in positive if item["segment"] == "satellite"]
        add_candidates(core, self.config.core_positions)
        add_candidates(satellite, self.config.satellite_positions)
        add_candidates(positive, self.config.max_positions)
        return selected[: self.config.max_positions]

    def _segment_for_symbol(self, market_cap_rank: int | None, volume_rank: int | None) -> str:
        if market_cap_rank is not None:
            if market_cap_rank <= self.config.mega_cap_rank_cutoff:
                return "mega_cap"
            if market_cap_rank <= self.config.mid_cap_rank_cutoff:
                return "satellite"
            return "core"
        if volume_rank is not None and volume_rank > self.config.mega_cap_rank_cutoff:
            return "satellite"
        return "core"

    def _size_diversification_score(self, market_cap_rank: int | None, volume_rank: int | None) -> float:
        segment = self._segment_for_symbol(market_cap_rank, volume_rank)
        if segment == "satellite":
            return 1.0
        if segment == "core":
            return 0.65
        return 0.25

    def _is_excluded_instrument(self, symbol: str, name: str) -> bool:
        if not symbol.isdigit():
            return True
        if name.endswith("우"):
            return True
        return any(keyword in name for keyword in self.config.excluded_name_keywords)

    def _target_reason(self, metric: dict[str, Any], risk_state: dict[str, Any]) -> str:
        segment_text = {
            "mega_cap": "초대형 코어",
            "core": "코어",
            "satellite": "탐색",
            "news_sweep": "뉴스 잔여예산",
        }.get(metric.get("segment"), "코어")
        if metric.get("source") == "news_sweep":
            reason = f"{segment_text} 슬롯; 남은 예산으로 1주 이상 매수 가능"
        else:
            reason = (
                f"{segment_text} 슬롯; "
                f"20일 수익률 {metric.get('short_return', 0) * 100:.2f}%, "
                f"60일 수익률 {metric.get('long_return', 0) * 100:.2f}%, "
                f"거래대금/변동성 필터 통과"
            )
        if metric.get("news_count"):
            reason = (
                f"{reason}; 뉴스 {int(metric.get('news_count') or 0)}건 "
                f"점수 {float(metric.get('news_sentiment') or 0):+.2f}"
                + (f" - {metric.get('news_reason')}" if metric.get("news_reason") else "")
            )
        if risk_state["regime"] != "risk_on":
            reason = f"{reason}; 시장 리스크 {risk_state['regime']} - {risk_state['reason']}"
        return reason

    def _order_reasons(
        self,
        selected: list[dict[str, Any]],
        metrics_by_symbol: dict[str, dict[str, Any]],
        holdings: dict[str, dict[str, float]],
        risk_state: dict[str, Any],
    ) -> dict[str, str]:
        reasons = {item["symbol"]: self._target_reason(item, risk_state) for item in selected}
        for symbol in holdings:
            if symbol in reasons:
                continue
            reasons[symbol] = self._exit_reason(metrics_by_symbol.get(symbol), risk_state)
        return reasons

    def _exit_reason(self, metric: dict[str, Any] | None, risk_state: dict[str, Any]) -> str:
        parts = ["전략 매도 후보: 현재 선정 종목에서 제외"]
        if metric:
            if metric.get("reject_reason"):
                parts.append(f"제외 사유: {metric['reject_reason']}")
            else:
                parts.append(
                    "점수/슬롯 기준 미달"
                    f" (20일 {float(metric.get('short_return') or 0) * 100:.2f}%, "
                    f"60일 {float(metric.get('long_return') or 0) * 100:.2f}%)"
                )
            if metric.get("news_count"):
                parts.append(
                    f"뉴스 {int(metric.get('news_count') or 0)}건 점수 "
                    f"{float(metric.get('news_sentiment') or 0):+.2f}"
                    + (f" - {metric.get('news_reason')}" if metric.get("news_reason") else "")
                )
        else:
            parts.append("전략 산출 데이터 부족 또는 유니버스 제외")
        if risk_state["regime"] != "risk_on":
            parts.append(f"시장 리스크 {risk_state['regime']} - {risk_state['reason']}")
        return "; ".join(parts)

    def _target_weights(
        self,
        selected: list[dict[str, Any]],
        exposure_multiplier: float,
        capital: float,
        quotes: dict[str, float],
        holdings: dict[str, dict[str, float]],
    ) -> dict[str, float]:
        if not selected:
            return {}
        if self._is_small_account(capital):
            return self._whole_share_target_weights(selected, exposure_multiplier, capital, quotes, holdings)

        max_gross = self._deployable_weight(capital, exposure_multiplier)
        if max_gross <= 0:
            return {}
        score_sum = sum(max(item["score"], 0.01) for item in selected)
        weights: dict[str, float] = {}
        for item in selected:
            raw_weight = max_gross * max(item["score"], 0.01) / score_sum
            weights[item["symbol"]] = min(raw_weight, self.config.max_position_weight)
        used = sum(weights.values())
        if used > 0 and used < max_gross:
            scale = min(max_gross / used, self.config.max_position_weight / max(weights.values()))
            weights = {symbol: round(weight * scale, 6) for symbol, weight in weights.items()}
        return weights

    def _whole_share_target_weights(
        self,
        selected: list[dict[str, Any]],
        exposure_multiplier: float,
        capital: float,
        quotes: dict[str, float],
        holdings: dict[str, dict[str, float]],
    ) -> dict[str, float]:
        budget = capital * self._deployable_weight(capital, exposure_multiplier)
        if budget <= 0:
            return {}

        quantities: dict[str, int] = {}
        remaining = budget
        for item in selected:
            symbol = item["symbol"]
            current_value = float(holdings.get(symbol, {}).get("value", 0.0))
            if current_value <= 0:
                continue
            quantities[symbol] = math.floor(current_value / max(float(quotes.get(symbol) or 0), 1))
            remaining -= min(current_value, remaining)

        candidates = [item for item in selected if float(quotes.get(item["symbol"]) or 0) > 0]
        candidates = sorted(candidates, key=lambda item: item.get("score", 0), reverse=True)
        while candidates:
            purchased = False
            for item in candidates:
                symbol = item["symbol"]
                price = float(quotes.get(symbol) or 0)
                if price <= 0 or price > remaining:
                    continue
                quantities[symbol] = quantities.get(symbol, 0) + 1
                remaining -= price
                purchased = True
            if not purchased:
                break

        return {
            symbol: round((quantity * float(quotes[symbol])) / capital, 6)
            for symbol, quantity in quantities.items()
            if quantity > 0 and symbol in quotes
        }

    def _deployable_weight(self, capital: float, exposure_multiplier: float) -> float:
        if exposure_multiplier <= 0:
            return 0.0
        if self._is_small_account(capital):
            return max(0.0, min(1.0, 1 - self.config.small_account_cash_buffer))
        return min(self.config.gross_exposure, 1 - self.config.cash_buffer) * max(exposure_multiplier, 0)

    def _is_small_account(self, capital: float) -> bool:
        return 0 < capital <= self.config.small_account_threshold

    def _market_risk_state(self, bars: dict[str, list[sqlite3.Row]]) -> dict[str, Any]:
        returns_by_date: dict[str, list[float]] = {}
        for rows in bars.values():
            clean_rows = [row for row in rows if row["close"]]
            for index in range(1, len(clean_rows)):
                previous = float(clean_rows[index - 1]["close"])
                current = float(clean_rows[index]["close"])
                if previous <= 0:
                    continue
                returns_by_date.setdefault(clean_rows[index]["trade_date"], []).append(current / previous - 1)

        dates = sorted(returns_by_date)
        if len(dates) < self.config.risk_short_lookback + 1:
            return {
                "regime": "unknown",
                "exposure_multiplier": min(self.config.risk_off_multiplier, 0.35),
                "market_return_20d": None,
                "market_drawdown_60d": None,
                "market_volatility_20d": None,
                "reason": "시장 프록시 데이터 부족",
            }

        nav = 1.0
        series: list[tuple[str, float, float]] = []
        for trade_date in dates:
            daily_return = sum(returns_by_date[trade_date]) / len(returns_by_date[trade_date])
            nav *= 1 + daily_return
            series.append((trade_date, nav, daily_return))

        nav_values = [item[1] for item in series]
        daily_returns = [item[2] for item in series]
        return_20d = nav_values[-1] / nav_values[-self.config.risk_short_lookback - 1] - 1
        lookback_nav = nav_values[-self.config.risk_lookback :] if len(nav_values) >= self.config.risk_lookback else nav_values
        drawdown_60d = nav_values[-1] / max(lookback_nav) - 1
        vol_returns = daily_returns[-self.config.risk_short_lookback :]
        volatility_20d = pstdev(vol_returns) * math.sqrt(252) if len(vol_returns) >= 2 else 0
        ma_60 = sum(lookback_nav) / len(lookback_nav)
        below_ma = nav_values[-1] < ma_60

        if return_20d <= self.config.crash_return_20d or drawdown_60d <= self.config.crash_drawdown_60d:
            return {
                "regime": "crash",
                "exposure_multiplier": self.config.crash_multiplier,
                "market_return_20d": return_20d,
                "market_drawdown_60d": drawdown_60d,
                "market_volatility_20d": volatility_20d,
                "reason": "20일 급락 또는 60일 고점 대비 큰 낙폭",
            }

        if return_20d <= self.config.risk_off_return_20d or drawdown_60d <= self.config.risk_off_drawdown_60d or (below_ma and return_20d < 0):
            return {
                "regime": "risk_off",
                "exposure_multiplier": self.config.risk_off_multiplier,
                "market_return_20d": return_20d,
                "market_drawdown_60d": drawdown_60d,
                "market_volatility_20d": volatility_20d,
                "reason": "시장 프록시 추세 약화 또는 손실 구간",
            }

        if volatility_20d >= self.config.high_volatility_20d:
            return {
                "regime": "high_volatility",
                "exposure_multiplier": self.config.high_vol_multiplier,
                "market_return_20d": return_20d,
                "market_drawdown_60d": drawdown_60d,
                "market_volatility_20d": volatility_20d,
                "reason": "20일 변동성 상승",
            }

        return {
            "regime": "risk_on",
            "exposure_multiplier": 1.0,
            "market_return_20d": return_20d,
            "market_drawdown_60d": drawdown_60d,
            "market_volatility_20d": volatility_20d,
            "reason": "시장 프록시 추세와 변동성 정상",
        }

    def _build_orders(
        self,
        targets: dict[str, float],
        holdings: dict[str, dict[str, float]],
        quotes: dict[str, float],
        capital: float,
        symbol_names: dict[str, str],
        order_reasons: dict[str, str],
        risk_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        orders: list[dict[str, Any]] = []
        symbols = sorted(set(targets) | set(holdings))
        for symbol in symbols:
            price = quotes.get(symbol)
            current_value = holdings.get(symbol, {}).get("value", 0.0)
            if not price or price <= 0:
                continue
            target_value = capital * targets.get(symbol, 0.0)
            diff = target_value - current_value
            min_order_amount = (
                self.config.small_account_min_order_amount if self._is_small_account(capital) else self.config.min_order_amount
            )
            if abs(diff) < min_order_amount:
                continue
            quantity = math.floor(abs(diff) / price)
            if quantity <= 0:
                continue
            side = "buy" if diff > 0 else "sell"
            reason = order_reasons.get(symbol, "")
            if side == "sell" and self._is_sell_grace_active(holdings.get(symbol, {}), risk_state):
                continue
            if side == "sell" and target_value > 0:
                reason = f"목표 비중 축소: 현재 평가액 {current_value:,.0f}원 > 목표 평가액 {target_value:,.0f}원; {reason}"
            orders.append(
                {
                    "symbol": symbol,
                    "name": self.symbol_names.get(symbol) or symbol_names.get(symbol, "") or holdings.get(symbol, {}).get("name", ""),
                    "side": side,
                    "quantity": quantity,
                    "price": price,
                    "current_value": round(current_value, 2),
                    "target_value": round(target_value, 2),
                    "order_value": round(quantity * price, 2),
                    "reason": reason,
                }
            )
        return orders

    def _is_sell_grace_active(self, holding: dict[str, Any], risk_state: dict[str, Any]) -> bool:
        if risk_state.get("regime") == "crash":
            return False
        last_buy_at = str(holding.get("last_buy_at") or "")
        if not last_buy_at:
            return False
        age_days = days_since_utc(last_buy_at)
        return age_days is not None and age_days < self.config.min_holding_days_before_sell


def total_return(closes: list[float], lookback: int) -> float:
    if len(closes) <= lookback or closes[-lookback - 1] == 0:
        return 0.0
    return closes[-1] / closes[-lookback - 1] - 1


def pct_changes(values: list[float]) -> list[float]:
    return [values[index] / values[index - 1] - 1 for index in range(1, len(values)) if values[index - 1] != 0]


def days_since_utc(value: str) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    return max(0, delta.days)


def assign_rank_score(rows: list[dict[str, Any]], key: str, reverse: bool, output: str) -> None:
    ordered = sorted(rows, key=lambda item: item[key], reverse=reverse)
    if len(ordered) == 1:
        ordered[0][output] = 1.0
        return
    for index, row in enumerate(ordered):
        row[output] = 1 - index / (len(ordered) - 1)


def first_non_empty(value: str, separator: str) -> str:
    for part in value.split(separator):
        text = part.strip()
        if text:
            return text
    return ""


def count_segment(rows: list[dict[str, Any]], segment: str) -> int:
    return sum(1 for row in rows if row.get("segment") == segment)


def account_value(account: dict[str, Any] | None) -> float | None:
    if not account:
        return None
    return float(account.get("total_eval_amount") or account.get("net_asset_amount") or 0) or None


def load_adaptive_config(store: Store) -> StrategyConfig:
    config = StrategyConfig()
    overrides = store.load_parameter_overrides(STRATEGY_NAME)
    if not overrides:
        return config

    field_names = {item.name for item in fields(StrategyConfig)}
    clean_overrides: dict[str, Any] = {}
    for name, value in overrides.items():
        if name not in field_names:
            continue
        current_value = getattr(config, name)
        if not isinstance(current_value, (int, float)):
            continue
        clean_overrides[name] = int(value) if isinstance(current_value, int) else float(value)
    return replace(config, **clean_overrides) if clean_overrides else config


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

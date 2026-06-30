from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import date
from statistics import mean
from typing import Any

from .storage import Store, normalize_trade_date
from .strategy import STRATEGY_NAME, StrategyConfig


class EndOfDayReview:
    def __init__(self, store: Store, config: StrategyConfig | None = None):
        self.store = store
        self.config = config or StrategyConfig()

    def run(
        self,
        trade_date: str | None = None,
        strategy_name: str = STRATEGY_NAME,
        apply_suggestions: bool = False,
        min_confidence: float = 0.70,
    ) -> dict[str, Any]:
        normalized_date = normalize_trade_date(trade_date or date.today().isoformat())
        with self.store.connect() as conn:
            trade_summary = self._trade_summary(conn, normalized_date)
            account_start, account_end = self._account_window(conn, normalized_date)
            signals = self._latest_signals(conn, strategy_name)
            risk = self._latest_risk_state(conn, strategy_name)
            signal_review = self._signal_followthrough(conn, normalized_date, signals)

        account_delta = None
        if account_start is not None and account_end is not None:
            account_delta = account_end - account_start

        review = self._build_review(
            normalized_date,
            strategy_name,
            trade_summary,
            account_start,
            account_end,
            account_delta,
            signal_review,
            risk,
        )
        suggestions = self._parameter_suggestions(review)
        applied: list[str] = []
        if apply_suggestions:
            applied = self.store.apply_parameter_suggestions(
                normalized_date,
                strategy_name,
                suggestions,
                min_confidence=min_confidence,
            )
            for item in suggestions:
                item["applied"] = item["parameter"] in applied
        review["recommendations"] = suggestions
        self.store.replace_daily_learning_review(normalized_date, strategy_name, review, suggestions)
        return {"review": review, "suggestions": suggestions, "applied": applied}

    def _trade_summary(self, conn: sqlite3.Connection, trade_date: str) -> dict[str, Any]:
        row = conn.execute(
            """
            select
                count(*) as trade_count,
                coalesce(sum(case when side = 'buy' then amount else 0 end), 0) as buy_amount,
                coalesce(sum(case when side = 'sell' then amount else 0 end), 0) as sell_amount,
                coalesce(sum(realized_pnl), 0) as realized_pnl,
                coalesce(sum(case when realized_pnl > 0 then 1 else 0 end), 0) as wins
            from trade_executions
            where trade_date = ?
            """,
            (trade_date,),
        ).fetchone()
        trade_count = int(row["trade_count"] or 0)
        wins = int(row["wins"] or 0)
        return {
            "trade_count": trade_count,
            "buy_amount": float(row["buy_amount"] or 0),
            "sell_amount": float(row["sell_amount"] or 0),
            "realized_pnl": float(row["realized_pnl"] or 0),
            "win_rate": round(wins / trade_count, 4) if trade_count else 0.0,
        }

    def _account_window(self, conn: sqlite3.Connection, trade_date: str) -> tuple[float | None, float | None]:
        end_row = conn.execute(
            """
            select total_eval_amount, net_asset_amount
            from account_snapshots
            where substr(collected_at, 1, 10) <= ?
            order by collected_at desc
            limit 1
            """,
            (trade_date,),
        ).fetchone()
        start_row = conn.execute(
            """
            select total_eval_amount, net_asset_amount
            from account_snapshots
            where substr(collected_at, 1, 10) < ?
            order by collected_at desc
            limit 1
            """,
            (trade_date,),
        ).fetchone()
        return account_value(start_row), account_value(end_row)

    def _latest_signals(self, conn: sqlite3.Connection, strategy_name: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            select run_at, as_of_date, strategy_name, symbol, name, score, target_weight, action, reason
            from strategy_signals
            where strategy_name = ?
              and run_at = (
                select max(run_at) from strategy_signals where strategy_name = ?
              )
            order by score desc
            """,
            (strategy_name, strategy_name),
        ).fetchall()
        return [dict(row) for row in rows]

    def _latest_risk_state(self, conn: sqlite3.Connection, strategy_name: str) -> dict[str, Any] | None:
        row = conn.execute(
            """
            select run_at, as_of_date, strategy_name, regime, exposure_multiplier,
                   market_return_20d, market_drawdown_60d, market_volatility_20d, reason
            from strategy_risk_states
            where strategy_name = ?
            order by run_at desc
            limit 1
            """,
            (strategy_name,),
        ).fetchone()
        return dict(row) if row else None

    def _signal_followthrough(
        self,
        conn: sqlite3.Connection,
        trade_date: str,
        signals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        target_returns: list[float] = []
        watch_returns: list[float] = []
        compact_review_date = compact_date(trade_date)
        for signal in signals:
            symbol = str(signal["symbol"])
            start_close = self._close_on_or_before(conn, symbol, compact_date(signal["as_of_date"]))
            end_close = self._close_on_or_before(conn, symbol, compact_review_date)
            if start_close is None or end_close is None or start_close <= 0:
                continue
            return_value = end_close / start_close - 1
            if signal["action"] == "target":
                target_returns.append(return_value)
            else:
                watch_returns.append(return_value)

        target_avg = mean(target_returns) if target_returns else None
        watch_avg = mean(watch_returns) if watch_returns else None
        excess = None
        if target_avg is not None and watch_avg is not None:
            excess = target_avg - watch_avg
        target_win_rate = (
            sum(1 for value in target_returns if value > 0) / len(target_returns) if target_returns else 0.0
        )
        return {
            "target_count": len(target_returns),
            "watch_count": len(watch_returns),
            "target_avg_return": target_avg,
            "watch_avg_return": watch_avg,
            "signal_excess_return": excess,
            "target_win_rate": target_win_rate,
        }

    def _close_on_or_before(self, conn: sqlite3.Connection, symbol: str, trade_date: str) -> float | None:
        row = conn.execute(
            """
            select close
            from daily_bars
            where symbol = ?
              and trade_date <= ?
              and close is not null
            order by trade_date desc
            limit 1
            """,
            (symbol, trade_date),
        ).fetchone()
        return float(row["close"]) if row and row["close"] else None

    def _build_review(
        self,
        trade_date: str,
        strategy_name: str,
        trade_summary: dict[str, Any],
        account_start: float | None,
        account_end: float | None,
        account_delta: float | None,
        signal_review: dict[str, Any],
        risk: dict[str, Any] | None,
    ) -> dict[str, Any]:
        realized_pnl = float(trade_summary["realized_pnl"])
        pnl_rate = account_delta / account_start if account_start and account_delta is not None else 0.0
        signal_excess = signal_review["signal_excess_return"] or 0.0
        quality_score = clamp(50 + pnl_rate * 1800 + signal_excess * 900, 0, 100)
        risk_regime = risk["regime"] if risk else "unknown"
        exposure = float(risk["exposure_multiplier"]) if risk and risk["exposure_multiplier"] is not None else None
        summary = self._summary_text(trade_summary, account_delta, signal_review, risk_regime, quality_score)
        return {
            "trade_date": trade_date,
            "strategy_name": strategy_name,
            "account_start": account_start,
            "account_end": account_end,
            "account_delta": account_delta,
            "realized_pnl": realized_pnl,
            "buy_amount": trade_summary["buy_amount"],
            "sell_amount": trade_summary["sell_amount"],
            "trade_count": trade_summary["trade_count"],
            "win_rate": trade_summary["win_rate"],
            "target_count": signal_review["target_count"],
            "watch_count": signal_review["watch_count"],
            "target_avg_return": signal_review["target_avg_return"],
            "watch_avg_return": signal_review["watch_avg_return"],
            "signal_excess_return": signal_review["signal_excess_return"],
            "target_win_rate": signal_review["target_win_rate"],
            "risk_regime": risk_regime,
            "exposure_multiplier": exposure,
            "quality_score": round(quality_score, 2),
            "summary": summary,
            "config_snapshot": asdict(self.config),
        }

    def _summary_text(
        self,
        trade_summary: dict[str, Any],
        account_delta: float | None,
        signal_review: dict[str, Any],
        risk_regime: str,
        quality_score: float,
    ) -> str:
        delta_text = "계좌 전일 비교 데이터 부족" if account_delta is None else f"계좌 변화 {account_delta:,.0f}원"
        signal_text = "전략 후보 검증 데이터 부족"
        if signal_review["signal_excess_return"] is not None:
            signal_text = f"후보 초과수익 {signal_review['signal_excess_return'] * 100:.2f}%p"
        return (
            f"{delta_text}, 실현손익 {trade_summary['realized_pnl']:,.0f}원, "
            f"{signal_text}, 리스크 체제 {risk_regime}, 품질점수 {quality_score:.1f}"
        )

    def _parameter_suggestions(self, review: dict[str, Any]) -> list[dict[str, Any]]:
        suggestions: list[dict[str, Any]] = []
        cfg = review["config_snapshot"]
        excess = review["signal_excess_return"]
        account_delta = review["account_delta"]
        realized_pnl = review["realized_pnl"]
        risk_regime = review["risk_regime"]
        target_count = int(review["target_count"] or 0)

        if excess is not None and excess < -0.01 and target_count >= 2:
            suggestions.append(
                suggestion(
                    "max_positions",
                    cfg["max_positions"],
                    max(1, cfg["max_positions"] - 1),
                    "선정 종목군이 관찰 종목군보다 부진해 단기적으로 집중도를 높입니다.",
                    0.72,
                )
            )
            suggestions.append(
                suggestion(
                    "max_annual_volatility",
                    cfg["max_annual_volatility"],
                    max(0.35, round(cfg["max_annual_volatility"] - 0.05, 2)),
                    "후보 종목 성과가 약해 변동성 필터를 더 엄격하게 둡니다.",
                    0.68,
                )
            )

        if (account_delta is not None and account_delta < 0) or realized_pnl < 0:
            suggestions.append(
                suggestion(
                    "gross_exposure",
                    cfg["gross_exposure"],
                    max(0.2, round(cfg["gross_exposure"] - 0.05, 2)),
                    "일일 손익이 손실 구간이라 다음 사이클의 총 노출을 낮춥니다.",
                    0.66,
                )
            )

        if risk_regime in {"risk_off", "high_volatility"} and account_delta is not None and account_delta < 0:
            suggestions.append(
                suggestion(
                    "risk_off_multiplier",
                    cfg["risk_off_multiplier"],
                    max(0.1, round(cfg["risk_off_multiplier"] - 0.05, 2)),
                    "하락장 방어 중에도 계좌가 감소해 위험 축소 배율을 더 보수적으로 조정합니다.",
                    0.74,
                )
            )

        if risk_regime == "crash":
            suggestions.append(
                suggestion(
                    "crash_multiplier",
                    cfg["crash_multiplier"],
                    0.0,
                    "급락 체제에서는 신규 위험자산 노출을 열지 않습니다.",
                    0.9,
                )
            )

        if not suggestions:
            suggestions.append(
                suggestion(
                    "no_change",
                    None,
                    None,
                    "오늘 데이터에서는 통계적으로 의미 있는 조정 신호가 약해 기존 파라미터를 유지합니다.",
                    0.6,
                )
            )
        return suggestions


def account_value(row: sqlite3.Row | None) -> float | None:
    if not row:
        return None
    value = row["total_eval_amount"] or row["net_asset_amount"]
    return float(value) if value else None


def compact_date(value: str) -> str:
    text = normalize_trade_date(value)
    return text.replace("-", "")


def clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def suggestion(
    parameter: str,
    current_value: float | int | None,
    suggested_value: float | int | None,
    reason: str,
    confidence: float,
) -> dict[str, Any]:
    return {
        "parameter": parameter,
        "current_value": current_value,
        "suggested_value": suggested_value,
        "reason": reason,
        "confidence": confidence,
        "applied": False,
    }

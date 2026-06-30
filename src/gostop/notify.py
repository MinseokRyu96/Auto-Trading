from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib import error, request
from zoneinfo import ZoneInfo

from .config import Settings


KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class TelegramNotifier:
    token: str | None
    chat_id: str | None

    @classmethod
    def from_settings(cls, settings: Settings) -> TelegramNotifier:
        return cls(settings.telegram_bot_token, settings.telegram_chat_id)

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        req = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=12) as res:
                return 200 <= res.status < 300
        except (error.HTTPError, error.URLError, TimeoutError) as exc:
            print(f"telegram notification failed: {exc}", flush=True)
            return False

    def send_runner_started(self, live: bool, start_time: str, end_time: str, interval_minutes: int) -> None:
        self.send(
            "\n".join(
                [
                    "[GoStop] 🟢 자동매매 러너 시작",
                    f"모드: {'실주문' if live else '모의 실행'}",
                    f"운영 시간: {start_time}-{end_time}",
                    f"주기: {interval_minutes}분",
                ]
            )
        )

    def send_cycle_summary(self, payload: dict[str, Any]) -> None:
        now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        self.send(
            "\n".join(
                [
                    f"[GoStop] 📊 장중 업데이트",
                    f"시간: {now}",
                    f"현금: {format_money(payload.get('cash_amount'))}",
                    f"총평가: {format_money(payload.get('total_eval_amount'))}",
                    f"시장 상태: {risk_label(payload.get('regime'))}",
                    f"매매 상태: {'ON' if payload.get('trading_enabled') else 'OFF'}",
                    f"뉴스 반영: {payload.get('news_rows', 0)}건",
                    f"주문 후보: {payload.get('orders', 0)}건",
                    f"제출: {payload.get('submitted', 0)}건 / 스킵: {payload.get('skipped', 0)}건",
                ]
            )
        )

    def send_order_events(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        lines = ["[GoStop] 주문 알림"]
        for index, row in enumerate(rows[:8]):
            if index:
                lines.append("")
            side = str(row.get("side") or "")
            status = str(row.get("status") or "-")
            title, status_text = order_title(side, status)
            lines.extend(
                [
                    title,
                    f"종목: {row.get('name') or row.get('symbol') or '-'} ({row.get('symbol') or '-'})",
                    f"수량: {float(row.get('quantity') or 0):g}주",
                    f"주문가: {format_money(row.get('price'))}",
                    f"매수 근거: {format_reason(row.get('reason'))}" if side == "buy" else f"매도 근거: {format_reason(row.get('reason'))}",
                    f"상태: {status_text}",
                ]
            )
        self.send("\n".join(lines))

    def send_error(self, message: str) -> None:
        now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        self.send(f"[GoStop] 🚨 오류 발생\n시간: {now}\n내용: {message}")


def format_money(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0
    return f"{number:,.0f}원"


def risk_label(value: Any) -> str:
    labels = {
        "risk_on": "정상",
        "high_volatility": "고변동성",
        "risk_off": "위험 축소",
        "crash": "급락 방어",
        "unknown": "데이터 부족",
    }
    return labels.get(str(value or ""), str(value or "-"))


def format_reason(value: Any) -> str:
    reason = " ".join(str(value or "").split())
    if not reason:
        return "-"
    if len(reason) > 180:
        return f"{reason[:177]}..."
    return reason


def order_title(side: str, status: str) -> tuple[str, str]:
    side_text = "매수" if side == "buy" else "매도"
    status_lower = status.lower()
    if status_lower == "submitted":
        return f"✅ {side_text} 주문완료", "주문 제출 완료"
    if status_lower == "submitting":
        return f"⏳ {side_text} 주문 제출중", "주문 제출중"
    if status_lower.startswith("failed"):
        return f"⚠️ {side_text} 주문실패", korean_status(status)
    if status_lower.startswith("guard_skip"):
        return f"⏭️ {side_text} 주문스킵", korean_status(status)
    if status_lower == "dry_run":
        return f"🧪 {side_text} 모의주문", "모의 실행 기록"
    return f"ℹ️ {side_text} 주문상태", korean_status(status)


def korean_status(status: str) -> str:
    replacements = {
        "guard_skip: same-day duplicate order blocked": "당일 중복 주문 차단",
        "guard_skip: daily order limit exceeded": "일일 주문 한도 초과",
        "dry_run": "모의 실행 기록",
        "submitted": "주문 제출 완료",
        "submitting": "주문 제출중",
    }
    if status in replacements:
        return replacements[status]
    if status.startswith("guard_skip: single order limit exceeded"):
        return "1회 주문 한도 초과"
    if status.startswith("failed:"):
        return f"실패: {status.removeprefix('failed:').strip()}"
    return status or "-"

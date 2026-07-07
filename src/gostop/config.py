from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    env: str
    app_key: str
    app_secret: str
    base_url: str
    db_path: Path
    market_div: str
    allow_live_trading: bool = False
    max_daily_order_value: float = 1_000_000
    max_single_order_value: float = 1_000_000
    kis_min_interval_seconds: float = 1.05
    kis_request_timeout_seconds: float = 30.0
    news_enabled: bool = True
    news_refresh_minutes: int = 30
    news_max_symbol_queries: int = 12
    news_items_per_query: int = 12
    telegram_summary_minutes: int = 20
    cano: str | None = None
    acnt_prdt_cd: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    @property
    def is_real(self) -> bool:
        return self.env == "real"


def load_settings() -> Settings:
    load_dotenv()

    env = os.getenv("KIS_ENV", "demo").strip().lower()
    if env not in {"demo", "real"}:
        raise ValueError("KIS_ENV must be 'demo' or 'real'")

    base_url = os.getenv("KIS_BASE_URL")
    if not base_url:
        base_url = (
            "https://openapi.koreainvestment.com:9443"
            if env == "real"
            else "https://openapivts.koreainvestment.com:29443"
        )

    account_no = os.getenv("KIS_ACCOUNT_NO", "").strip()
    cano = os.getenv("KIS_CANO", "").strip() or None
    product = os.getenv("KIS_ACNT_PRDT_CD", "").strip() or None
    if account_no and "-" in account_no:
        cano, product = account_no.split("-", 1)

    return Settings(
        env=env,
        app_key=os.getenv("KIS_APP_KEY", "").strip(),
        app_secret=os.getenv("KIS_APP_SECRET", "").strip(),
        base_url=base_url.rstrip("/"),
        db_path=Path(os.getenv("GOSTOP_DB_PATH", "data/gostop.sqlite3")),
        market_div=os.getenv("GOSTOP_MARKET_DIV", "J").strip() or "J",
        allow_live_trading=os.getenv("GOSTOP_ALLOW_LIVE_TRADING", "false").strip().lower() == "true",
        max_daily_order_value=float(os.getenv("GOSTOP_MAX_DAILY_ORDER_VALUE", "1000000")),
        max_single_order_value=float(os.getenv("GOSTOP_MAX_SINGLE_ORDER_VALUE", "1000000")),
        kis_min_interval_seconds=float(os.getenv("GOSTOP_KIS_MIN_INTERVAL_SECONDS", "1.05")),
        kis_request_timeout_seconds=float(os.getenv("GOSTOP_KIS_REQUEST_TIMEOUT_SECONDS", "30")),
        news_enabled=os.getenv("GOSTOP_NEWS_ENABLED", "true").strip().lower() == "true",
        news_refresh_minutes=int(os.getenv("GOSTOP_NEWS_REFRESH_MINUTES", "30")),
        news_max_symbol_queries=int(os.getenv("GOSTOP_NEWS_MAX_SYMBOL_QUERIES", "12")),
        news_items_per_query=int(os.getenv("GOSTOP_NEWS_ITEMS_PER_QUERY", "12")),
        telegram_summary_minutes=int(os.getenv("GOSTOP_TELEGRAM_SUMMARY_MINUTES", "20")),
        cano=cano,
        acnt_prdt_cd=product,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip() or None,
    )

from __future__ import annotations

import csv
import html
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib import parse, request
from xml.etree import ElementTree

from .config import Settings
from .storage import Store


GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
MARKET_QUERY = "한국 증시 OR 코스피 OR 코스닥 OR 주식"

POSITIVE_KEYWORDS = (
    "호실적",
    "실적 개선",
    "흑자",
    "상승",
    "강세",
    "급등",
    "신고가",
    "수주",
    "계약",
    "승인",
    "증가",
    "목표가 상향",
    "매수",
    "배당",
    "자사주",
    "성장",
)
NEGATIVE_KEYWORDS = (
    "실적 부진",
    "적자",
    "하락",
    "약세",
    "급락",
    "감소",
    "목표가 하향",
    "매도",
    "리콜",
    "소송",
    "제재",
    "압수수색",
    "유상증자",
    "쇼크",
    "중단",
    "경고",
)

EXCLUDED_NEWS_NAME_KEYWORDS = (
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


@dataclass(frozen=True)
class NewsCollectionResult:
    queries: int
    rows: int


class NewsCrawler:
    def __init__(self, settings: Settings, store: Store, pause_seconds: float = 0.4):
        self.settings = settings
        self.store = store
        self.pause_seconds = pause_seconds

    def collect(self, symbols_file: str = "config/symbols.csv") -> NewsCollectionResult:
        symbol_names = load_news_symbol_names(self.store, symbols_file)
        queries: list[tuple[str, str | None, str | None]] = [(MARKET_QUERY, None, None)]
        for symbol, name in list(symbol_names.items())[: self.settings.news_max_symbol_queries]:
            queries.append((f"{name} 주가 OR 실적 OR 수주 OR 증권", symbol, name))

        rows: list[dict[str, Any]] = []
        for query, query_symbol, query_name in queries:
            items = self._fetch_rss(query, self.settings.news_items_per_query)
            for item in items:
                rows.extend(news_rows_for_item(item, query, symbol_names, query_symbol, query_name))
            time.sleep(self.pause_seconds)

        return NewsCollectionResult(queries=len(queries), rows=self.store.insert_news_rows(rows))

    def _fetch_rss(self, query: str, limit: int) -> list[dict[str, Any]]:
        params = parse.urlencode({"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
        req = request.Request(
            f"{GOOGLE_NEWS_RSS}?{params}",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with request.urlopen(req, timeout=self.settings.kis_request_timeout_seconds) as res:
            raw = res.read()

        root = ElementTree.fromstring(raw)
        items: list[dict[str, Any]] = []
        for item in root.findall("./channel/item")[:limit]:
            title = text_of(item, "title")
            description = strip_html(text_of(item, "description"))
            source_node = item.find("source")
            items.append(
                {
                    "title": html.unescape(title),
                    "summary": html.unescape(description),
                    "url": text_of(item, "link"),
                    "source": source_node.text if source_node is not None else "",
                    "published_at": parse_rss_date(text_of(item, "pubDate")),
                }
            )
        return items


def news_rows_for_item(
    item: dict[str, Any],
    query: str,
    symbol_names: dict[str, str],
    query_symbol: str | None = None,
    query_name: str | None = None,
) -> list[dict[str, Any]]:
    text = f"{item.get('title') or ''} {item.get('summary') or ''}"
    matches = matched_symbols(text, symbol_names)
    if query_symbol and query_name and query_symbol not in matches:
        matches[query_symbol] = query_name

    sentiment, reason = score_news(text)
    if not matches:
        return [
            {
                **item,
                "symbol": None,
                "name": None,
                "query": query,
                "sentiment": sentiment,
                "reason": reason,
            }
        ]

    return [
        {
            **item,
            "symbol": symbol,
            "name": name,
            "query": query,
            "sentiment": sentiment,
            "reason": reason,
        }
        for symbol, name in matches.items()
    ]


def matched_symbols(text: str, symbol_names: dict[str, str]) -> dict[str, str]:
    matches: dict[str, str] = {}
    for symbol, name in symbol_names.items():
        clean_name = name.strip()
        if len(clean_name) < 2:
            continue
        if clean_name in text:
            matches[symbol] = clean_name
    return matches


def score_news(text: str) -> tuple[float, str]:
    positive = [keyword for keyword in POSITIVE_KEYWORDS if keyword in text]
    negative = [keyword for keyword in NEGATIVE_KEYWORDS if keyword in text]
    raw_score = len(positive) - len(negative)
    score = max(-1.0, min(1.0, raw_score / 3))
    parts = []
    if positive:
        parts.append("긍정: " + ", ".join(positive[:4]))
    if negative:
        parts.append("부정: " + ", ".join(negative[:4]))
    return score, "; ".join(parts) if parts else "중립 뉴스"


def load_news_symbol_names(store: Store, symbols_file: str) -> dict[str, str]:
    names: dict[str, str] = {}

    with store.connect() as conn:
        holding_rows = conn.execute(
            """
            select symbol, name
            from balance_snapshots
            where collected_at = (select max(collected_at) from balance_snapshots)
              and coalesce(quantity, 0) > 0
              and symbol is not null and name is not null
            """
        ).fetchall()
        affordable_rows = conn.execute(
            """
            select q.symbol, rn.name
            from current_quotes q
            join (
                select symbol, max(collected_at) as collected_at
                from current_quotes
                group by symbol
            ) latest_quote
              on q.symbol = latest_quote.symbol
             and q.collected_at = latest_quote.collected_at
            join (
                select rs.symbol, rs.name
                from ranking_snapshots rs
                join (
                    select symbol, max(collected_at) as collected_at
                    from ranking_snapshots
                    where name is not null
                    group by symbol
                ) latest_name
                  on rs.symbol = latest_name.symbol
                 and rs.collected_at = latest_name.collected_at
            ) rn
              on q.symbol = rn.symbol
            where q.last_price between 1000 and 100000
              and rn.name is not null
            order by abs(coalesce(q.change_rate, 0)) desc, coalesce(q.accumulated_volume, 0) desc
            limit 20
            """
        ).fetchall()
        rows = conn.execute(
            """
            select symbol, name, 2 as priority
            from strategy_signals
            where run_at = (select max(run_at) from strategy_signals)
              and action in ('target', 'watch')
              and symbol is not null and name is not null
            union all
            select symbol, name, 3 as priority
            from ranking_snapshots
            where collected_at in (
                select max(collected_at)
                from ranking_snapshots
                group by source
            )
              and symbol is not null and name is not null
            order by priority
            """
        ).fetchall()

    for row in holding_rows:
        add_symbol_name(names, row["symbol"], row["name"])
    for row in affordable_rows:
        add_symbol_name(names, row["symbol"], row["name"])
    for row in rows:
        add_symbol_name(names, row["symbol"], row["name"])
    for symbol, name in load_symbols_file(symbols_file).items():
        add_symbol_name(names, symbol, name)
    return names


def add_symbol_name(names: dict[str, str], symbol_value: Any, name_value: Any) -> None:
    symbol = str(symbol_value or "")
    name = str(name_value or "").strip()
    if not symbol.isdigit() or not name or symbol in names:
        return
    if any(keyword in name for keyword in EXCLUDED_NEWS_NAME_KEYWORDS):
        return
    if name.endswith("우"):
        return
    names[symbol] = name


def load_symbols_file(path: str | Path) -> dict[str, str]:
    csv_path = Path(path)
    if not csv_path.exists():
        return {}
    with csv_path.open(newline="", encoding="utf-8") as fp:
        return {
            row["symbol"].strip(): row["name"].strip()
            for row in csv.DictReader(fp)
            if row.get("symbol") and row.get("name")
        }


def text_of(item: ElementTree.Element, tag: str) -> str:
    node = item.find(tag)
    return node.text if node is not None and node.text else ""


def strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def parse_rss_date(value: str) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")

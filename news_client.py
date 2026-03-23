"""Market news helpers with NewsAPI primary and RSS fallback."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from config import NEWSAPI_KEY

logger = logging.getLogger(__name__)

NEWS_TIMEOUT = httpx.Timeout(20.0)
RSS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]
SYMBOL_ALIASES = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth", "ether"],
    "SOL": ["solana", "sol"],
    "XRP": ["xrp", "ripple"],
    "DOGE": ["dogecoin", "doge"],
}


def _matches_symbol(text, symbol):
    if not symbol:
        return True
    haystack = (text or "").lower()
    aliases = SYMBOL_ALIASES.get(symbol.upper(), [symbol.lower()])
    return any(alias in haystack for alias in aliases)


def _build_newsapi_query(symbol=None, keywords=None):
    if keywords:
        if isinstance(keywords, (list, tuple)):
            return " OR ".join(str(item).strip() for item in keywords if str(item).strip())
        return str(keywords).strip()
    if symbol:
        aliases = SYMBOL_ALIASES.get(symbol.upper(), [symbol.lower(), symbol.upper()])
        alias_query = " OR ".join(f'"{alias}"' for alias in aliases)
        return f"({alias_query}) AND (crypto OR token OR blockchain)"
    return "(crypto OR bitcoin OR ethereum OR altcoin) AND (market OR price OR breakout OR rally)"


def _dedupe_news(items, limit):
    deduped = []
    seen = set()
    for item in items:
        key = (item.get("title", "").strip().lower(), item.get("url", "").strip())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


async def _fetch_newsapi(symbol=None, keywords=None, limit=8):
    if not NEWSAPI_KEY:
        return []

    params = {
        "q": _build_newsapi_query(symbol=symbol, keywords=keywords),
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": max(limit * 2, 10),
        "from": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
    }

    try:
        async with httpx.AsyncClient(timeout=NEWS_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(
                "https://newsapi.org/v2/everything",
                headers={"X-Api-Key": NEWSAPI_KEY},
                params=params,
            )
            response.raise_for_status()
    except Exception as exc:
        logger.warning(f"NewsAPI fetch failed: {exc}")
        return []

    payload = response.json()
    articles = []
    for article in payload.get("articles", []):
        title = (article.get("title") or "").strip()
        if not title:
            continue
        articles.append({
            "title": title,
            "url": article.get("url", ""),
            "source": article.get("source", {}).get("name", "NewsAPI"),
            "published_at": article.get("publishedAt", ""),
            "summary": article.get("description", "") or "",
        })
    return _dedupe_news(articles, limit)


async def _fetch_rss(symbol=None, limit=8):
    articles = []
    try:
        async with httpx.AsyncClient(timeout=NEWS_TIMEOUT, follow_redirects=True) as client:
            responses = await asyncio.gather(
                *(client.get(url) for url in RSS_FEEDS),
                return_exceptions=True,
            )
    except Exception as exc:
        logger.warning(f"RSS fetch failed: {exc}")
        return []

    for response in responses:
        if isinstance(response, Exception):
            continue
        if response.status_code >= 400:
            continue
        feed = feedparser.parse(response.text)
        for entry in getattr(feed, "entries", []):
            title = getattr(entry, "title", "").strip()
            summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
            if not title:
                continue
            if not _matches_symbol(f"{title} {summary}", symbol):
                continue
            articles.append({
                "title": title,
                "url": getattr(entry, "link", ""),
                "source": getattr(feed.feed, "title", "RSS"),
                "published_at": getattr(entry, "published", ""),
                "summary": summary,
            })

    return _dedupe_news(articles, limit)


async def get_market_news(symbol=None, keywords=None, limit=8):
    """Fetch market news for /news and daily brief output."""
    articles = await _fetch_newsapi(symbol=symbol, keywords=keywords, limit=limit)
    if articles:
        return articles
    return await _fetch_rss(symbol=symbol, limit=limit)


def summarize_headlines(articles, limit=3):
    """Create short headline lines for briefing messages."""
    lines = []
    for article in articles[:limit]:
        source = article.get("source", "?")
        title = article.get("title", "?")
        lines.append(f"[{source}] {title}")
    return lines

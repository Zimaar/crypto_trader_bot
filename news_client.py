"""Market news helpers with NewsAPI primary and RSS fallback."""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from urllib.parse import urlparse

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
CRYPTO_TERMS = {
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto", "token",
    "blockchain", "defi", "altcoin", "exchange", "stablecoin", "wallet",
}
MARKET_TERMS = {
    "etf", "sec", "liquidity", "breakout", "rally", "selloff", "surge",
    "drops", "plunge", "bull", "bear", "inflows", "outflows", "volatility",
}
MACRO_TERMS = {
    "fed", "inflation", "rates", "macro", "tariff", "recession", "gold",
    "stocks", "equities", "treasury", "liquidity",
}
HIGH_QUALITY_SOURCES = {
    "coindesk", "cointelegraph", "decrypt", "the block", "bitcoinist",
    "bloomberg", "reuters", "financial times", "forbes", "cnbc",
    "financial post", "businessline",
}
DENYLIST_DOMAINS = {
    "pypi.org",
    "fxbackoffice.com",
    "prnewswire.com",
    "globenewswire.com",
    "accesswire.com",
    "openpr.com",
}
DENYLIST_SOURCE_TERMS = {
    "pypi", "fxbackoffice", "accesswire", "pr newswire", "globe newswire", "openpr",
}
LOW_SIGNAL_PATTERNS = {
    " mcp", " sdk", " package", " pypi", " marketing", " forex broker",
    " sponsored", " press release", "launches new version", "from zero to hero",
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
    return "(crypto OR bitcoin OR ethereum OR altcoin) AND (market OR price OR breakout OR rally OR etf OR liquidity)"


def _dedupe_news(items):
    deduped = []
    seen = set()
    for item in items:
        key = (item.get("title", "").strip().lower(), item.get("url", "").strip())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _extract_domain(url):
    if not url:
        return ""
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def _text_blob(article):
    title = article.get("title", "") or ""
    summary = article.get("summary", "") or ""
    return f"{title} {summary}".strip().lower()


def _contains_any(text, patterns):
    return any(pattern in text for pattern in patterns)


def _parse_published_at(value):
    if not value:
        return None
    cleaned = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _is_article_relevant(article, symbol=None):
    source = (article.get("source", "") or "").lower()
    domain = _extract_domain(article.get("url", ""))
    text = f" {_text_blob(article)} "

    if domain in DENYLIST_DOMAINS:
        return False
    if _contains_any(source, DENYLIST_SOURCE_TERMS):
        return False
    if _contains_any(text, LOW_SIGNAL_PATTERNS):
        return False
    if symbol and not _matches_symbol(text, symbol):
        return False

    has_crypto = _contains_any(text, CRYPTO_TERMS)
    has_market = _contains_any(text, MARKET_TERMS)
    has_macro = _contains_any(text, MACRO_TERMS)

    if symbol:
        return has_crypto or has_market
    return has_crypto or (has_macro and has_market)


def _score_article(article, symbol=None):
    if not _is_article_relevant(article, symbol=symbol):
        return None

    source = (article.get("source", "") or "").lower()
    text = _text_blob(article)
    score = 0.0

    if any(name in source for name in HIGH_QUALITY_SOURCES):
        score += 3
    elif source:
        score += 1

    if symbol and _matches_symbol(text, symbol):
        score += 4

    score += sum(1 for term in CRYPTO_TERMS if term in text)
    score += 0.75 * sum(1 for term in MARKET_TERMS if term in text)
    score += 0.5 * sum(1 for term in MACRO_TERMS if term in text)

    published_at = _parse_published_at(article.get("published_at"))
    if published_at:
        age_hours = (_utc_now() - published_at).total_seconds() / 3600
        if age_hours <= 6:
            score += 1.5
        elif age_hours <= 24:
            score += 0.5

    return score


def _rank_news(items, symbol=None, limit=8):
    scored = []
    for item in _dedupe_news(items):
        score = _score_article(item, symbol=symbol)
        if score is None:
            continue
        item["relevance_score"] = round(score, 2)
        scored.append(item)

    scored.sort(
        key=lambda item: (
            item.get("relevance_score", 0),
            item.get("published_at", ""),
        ),
        reverse=True,
    )
    return scored[:limit]


def _utc_now():
    return datetime.now(timezone.utc)


async def _fetch_newsapi(symbol=None, keywords=None, limit=20):
    if not NEWSAPI_KEY:
        return []

    params = {
        "q": _build_newsapi_query(symbol=symbol, keywords=keywords),
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": max(limit, 10),
        "from": (_utc_now() - timedelta(days=3)).isoformat(),
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
    return articles


async def _fetch_rss(symbol=None, limit=20):
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
        if isinstance(response, Exception) or response.status_code >= 400:
            continue
        feed = feedparser.parse(response.text)
        for entry in getattr(feed, "entries", []):
            title = getattr(entry, "title", "").strip()
            summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
            if not title:
                continue
            articles.append({
                "title": title,
                "url": getattr(entry, "link", ""),
                "source": getattr(feed.feed, "title", "RSS"),
                "published_at": getattr(entry, "published", ""),
                "summary": summary,
            })
            if len(articles) >= limit * len(RSS_FEEDS):
                break
    return articles


async def get_market_news(symbol=None, keywords=None, limit=8):
    """Fetch and rank market news for /news and briefing output."""
    raw_newsapi = await _fetch_newsapi(symbol=symbol, keywords=keywords, limit=limit * 3)
    ranked_newsapi = _rank_news(raw_newsapi, symbol=symbol, limit=limit)
    if ranked_newsapi:
        return ranked_newsapi

    raw_rss = await _fetch_rss(symbol=symbol, limit=limit * 3)
    return _rank_news(raw_rss, symbol=symbol, limit=limit)


def summarize_headlines(articles, limit=3):
    """Create short headline lines for briefing messages."""
    lines = []
    for article in articles[:limit]:
        source = article.get("source", "?")
        title = article.get("title", "?")
        lines.append(f"[{source}] {title}")
    return lines

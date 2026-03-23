"""Geopolitical sentiment scoring — tracks war, sanctions, macro headlines and adjusts signal quality."""

import logging
import httpx
import feedparser
from datetime import datetime, timezone
from config import NEWSAPI_KEY
from database import log_geo, get_last_geo_score

logger = logging.getLogger(__name__)

ESCALATION_KEYWORDS = [
    "strike", "retaliation", "attack", "missile", "bomb", "airstrike",
    "sanctions", "nuclear", "troops deployed", "closure", "blockade",
    "invasion", "escalation", "war ", "warplane", "strait of hormuz",
    "military operation", "casualties", "conflict intensif",
]
DEESCALATION_KEYWORDS = [
    "ceasefire", "peace talks", "peace deal", "reduced operations",
    "diplomatic", "withdrawal", "troop withdrawal", "agreement",
    "de-escalat", "scaling back", "negotiate", "treaty", "end of war",
    "resolution", "calm", "stand down",
]


def _count_matches(text, keywords):
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw.lower() in text_lower)


async def _fetch_headlines_newsapi():
    """Fetch geopolitical headlines from NewsAPI."""
    if not NEWSAPI_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": "(Iran OR war OR sanctions OR geopolitical OR tariff) AND (crypto OR bitcoin OR market)",
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": 20,
                    "apiKey": NEWSAPI_KEY,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                return [
                    a.get("title", "") + " " + (a.get("description", "") or "")
                    for a in data.get("articles", [])
                ]
    except Exception as e:
        logger.warning(f"NewsAPI error: {e}")
    return []


async def _fetch_headlines_rss():
    """Fallback: fetch headlines from RSS feeds (no API key needed)."""
    feeds = [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
    ]
    headlines = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for url in feeds:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        feed = feedparser.parse(resp.text)
                        for entry in feed.entries[:10]:
                            title = entry.get("title", "")
                            summary = entry.get("summary", "")
                            headlines.append(f"{title} {summary}")
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"RSS fetch error: {e}")
    return headlines


async def calculate_geo_score(btc_change_24h=0.0):
    """
    Calculate geopolitical sentiment score from -3 to +3.
    Combines headline sentiment + BTC price action.

    Returns (score, summary_headlines)
    """
    # Gather headlines from multiple sources
    headlines = await _fetch_headlines_newsapi()
    if not headlines:
        headlines = await _fetch_headlines_rss()

    all_text = " ".join(headlines)
    esc_count = _count_matches(all_text, ESCALATION_KEYWORDS)
    deesc_count = _count_matches(all_text, DEESCALATION_KEYWORDS)

    score = 0

    # Headline sentiment
    if deesc_count > esc_count + 3:
        score += 2
    elif deesc_count > esc_count + 1:
        score += 1
    elif esc_count > deesc_count + 3:
        score -= 2
    elif esc_count > deesc_count + 1:
        score -= 1

    # BTC price action as fear barometer
    if btc_change_24h < -5:
        score -= 1
    elif btc_change_24h > 5:
        score += 1

    score = max(-3, min(3, score))

    # Summary of relevant headlines (top 5 most relevant)
    relevant = []
    for h in headlines[:20]:
        h_lower = h.lower()
        if any(kw.lower() in h_lower for kw in ESCALATION_KEYWORDS + DEESCALATION_KEYWORDS):
            relevant.append(h[:120])
        if len(relevant) >= 5:
            break

    # Log to database
    await log_geo(score, relevant, btc_change_24h)

    logger.info(f"Geo Score: {score} | Escalation: {esc_count} | De-escalation: {deesc_count} | BTC 24h: {btc_change_24h:.1f}%")

    return score, relevant


GEO_LABELS = {
    3: ("🟢 STRONG DE-ESCALATION", "All signals boosted. Breakouts more likely to hold."),
    2: ("🟢 DE-ESCALATION SIGNALS", "Signals boosted +1. Normal positioning."),
    1: ("🟡 CALM", "No adjustment. Business as usual."),
    0: ("⚪ MIXED", "Uncertainty. Be selective."),
    -1: ("🟠 ESCALATION RHETORIC", "Signals reduced -1. Smaller sizes recommended."),
    -2: ("🔴 ACTIVE ESCALATION", "Only highest conviction signals pass."),
    -3: ("🔴🔴 MAJOR ESCALATION", "Bot suppressing buy signals. Extreme caution."),
}


def geo_label(score):
    return GEO_LABELS.get(score, GEO_LABELS[0])

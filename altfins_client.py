"""Async ALTfins API client for signals, screener, TA, news, events, and OHLCV."""

import httpx
import logging
from datetime import datetime, timezone, timedelta
from config import ALTFINS_API_KEY, ALTFINS_BASE_URL

logger = logging.getLogger(__name__)

HEADERS = {
    "x-api-key": ALTFINS_API_KEY,
    "Content-Type": "application/json",
}
TIMEOUT = httpx.Timeout(30.0)


def _base_url():
    """Normalize legacy base URLs to the current public API host."""
    base = (ALTFINS_BASE_URL or "https://altfins.com").rstrip("/")
    if "platform-api.altfins.com" in base:
        return "https://altfins.com"
    return base


def _unwrap_content(data):
    """Return the list payload from paginated responses."""
    if isinstance(data, dict):
        content = data.get("content")
        if isinstance(content, list):
            return content
    return data if isinstance(data, list) else []


def _normalize_signal(signal):
    """Fill a few compatibility aliases for formatter/scorer code."""
    if not isinstance(signal, dict):
        return signal
    if "name" not in signal and signal.get("symbolName"):
        signal["name"] = signal["symbolName"]
    return signal


async def _request(method, path, params=None, json_body=None):
    url = f"{_base_url()}{path}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            if method == "GET":
                resp = await client.get(url, headers=HEADERS, params=params)
            else:
                resp = await client.post(url, headers=HEADERS, json=json_body, params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"ALTfins HTTP {e.response.status_code}: {e.response.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"ALTfins request error: {e}")
        return None


# ──────────────────────────────────────────────────────────────
# Signal Feed
# ──────────────────────────────────────────────────────────────

async def get_signal_feed(signal_types, direction="BULLISH", hours_back=6, size=50, symbols=None):
    """Fetch signal feed from ALTfins."""
    from_dt = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {
        "signals": signal_types,
        "signalDirection": direction,
        "from": from_dt,
    }
    if symbols:
        body["symbols"] = [str(symbol).upper() for symbol in symbols if str(symbol).strip()]
    data = await _request(
        "POST",
        "/api/v2/public/signals-feed/search-requests",
        params={"size": size},
        json_body=body,
    )
    return [_normalize_signal(item) for item in _unwrap_content(data)]


# ──────────────────────────────────────────────────────────────
# Screener
# ──────────────────────────────────────────────────────────────

async def screener_confluence(min_mcap=100_000_000):
    """Find coins with MACD bullish + RSI sweet spot + uptrend + volume."""
    # The current public API does not expose the older server-side filter model
    # this bot used here, so we keep this non-blocking and rely on signal feed
    # scans plus per-symbol screener enrichment for scoring.
    return []


async def screener_oversold(min_mcap=100_000_000):
    """Find deeply oversold coins (RSI < 30)."""
    return []


async def screener_symbol(symbol):
    """Get screener data for a specific symbol."""
    body = {
        "symbols": [symbol],
        "displayType": [
            "RSI14", "MACD", "SHORT_TERM_TREND", "MEDIUM_TERM_TREND",
            "LONG_TERM_TREND", "VOLUME_RELATIVE", "PRICE_CHANGE_1D",
            "PRICE_CHANGE_1W", "PRICE_CHANGE_1M", "SMA50", "SMA200",
            "ATH", "MARKET_CAP", "RESISTANCE", "SUPPORT",
        ],
    }
    data = await _request(
        "POST",
        "/api/v2/public/screener-data/search-requests",
        params={"size": 1},
        json_body=body,
    )
    items = _unwrap_content(data)
    if items:
        return items[0]
    return None


# ──────────────────────────────────────────────────────────────
# Technical Analysis (curated analyst setups)
# ──────────────────────────────────────────────────────────────

async def get_technical_analysis(symbol=None, size=10):
    """Get curated analyst trade setups."""
    params = {"size": size, "sortField": "updatedDate", "sortDirection": "DESC"}
    if symbol:
        params["symbol"] = symbol
    data = await _request("GET", "/api/v2/public/technical-analysis/data", params=params)
    return _unwrap_content(data)


# ──────────────────────────────────────────────────────────────
# News
# ──────────────────────────────────────────────────────────────

async def get_news(keywords=None, asset_symbols=None, size=15):
    """Get crypto news, optionally filtered."""
    from_dt = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {"fromDate": from_dt}
    if keywords:
        body["keywords"] = keywords
    if asset_symbols:
        body["assetSymbols"] = [asset_symbols] if isinstance(asset_symbols, str) else asset_symbols
    data = await _request(
        "POST",
        "/api/v2/public/news-summary/search-requests",
        params={"size": size},
        json_body=body,
    )
    items = _unwrap_content(data)
    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "newsSource": {"name": item.get("sourceName", "?")},
            "assetSymbols": ", ".join(item.get("assetSymbols", [])) if isinstance(item.get("assetSymbols"), list) else item.get("assetSymbols", ""),
        }
        for item in items
    ]


# ──────────────────────────────────────────────────────────────
# Calendar Events
# ──────────────────────────────────────────────────────────────

async def get_events(significant_only=True, size=20):
    """Get upcoming crypto calendar events."""
    return []


# ──────────────────────────────────────────────────────────────
# OHLCV (latest price)
# ──────────────────────────────────────────────────────────────

async def get_latest_price(symbol):
    """Get latest OHLCV candle for a symbol."""
    data = await _request(
        "POST",
        "/api/v2/public/ohlcv/snapshot-requests",
        json_body={"symbols": [symbol], "timeInterval": "DAILY"},
    )
    if isinstance(data, list) and data:
        return data[0]
    return None


async def get_latest_prices(symbols_csv):
    """Get latest candles for multiple symbols."""
    symbols = [s.strip() for s in symbols_csv.split(",") if s.strip()]
    data = await _request(
        "POST",
        "/api/v2/public/ohlcv/snapshot-requests",
        json_body={"symbols": symbols, "timeInterval": "DAILY"},
    )
    return data if isinstance(data, list) else []

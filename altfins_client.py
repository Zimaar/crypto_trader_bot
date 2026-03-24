"""Async ALTfins API client for signals, screener snapshots, and OHLCV."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import time

import httpx

from config import ALTFINS_API_KEY, ALTFINS_BASE_URL

logger = logging.getLogger(__name__)

HEADERS = {
    "x-api-key": ALTFINS_API_KEY,
    "Content-Type": "application/json",
}
TIMEOUT = httpx.Timeout(30.0)
REQUEST_RETRY_ATTEMPTS = 2
REQUEST_RETRY_BACKOFF_SECONDS = 0.8
REQUEST_SEMAPHORE = asyncio.Semaphore(4)
SCREENER_CACHE_TTL_SECONDS = 900
SIGNAL_FEED_CACHE_TTL_SECONDS = 180

_screener_cache = {}
_signal_feed_cache = {}


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


def _cache_get(cache, key, max_age_seconds):
    entry = cache.get(key)
    if not entry:
        return None, None
    age_seconds = time.time() - entry["ts"]
    if age_seconds > max_age_seconds:
        return None, age_seconds
    return entry["data"], age_seconds


def _cache_set(cache, key, data):
    cache[key] = {
        "data": data,
        "ts": time.time(),
    }


def _feed_cache_key(path, params, json_body):
    return json.dumps(
        {
            "path": path,
            "params": params or {},
            "json_body": json_body or {},
        },
        sort_keys=True,
        separators=(",", ":"),
    )


async def _request(method, path, params=None, json_body=None):
    url = f"{_base_url()}{path}"
    for attempt in range(REQUEST_RETRY_ATTEMPTS + 1):
        try:
            async with REQUEST_SEMAPHORE:
                async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                    if method == "GET":
                        resp = await client.get(url, headers=HEADERS, params=params)
                    else:
                        resp = await client.post(url, headers=HEADERS, json=json_body, params=params)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            retryable = status_code in {429, 500, 502, 503, 504}
            if retryable and attempt < REQUEST_RETRY_ATTEMPTS:
                delay = REQUEST_RETRY_BACKOFF_SECONDS * (attempt + 1)
                logger.warning(
                    "ALTfins HTTP %s on %s %s. Retrying in %.1fs.",
                    status_code,
                    method,
                    path,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            logger.error("ALTfins HTTP %s: %s", status_code, e.response.text[:200])
            return None
        except Exception as e:
            if attempt < REQUEST_RETRY_ATTEMPTS:
                delay = REQUEST_RETRY_BACKOFF_SECONDS * (attempt + 1)
                logger.warning(
                    "ALTfins request error on %s %s: %s. Retrying in %.1fs.",
                    method,
                    path,
                    e,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            logger.error("ALTfins request error: %s", e)
            return None


# ──────────────────────────────────────────────────────────────
# Signal Feed
# ──────────────────────────────────────────────────────────────

async def get_signal_feed(
    signal_types,
    direction="BULLISH",
    hours_back=6,
    size=50,
    symbols=None,
    *,
    prefer_cache=False,
    cache_ttl_seconds=SIGNAL_FEED_CACHE_TTL_SECONDS,
):
    """Fetch signal feed from ALTfins."""
    from_dt = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {
        "signals": signal_types,
        "signalDirection": direction,
        "from": from_dt,
    }
    if symbols:
        body["symbols"] = [str(symbol).upper() for symbol in symbols if str(symbol).strip()]
    cache_key = _feed_cache_key(
        "/api/v2/public/signals-feed/search-requests",
        {"size": size},
        body,
    )

    if prefer_cache:
        cached_rows, _ = _cache_get(_signal_feed_cache, cache_key, cache_ttl_seconds)
        if cached_rows is not None:
            logger.info("Using cached ALTfins signal feed for command request.")
            return [_normalize_signal(dict(item)) for item in cached_rows]

    data = await _request(
        "POST",
        "/api/v2/public/signals-feed/search-requests",
        params={"size": size},
        json_body=body,
    )
    if data is not None:
        rows = [_normalize_signal(item) for item in _unwrap_content(data)]
        _cache_set(_signal_feed_cache, cache_key, rows)
        return rows

    cached_rows, age_seconds = _cache_get(_signal_feed_cache, cache_key, cache_ttl_seconds)
    if cached_rows is not None:
        logger.warning(
            "ALTfins signal feed unavailable. Using cached feed from %.0fs ago.",
            age_seconds,
        )
        return [_normalize_signal(dict(item)) for item in cached_rows]

    return []


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


async def screener_symbol(symbol, *, prefer_cache=False, return_meta=False):
    """Get screener data for a specific symbol."""
    normalized_symbol = str(symbol).upper()
    cached_snapshot, cached_age = _cache_get(
        _screener_cache,
        normalized_symbol,
        SCREENER_CACHE_TTL_SECONDS,
    )
    if prefer_cache and cached_snapshot is not None:
        logger.info("Using cached screener snapshot for %s.", normalized_symbol)
        result = {
            "data": cached_snapshot,
            "source": "cache",
            "age_seconds": cached_age,
        }
        return result if return_meta else cached_snapshot

    body = {
        "symbols": [normalized_symbol],
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
    if data is not None:
        items = _unwrap_content(data)
        if items:
            snapshot = items[0]
            _cache_set(_screener_cache, normalized_symbol, snapshot)
            result = {
                "data": snapshot,
                "source": "live",
                "age_seconds": 0.0,
            }
            return result if return_meta else snapshot
        result = {
            "data": None,
            "source": "live",
            "age_seconds": None,
        }
        return result if return_meta else None

    if cached_snapshot is not None:
        logger.warning(
            "ALTfins screener unavailable for %s. Using cached snapshot from %.0fs ago.",
            normalized_symbol,
            cached_age,
        )
        result = {
            "data": cached_snapshot,
            "source": "cache",
            "age_seconds": cached_age,
        }
        return result if return_meta else cached_snapshot

    result = {
        "data": None,
        "source": "unavailable",
        "age_seconds": None,
    }
    return result if return_meta else None


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

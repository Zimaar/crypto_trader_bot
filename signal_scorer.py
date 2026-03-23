"""Signal scoring engine — multi-layer confirmation + confluence detection."""

import logging
from config import SIGNAL_WEIGHTS, MIN_MARKET_CAP
from database import get_recent_signals

logger = logging.getLogger(__name__)


def parse_mcap(mcap_str):
    """Parse market cap from ALTfins signal data."""
    if isinstance(mcap_str, (int, float)):
        return float(mcap_str)
    if isinstance(mcap_str, str):
        return float(mcap_str.replace(",", "").replace("$", "").strip())
    return 0


def parse_trend_score(trend_str):
    """Parse trend string like 'Up (7/10)' or 'Strong Up (10/10)' → int."""
    if not trend_str:
        return 5
    trend_str = str(trend_str)
    if "Strong Up" in trend_str or "Strong_Up" in trend_str:
        return 9
    if "Up" in trend_str:
        return 7
    if "Neutral" in trend_str:
        return 5
    if "Strong Down" in trend_str or "Strong_Down" in trend_str:
        return 1
    if "Down" in trend_str:
        return 3
    return 5


async def score_signal(signal, screener_data=None, geo_score=0):
    """
    Score a signal from 0–10 based on:
    1. Signal type weight
    2. Trend alignment (screener)
    3. RSI sweet spot
    4. Volume confirmation
    5. Market cap filter
    6. Confluence bonus (multiple signals same coin)
    7. Geopolitical adjustment

    Returns (ta_score, adjusted_score, details_dict)
    """
    details = {}
    score = 0

    # --- Layer 1: Signal type base weight ---
    signal_key = signal.get("signalKey", "").replace(".TXT", "")
    base = SIGNAL_WEIGHTS.get(signal_key, 2)
    score += base
    details["signal_type"] = signal_key
    details["base_weight"] = base

    # --- Market cap filter ---
    mcap = parse_mcap(signal.get("marketCap", 0))
    if mcap < MIN_MARKET_CAP:
        details["mcap_penalty"] = True
        details["market_cap"] = mcap
        return 0, 0, details  # Hard filter — skip microcaps entirely
    details["market_cap"] = mcap

    # --- Layer 2: Screener confirmation (if available) ---
    if screener_data and isinstance(screener_data, dict):
        add_data = screener_data.get("additionalData", screener_data)

        # Trend alignment
        med_trend = add_data.get("MEDIUM_TERM_TREND", "")
        short_trend = add_data.get("SHORT_TERM_TREND", "")
        med_score = parse_trend_score(med_trend)
        short_score = parse_trend_score(short_trend)

        if med_score >= 7:
            score += 2
            details["med_trend_bonus"] = 2
        if short_score >= 7:
            score += 1
            details["short_trend_bonus"] = 1

        details["medium_trend"] = med_trend
        details["short_trend"] = short_trend

        # RSI sweet spot
        rsi_str = add_data.get("RSI14", "50")
        try:
            rsi = float(rsi_str)
        except (ValueError, TypeError):
            rsi = 50
        details["rsi"] = rsi

        if 40 <= rsi <= 65:
            score += 1
            details["rsi_bonus"] = 1
        elif rsi > 75:
            score -= 2
            details["rsi_penalty"] = -2
        elif rsi < 25:
            # Oversold — can be a bounce opportunity
            score += 1
            details["rsi_oversold_bonus"] = 1

        # Volume confirmation
        vol_str = add_data.get("VOLUME_RELATIVE", "1.0")
        try:
            vol_rel = float(vol_str)
        except (ValueError, TypeError):
            vol_rel = 1.0
        details["volume_relative"] = vol_rel

        if vol_rel > 2.5:
            score += 2
            details["volume_bonus"] = 2
        elif vol_rel > 1.3:
            score += 1
            details["volume_bonus"] = 1

    # --- Layer 3: Confluence bonus ---
    symbol = signal.get("symbol", "")
    recent_count = await get_recent_signals(symbol, hours=24)
    if recent_count >= 3:
        score += 2
        details["confluence_bonus"] = 2
        details["confluence_count"] = recent_count
    elif recent_count >= 2:
        score += 1
        details["confluence_bonus"] = 1
        details["confluence_count"] = recent_count

    # Cap TA score at 10
    ta_score = min(max(score, 0), 10)
    details["ta_score"] = ta_score

    # --- Layer 4: Geopolitical adjustment ---
    geo_adj = 0
    if geo_score <= -2:
        geo_adj = -3
    elif geo_score == -1:
        geo_adj = -1
    elif geo_score >= 2:
        geo_adj = 1
    adjusted = min(max(ta_score + geo_adj, 0), 10)
    details["geo_score"] = geo_score
    details["geo_adjustment"] = geo_adj
    details["adjusted_score"] = adjusted

    return ta_score, adjusted, details

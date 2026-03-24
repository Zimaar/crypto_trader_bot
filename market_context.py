"""BTC-led market context helpers used to tighten alert quality."""

import copy
import logging
import time

from altfins_client import screener_symbol
from signal_scorer import parse_trend_score

logger = logging.getLogger(__name__)
MARKET_CONTEXT_CACHE_TTL_SECONDS = 120
_market_context_cache = {
    "data": None,
    "ts": 0.0,
}


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _default_context():
    return {
        "regime": "neutral",
        "label": "Neutral",
        "summary": "BTC tape unavailable. Standard confirmation thresholds stay active.",
        "snapshot": "BTC tape unavailable.",
        "tape_line": "BTC Tape: Unavailable",
        "filter_line": "Filter Active: Min medium trend 5/10, min relative volume 0.90x",
        "premium_filter_line": "BTC Filter: Neutral | Standard thresholds active",
        "reasons": [],
        "alert_threshold_delta": 0,
        "min_volume_relative": 0.9,
        "min_medium_trend": 5,
        "btc_rsi": None,
        "btc_change_1d": None,
        "btc_change_1w": None,
    }


def _get_cached_market_context():
    data = _market_context_cache.get("data")
    if not data:
        return None, None
    age_seconds = time.time() - _market_context_cache["ts"]
    if age_seconds > MARKET_CONTEXT_CACHE_TTL_SECONDS:
        return None, age_seconds
    return copy.deepcopy(data), age_seconds


def _set_cached_market_context(context):
    _market_context_cache["data"] = copy.deepcopy(context)
    _market_context_cache["ts"] = time.time()


async def get_market_context(*, prefer_cache=False):
    """Build a simple BTC-led market regime for alert gating."""
    cached_context, cached_age = _get_cached_market_context()
    if prefer_cache and cached_context:
        return cached_context

    screener_response = await screener_symbol("BTC", prefer_cache=prefer_cache, return_meta=True)
    screener = (screener_response or {}).get("data")
    if not screener:
        if cached_context:
            logger.warning(
                "Using cached BTC market context from %.0fs ago after ALTfins degradation.",
                cached_age,
            )
            return cached_context
        return _default_context()

    add = screener.get("additionalData", screener)
    medium_trend = add.get("MEDIUM_TERM_TREND", "")
    long_trend = add.get("LONG_TERM_TREND", "")
    medium_score = parse_trend_score(medium_trend)
    long_score = parse_trend_score(long_trend)
    rsi = _safe_float(add.get("RSI14"), 50.0)
    change_1d = _safe_float(add.get("PRICE_CHANGE_1D"), 0.0)
    change_1w = _safe_float(add.get("PRICE_CHANGE_1W"), 0.0)

    context = {
        "btc_rsi": rsi,
        "btc_change_1d": change_1d,
        "btc_change_1w": change_1w,
        "reasons": [],
        "btc_medium_score": medium_score,
        "btc_long_score": long_score,
        "btc_medium_trend": medium_trend or "Neutral",
        "btc_long_trend": long_trend or "Neutral",
    }

    bearish_reasons = []
    if medium_score <= 4:
        bearish_reasons.append(f"medium trend {medium_score}/10")
    if long_score <= 4:
        bearish_reasons.append(f"long trend {long_score}/10")
    if change_1d <= -3:
        bearish_reasons.append(f"1D {change_1d:+.1f}%")
    if change_1w <= -8:
        bearish_reasons.append(f"1W {change_1w:+.1f}%")

    bullish_reasons = []
    if medium_score >= 7:
        bullish_reasons.append(f"medium trend {medium_score}/10")
    if long_score >= 7:
        bullish_reasons.append(f"long trend {long_score}/10")
    if change_1w > 0:
        bullish_reasons.append(f"1W {change_1w:+.1f}%")

    severe_risk_off = (
        change_1d <= -5
        or change_1w <= -10
        or (medium_score <= 3 and long_score <= 4)
    )

    if medium_score >= 7 and long_score >= 7 and rsi >= 45 and change_1w > -3:
        context.update({
            "regime": "risk_on",
            "label": "Risk-on",
            "summary": (
                "Risk appetite is supportive. Standard breakout thresholds apply."
            ),
            "premium_filter_line": "BTC Filter: Risk-On | Supportive backdrop",
            "reasons": bullish_reasons,
            "alert_threshold_delta": 0,
            "min_volume_relative": 0.8,
            "min_medium_trend": 5,
        })
    elif severe_risk_off or len(bearish_reasons) >= 2:
        context.update({
            "regime": "risk_off",
            "label": "Cautious",
            "summary": (
                "Backdrop is cautious."
                + (f" Pressure shows in {', '.join(bearish_reasons[:2])}." if bearish_reasons else "")
                + " Only higher-quality trend and liquidity setups qualify."
            ),
            "premium_filter_line": "BTC Filter: Cautious | Higher confirmation required",
            "reasons": bearish_reasons,
            "alert_threshold_delta": 1,
            "min_volume_relative": 1.1,
            "min_medium_trend": 6,
        })
    else:
        context.update({
            "regime": "neutral",
            "label": "Neutral",
            "summary": "No broad market tailwind. Trade selection should lean on coin-specific strength.",
            "premium_filter_line": "BTC Filter: Neutral | Standard thresholds active",
            "alert_threshold_delta": 0,
            "min_volume_relative": 0.9,
            "min_medium_trend": 5,
        })

    context["snapshot"] = (
        f"BTC Tape: Medium {medium_score}/10 | Long {long_score}/10 | RSI {rsi:.1f} | "
        f"1D {change_1d:+.1f}% | 1W {change_1w:+.1f}%"
    )
    context["tape_line"] = context["snapshot"]
    context["filter_line"] = (
        f"Filter Active: Min medium trend {context['min_medium_trend']}/10, "
        f"min relative volume {context['min_volume_relative']:.2f}x"
    )
    if screener_response and screener_response.get("source") == "cache":
        logger.warning(
            "BTC market context built from cached ALTfins data (%.0fs old).",
            screener_response.get("age_seconds") or 0,
        )
    _set_cached_market_context(context)
    return context


def signal_passes_context_gate(screener_data, market_context):
    """Require stronger setups when BTC market context is weak."""
    context = market_context or _default_context()
    if not screener_data:
        if context["regime"] == "risk_off":
            return False, ["screener data unavailable in risk-off conditions"]
        return True, []

    add = screener_data.get("additionalData", screener_data)
    volume_relative = _safe_float(add.get("VOLUME_RELATIVE"), 1.0)
    medium_trend = add.get("MEDIUM_TERM_TREND", "")
    medium_score = parse_trend_score(medium_trend)

    reasons = []
    min_volume = context.get("min_volume_relative", 0.9)
    min_medium_trend = context.get("min_medium_trend", 5)

    if volume_relative < min_volume:
        reasons.append(f"relative volume {volume_relative:.2f}x < {min_volume:.2f}x")
    if medium_score < min_medium_trend:
        reasons.append(f"medium trend {medium_score}/10 < {min_medium_trend}/10")

    return not reasons, reasons

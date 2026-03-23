"""BTC-led market context helpers used to tighten alert quality."""

from altfins_client import screener_symbol
from signal_scorer import parse_trend_score


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _default_context():
    return {
        "regime": "neutral",
        "label": "Neutral",
        "summary": "BTC context is unavailable, so the bot is using its standard confirmation rules.",
        "snapshot": "BTC context unavailable.",
        "reasons": [],
        "alert_threshold_delta": 0,
        "min_volume_relative": 0.9,
        "min_medium_trend": 5,
        "btc_rsi": None,
        "btc_change_1d": None,
        "btc_change_1w": None,
    }


async def get_market_context():
    """Build a simple BTC-led market regime for alert gating."""
    screener = await screener_symbol("BTC")
    if not screener:
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
                "BTC trend is supportive"
                + (f" ({', '.join(bullish_reasons[:2])})" if bullish_reasons else "")
                + ", so clean breakout setups can alert with standard conviction."
            ),
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
                "BTC is guarded right now"
                + (f" because of {', '.join(bearish_reasons[:2])}" if bearish_reasons else "")
                + ", so the bot requires stronger trend and volume confirmation before alerting."
            ),
            "reasons": bearish_reasons,
            "alert_threshold_delta": 1,
            "min_volume_relative": 1.1,
            "min_medium_trend": 6,
        })
    else:
        context.update({
            "regime": "neutral",
            "label": "Neutral",
            "summary": "BTC is mixed, so the bot waits for solid trend and liquidity confirmation.",
            "alert_threshold_delta": 0,
            "min_volume_relative": 0.9,
            "min_medium_trend": 5,
        })

    context["snapshot"] = (
        f"BTC Medium {medium_trend or 'Neutral'} | Long {long_trend or 'Neutral'}, RSI {rsi:.1f}, "
        f"1D {change_1d:+.1f}%, 1W {change_1w:+.1f}%"
    )
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

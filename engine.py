"""Core engine — polls ALTfins, scores signals, checks geo, sends alerts."""

import logging
from datetime import datetime, timezone
from config import (
    ALL_SIGNAL_TYPES, SIGNAL_TYPES_BREAKOUT, SIGNAL_TYPES_MOMENTUM,
    SIGNAL_TYPES_PULLBACK, MIN_SCORE_ALERT, MIN_MARKET_CAP,
    AI_ALERT_ANALYSIS_ENABLED, FOCUS_SYMBOLS,
)
from altfins_client import (
    get_signal_feed, screener_confluence, screener_symbol,
    get_technical_analysis, get_news, get_events, get_latest_price,
)
from ai_module import analyze_symbol_setup
from signal_scorer import score_signal, parse_mcap
from geo_module import calculate_geo_score, geo_label
from database import (
    log_signal, get_last_geo_score, get_config, get_accuracy_stats,
    get_signals_needing_update, update_signal_prices, get_focus_symbols,
)
from formatters import (
    format_signal_alert, format_daily_brief, format_geo_alert,
)
from telegram_bot import notify

logger = logging.getLogger(__name__)

# In-memory dedup cache (signal_key:symbol:date → True)
_dedup_cache = {}
_last_geo_score = 0


def _dedup_key(symbol, signal_key):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{symbol}:{signal_key}:{today}"


async def _get_current_geo():
    """Get or refresh geopolitical score."""
    global _last_geo_score
    # Get BTC 24h change for context
    btc = await get_latest_price("BTC")
    btc_change = 0.0
    if btc:
        try:
            o = float(btc.get("open", 0))
            c = float(btc.get("close", 0))
            if o > 0:
                btc_change = ((c - o) / o) * 100
        except (ValueError, TypeError):
            pass

    new_score, headlines = await calculate_geo_score(btc_change)

    # Check for significant geo shift
    old_score = _last_geo_score
    if abs(new_score - old_score) >= 2:
        msg = format_geo_alert(old_score, new_score, headlines)
        await notify(msg, score=10)  # Geo shifts are always urgent
    _last_geo_score = new_score
    return new_score


async def process_signals(signals, geo_score):
    """Score and alert on a batch of signals."""
    alerted = 0
    paused = await get_config("paused")
    if paused == "true":
        return 0
    focus_symbols = set(await get_focus_symbols() or FOCUS_SYMBOLS)

    for signal in signals:
        symbol = signal.get("symbol", "?")
        signal_key = signal.get("signalKey", "").replace(".TXT", "")

        if focus_symbols and symbol.upper() not in focus_symbols:
            continue

        # Dedup
        dk = _dedup_key(symbol, signal_key)
        if dk in _dedup_cache:
            continue

        # Market cap hard filter
        mcap = parse_mcap(signal.get("marketCap", 0))
        if mcap < MIN_MARKET_CAP:
            continue

        # Get screener data for this coin (enrichment)
        screener = await screener_symbol(symbol)
        screener_data = None
        if screener:
            screener_data = screener.get("additionalData", screener)

        # Score
        ta_score, adj_score, details = await score_signal(signal, screener_data, geo_score)

        # Log to DB regardless
        price = signal.get("lastPrice", 0)
        try:
            price = float(str(price).replace(",", ""))
        except (ValueError, TypeError):
            price = 0

        logged = await log_signal(
            symbol=symbol,
            signal_key=signal.get("signalKey", ""),
            signal_name=signal.get("signalName", ""),
            direction=signal.get("direction", ""),
            score=ta_score,
            geo_score=geo_score,
            adjusted_score=adj_score,
            price_at_signal=price,
            market_cap=mcap,
            screener_data=screener_data,
            alerted=(adj_score >= MIN_SCORE_ALERT),
        )

        # Mark dedup
        _dedup_cache[dk] = True

        # Alert if meets threshold
        if adj_score >= MIN_SCORE_ALERT and logged:
            ai_analysis = None
            if AI_ALERT_ANALYSIS_ENABLED:
                ai_analysis = await analyze_symbol_setup(
                    symbol=symbol,
                    latest_signal=signal,
                    screener_data=screener_data,
                    geo_score=geo_score,
                )
            msg = format_signal_alert(
                signal,
                details,
                screener_data=screener_data,
                ai_analysis=ai_analysis,
            )
            await notify(msg, score=adj_score)
            alerted += 1
            logger.info(f"ALERTED: {symbol} — {signal_key} — Score {adj_score}")
        else:
            logger.debug(f"Logged (no alert): {symbol} — {signal_key} — Score {adj_score}")

    return alerted


# ──────────────────────────────────────────────────────────────
# Scheduled scan functions (called by APScheduler)
# ──────────────────────────────────────────────────────────────

async def scan_breakouts():
    """Poll breakout signals — highest priority, most time-sensitive."""
    logger.info("Scanning breakout signals...")
    geo = await get_last_geo_score()
    signals = await get_signal_feed(SIGNAL_TYPES_BREAKOUT, direction="BULLISH", hours_back=1)
    count = await process_signals(signals, geo)
    logger.info(f"Breakout scan: {len(signals)} signals, {count} alerted.")


async def scan_momentum():
    """Poll momentum signals."""
    logger.info("Scanning momentum signals...")
    geo = await get_last_geo_score()
    signals = await get_signal_feed(SIGNAL_TYPES_MOMENTUM, direction="BULLISH", hours_back=2)
    count = await process_signals(signals, geo)
    logger.info(f"Momentum scan: {len(signals)} signals, {count} alerted.")


async def scan_pullbacks():
    """Poll pullback signals."""
    logger.info("Scanning pullback signals...")
    geo = await get_last_geo_score()
    signals = await get_signal_feed(SIGNAL_TYPES_PULLBACK, direction="BULLISH", hours_back=4)
    count = await process_signals(signals, geo)
    logger.info(f"Pullback scan: {len(signals)} signals, {count} alerted.")


async def scan_geo():
    """Update geopolitical sentiment score."""
    logger.info("Updating geo score...")
    await _get_current_geo()


async def run_full_scan():
    """Force a complete scan of all signal types. Called by /scan command."""
    geo = await _get_current_geo()
    total = 0

    signals = await get_signal_feed(ALL_SIGNAL_TYPES, direction="BULLISH", hours_back=6)
    total += await process_signals(signals, geo)

    # Also check screener confluence
    confluence = await screener_confluence()
    logger.info(f"Screener confluence found {len(confluence)} coins matching.")

    return total


async def update_accuracy():
    """Background job to check prices for past signals and update accuracy."""
    logger.info("Updating signal accuracy tracking...")
    signals = await get_signals_needing_update()
    for s in signals:
        symbol = s["symbol"]
        created = datetime.fromisoformat(s["created_at"])
        age_hours = (datetime.now(timezone.utc) - created.replace(tzinfo=timezone.utc)).total_seconds() / 3600

        price_data = await get_latest_price(symbol)
        if not price_data:
            continue
        try:
            current = float(price_data.get("close", 0))
        except (ValueError, TypeError):
            continue

        entry = s["price_at_signal"]
        if not entry or entry == 0:
            continue

        change_pct = ((current - entry) / entry) * 100

        updates = {}
        if age_hours >= 24 and s.get("price_24h") is None:
            updates["price_24h"] = current
        if age_hours >= 72 and s.get("price_72h") is None:
            updates["price_72h"] = current
        if age_hours >= 168 and s.get("price_7d") is None:
            updates["price_7d"] = current

        # Check if TP1 hit (8% up) or Stop hit (5% down)
        if change_pct >= 8:
            updates["hit_tp1"] = 1
        elif change_pct <= -5:
            updates["hit_stop"] = 1

        if updates:
            await update_signal_prices(s["id"], **updates)

    logger.info(f"Updated accuracy for {len(signals)} signals.")


async def generate_daily_brief():
    """Generate and send the morning daily brief."""
    geo = await get_last_geo_score()
    _, headlines = await calculate_geo_score()
    focus_symbols = set(await get_focus_symbols() or FOCUS_SYMBOLS)

    # Get recent high-score signals
    signals = await get_signal_feed(ALL_SIGNAL_TYPES, direction="BULLISH", hours_back=24, size=20)
    if focus_symbols:
        signals = [s for s in signals if s.get("symbol", "").upper() in focus_symbols]
    # Filter to only ones that would have been alerted
    top_signals = [s for s in signals if parse_mcap(s.get("marketCap", 0)) >= MIN_MARKET_CAP][:5]

    events = await get_events(significant_only=True)

    msg = format_daily_brief(top_signals, geo, headlines, events)
    await notify(msg, score=0)
    return msg


async def cleanup_dedup_cache():
    """Clear old entries from dedup cache daily."""
    global _dedup_cache
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _dedup_cache = {k: v for k, v in _dedup_cache.items() if today in k}
    logger.info(f"Dedup cache cleaned. {len(_dedup_cache)} entries remaining.")

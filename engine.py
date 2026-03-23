"""Core engine for polling ALTfins, scoring signals, and sending alerts."""

import asyncio
import logging
from datetime import datetime, timezone

from config import (
    ALL_SIGNAL_TYPES,
    SIGNAL_TYPES_BREAKOUT,
    SIGNAL_TYPES_MOMENTUM,
    SIGNAL_TYPES_PULLBACK,
    MIN_SCORE_ALERT,
    MIN_MARKET_CAP,
    AI_ALERT_ANALYSIS_ENABLED,
    FOCUS_SYMBOLS,
)
from altfins_client import get_signal_feed, screener_symbol, get_latest_price
from ai_module import analyze_symbol_setup
from signal_scorer import score_signal, parse_mcap
from database import (
    log_signal,
    has_recent_signal,
    get_config,
    get_signals_needing_update,
    update_signal_prices,
    get_focus_symbols,
    get_signal_key_performance,
    get_symbol_performance,
)
from formatters import format_signal_alert, format_daily_brief
from market_context import get_market_context, signal_passes_context_gate
from news_client import get_market_news, summarize_headlines
from telegram_bot import notify

logger = logging.getLogger(__name__)

# In-memory dedup cache (signal_key:symbol:date → True)
_dedup_cache = {}


def _dedup_key(symbol, signal_key):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{symbol}:{signal_key}:{today}"


async def _build_scan_context():
    """Collect the market and performance context used across a scan batch."""
    market_context, signal_performance, symbol_performance = await asyncio.gather(
        get_market_context(),
        get_signal_key_performance(days=60),
        get_symbol_performance(days=45),
    )
    return market_context, signal_performance, symbol_performance


async def process_signals(signals, market_context, signal_performance, symbol_performance):
    """Score and alert on a batch of signals."""
    alerted = 0
    paused = await get_config("paused")
    if paused == "true":
        return 0

    focus_symbols = set(await get_focus_symbols() or FOCUS_SYMBOLS)
    effective_threshold = MIN_SCORE_ALERT + (market_context or {}).get("alert_threshold_delta", 0)

    for signal in signals:
        symbol = signal.get("symbol", "?").upper()
        signal_key = signal.get("signalKey", "").replace(".TXT", "")

        if focus_symbols and symbol not in focus_symbols:
            continue

        dk = _dedup_key(symbol, signal_key)
        if dk in _dedup_cache:
            continue
        if await has_recent_signal(symbol, signal.get("signalKey", ""), hours=24):
            _dedup_cache[dk] = True
            continue

        mcap = parse_mcap(signal.get("marketCap", 0))
        if mcap < MIN_MARKET_CAP:
            continue

        screener_data = await screener_symbol(symbol)
        base_score, final_score, details = await score_signal(
            signal,
            screener_data=screener_data,
            signal_performance=signal_performance,
            symbol_performance=symbol_performance,
        )
        gate_passed, gate_reasons = signal_passes_context_gate(screener_data, market_context)
        details["context_gate_passed"] = gate_passed
        details["context_gate_reasons"] = gate_reasons

        price = signal.get("lastPrice", 0)
        try:
            price = float(str(price).replace(",", ""))
        except (ValueError, TypeError):
            price = 0

        should_alert = final_score >= effective_threshold and gate_passed
        logged = await log_signal(
            symbol=symbol,
            signal_key=signal.get("signalKey", ""),
            signal_name=signal.get("signalName", ""),
            direction=signal.get("direction", ""),
            score=base_score,
            adjusted_score=final_score,
            price_at_signal=price,
            market_cap=mcap,
            screener_data=screener_data,
            alerted=should_alert,
        )

        _dedup_cache[dk] = True
        if not logged:
            continue

        if should_alert:
            ai_analysis = None
            if AI_ALERT_ANALYSIS_ENABLED:
                ai_analysis = await analyze_symbol_setup(
                    symbol=symbol,
                    latest_signal=signal,
                    screener_data=screener_data,
                    market_context=market_context,
                )
            msg = format_signal_alert(
                signal,
                details,
                screener_data=screener_data,
                ai_analysis=ai_analysis,
                market_context=market_context,
            )
            await notify(msg, score=final_score)
            alerted += 1
            logger.info(
                "ALERTED: %s — %s — Score %s — Context %s",
                symbol,
                signal_key,
                final_score,
                (market_context or {}).get("label", "Neutral"),
            )
        else:
            logger.debug(
                "Logged (no alert): %s — %s — Score %s — Gate %s",
                symbol,
                signal_key,
                final_score,
                "pass" if gate_passed else ", ".join(gate_reasons),
            )

    return alerted


async def _scan_signal_group(signal_types, hours_back):
    market_context, signal_performance, symbol_performance = await _build_scan_context()
    signals = await get_signal_feed(signal_types, direction="BULLISH", hours_back=hours_back)
    alerted = await process_signals(signals, market_context, signal_performance, symbol_performance)
    return signals, alerted, market_context


async def scan_breakouts():
    """Poll breakout signals — highest priority, most time-sensitive."""
    logger.info("Scanning breakout signals...")
    signals, alerted, market_context = await _scan_signal_group(SIGNAL_TYPES_BREAKOUT, hours_back=1)
    logger.info(
        "Breakout scan: %s signals, %s alerted. Context: %s",
        len(signals),
        alerted,
        market_context.get("label", "Neutral"),
    )


async def scan_momentum():
    """Poll momentum signals."""
    logger.info("Scanning momentum signals...")
    signals, alerted, market_context = await _scan_signal_group(SIGNAL_TYPES_MOMENTUM, hours_back=2)
    logger.info(
        "Momentum scan: %s signals, %s alerted. Context: %s",
        len(signals),
        alerted,
        market_context.get("label", "Neutral"),
    )


async def scan_pullbacks():
    """Poll pullback signals."""
    logger.info("Scanning pullback signals...")
    signals, alerted, market_context = await _scan_signal_group(SIGNAL_TYPES_PULLBACK, hours_back=4)
    logger.info(
        "Pullback scan: %s signals, %s alerted. Context: %s",
        len(signals),
        alerted,
        market_context.get("label", "Neutral"),
    )


async def run_full_scan():
    """Force a complete scan of all signal types. Called by /scan command."""
    market_context, signal_performance, symbol_performance = await _build_scan_context()
    signals = await get_signal_feed(ALL_SIGNAL_TYPES, direction="BULLISH", hours_back=6)
    total = await process_signals(signals, market_context, signal_performance, symbol_performance)
    logger.info(
        "Full scan: %s signals, %s alerted. Context: %s",
        len(signals),
        total,
        market_context.get("label", "Neutral"),
    )
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

        if change_pct >= 8:
            updates["hit_tp1"] = 1
        elif change_pct <= -5:
            updates["hit_stop"] = 1

        if updates:
            await update_signal_prices(s["id"], **updates)

    logger.info("Updated accuracy for %s signals.", len(signals))


async def _rank_daily_brief_signals(signals, market_context, signal_performance, symbol_performance):
    """Score recent signals for the morning brief and keep the best setup per symbol."""
    unique_symbols = []
    for signal in signals:
        symbol = signal.get("symbol", "").upper()
        if symbol and symbol not in unique_symbols:
            unique_symbols.append(symbol)

    screener_results = await asyncio.gather(*(screener_symbol(symbol) for symbol in unique_symbols))
    screener_map = dict(zip(unique_symbols, screener_results))

    ranked_by_symbol = {}
    for signal in signals:
        symbol = signal.get("symbol", "").upper()
        screener_data = screener_map.get(symbol)
        _, final_score, _ = await score_signal(
            signal,
            screener_data=screener_data,
            signal_performance=signal_performance,
            symbol_performance=symbol_performance,
        )
        gate_passed, _ = signal_passes_context_gate(screener_data, market_context)
        if not gate_passed:
            continue
        candidate = dict(signal)
        candidate["adjusted_score"] = final_score
        best = ranked_by_symbol.get(symbol)
        if not best or candidate["adjusted_score"] > best["adjusted_score"]:
            ranked_by_symbol[symbol] = candidate

    ranked = sorted(
        ranked_by_symbol.values(),
        key=lambda item: (item.get("adjusted_score", 0), parse_mcap(item.get("marketCap", 0))),
        reverse=True,
    )
    return ranked[:5]


async def generate_daily_brief():
    """Generate and send the morning daily brief."""
    focus_symbols = set(await get_focus_symbols() or FOCUS_SYMBOLS)
    market_context, signal_performance, symbol_performance = await _build_scan_context()

    signals, news = await asyncio.gather(
        get_signal_feed(ALL_SIGNAL_TYPES, direction="BULLISH", hours_back=24, size=30),
        get_market_news(limit=5),
    )
    if focus_symbols:
        signals = [signal for signal in signals if signal.get("symbol", "").upper() in focus_symbols]

    top_signals = await _rank_daily_brief_signals(
        [signal for signal in signals if parse_mcap(signal.get("marketCap", 0)) >= MIN_MARKET_CAP],
        market_context,
        signal_performance,
        symbol_performance,
    )

    msg = format_daily_brief(top_signals, market_context, summarize_headlines(news, limit=3))
    await notify(msg, score=0)
    return msg


async def cleanup_dedup_cache():
    """Clear old entries from dedup cache daily."""
    global _dedup_cache
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _dedup_cache = {key: value for key, value in _dedup_cache.items() if today in key}
    logger.info("Dedup cache cleaned. %s entries remaining.", len(_dedup_cache))

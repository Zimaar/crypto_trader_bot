"""Core engine for ranked premium alerts, market digests, and lifecycle updates."""

import asyncio
from datetime import datetime, timezone
import logging

from config import (
    ALL_SIGNAL_TYPES,
    SIGNAL_TYPES_BREAKOUT,
    SIGNAL_TYPES_MOMENTUM,
    SIGNAL_TYPES_PULLBACK,
    MIN_MARKET_CAP,
    AI_ALERT_ANALYSIS_ENABLED,
    FOCUS_SYMBOLS,
    PREMIUM_SYMBOL_COOLDOWN_HOURS,
    MAX_PREMIUM_ALERTS_PER_SCAN,
    MAX_PREMIUM_ALERTS_PER_DAY,
    MARKET_DIGEST_INTERVAL_HOURS,
    MANAGED_SETUP_EXPIRY_HOURS,
    DIGEST_SYMBOL_SUPPRESSION_HOURS,
    DIGEST_SCORE_IMPROVEMENT_THRESHOLD,
    MAX_DIGEST_CANDIDATES,
)
from altfins_client import get_signal_feed, screener_symbol, get_latest_price, get_latest_prices
from ai_module import analyze_symbol_setup
from database import (
    create_managed_setup,
    get_active_managed_setups,
    get_config,
    get_focus_symbols,
    get_recent_alerted_symbols,
    get_recent_digest_scores,
    get_signal_count_today,
    get_signal_key_performance,
    get_signals_needing_update,
    get_symbol_performance,
    has_recent_signal,
    log_digest_candidates,
    log_signal,
    update_managed_setup,
    update_signal_prices,
)
from formatters import (
    format_daily_brief,
    format_market_digest,
    format_setup_lifecycle_update,
    format_signal_alert,
)
from market_context import get_market_context, signal_passes_context_gate
from news_client import get_market_news, summarize_headlines
from signal_scorer import parse_mcap, parse_trend_score, score_signal
from telegram_bot import notify
from trade_levels import build_trade_plan

logger = logging.getLogger(__name__)

_dedup_cache = {}


def _dedup_key(symbol, signal_key):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{symbol}:{signal_key}:{today}"


def _setup_type(signal_key):
    clean_key = signal_key.replace(".TXT", "")
    if "PULLBACK" in clean_key:
        return "pullback"
    if any(token in clean_key for token in ["MOMENTUM", "MACD", "EMA", "INFLECTION"]):
        return "momentum"
    return "breakout"


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _premium_sort_key(candidate):
    setup_priority = 1 if candidate["setup_type"] == "breakout" else 0
    return (
        candidate["final_score"],
        setup_priority,
        candidate["volume_relative"],
        candidate["market_cap"],
    )


def _best_candidates_by_symbol(candidates):
    best = {}
    for candidate in candidates:
        current = best.get(candidate["symbol"])
        if not current or _premium_sort_key(candidate) > _premium_sort_key(current):
            best[candidate["symbol"]] = candidate
    return list(best.values())


async def _build_scan_context():
    market_context, signal_performance, symbol_performance = await asyncio.gather(
        get_market_context(),
        get_signal_key_performance(days=60),
        get_symbol_performance(days=45),
    )
    return market_context, signal_performance, symbol_performance


def _is_premium_eligible(candidate, has_focus_symbols):
    if not candidate["gate_passed"]:
        return False, None

    score = candidate["final_score"]
    medium_score = candidate["medium_score"]
    volume_relative = candidate["volume_relative"]
    setup_type = candidate["setup_type"]

    if setup_type == "pullback":
        return False, None

    if has_focus_symbols:
        if not candidate["is_focus"]:
            return False, None
        if setup_type == "breakout" and score >= 7 and medium_score >= 6 and volume_relative >= 1.2:
            return True, "focus watchlist"
        if setup_type == "momentum" and score >= 8 and medium_score >= 7 and volume_relative >= 1.5:
            return True, "focus watchlist"
        return False, None

    if setup_type == "breakout" and score >= 8:
        return True, "market-wide elite"
    if setup_type == "momentum" and score >= 9:
        return True, "market-wide elite"
    return False, None


def _is_market_digest_eligible(candidate):
    if not candidate["gate_passed"]:
        return False
    if candidate["setup_type"] == "breakout" and candidate["final_score"] >= 8:
        return True
    if (
        candidate["setup_type"] == "momentum"
        and candidate["final_score"] >= 9
        and candidate["medium_score"] >= 7
        and candidate["volume_relative"] >= 1.8
    ):
        return True
    return False


async def _prepare_candidates(
    signals,
    market_context,
    signal_performance,
    symbol_performance,
    *,
    ignore_recent_signal=False,
):
    focus_symbols = set(await get_focus_symbols() or FOCUS_SYMBOLS)
    filtered = []
    unique_symbols = []
    seen_symbols = set()

    for signal in signals:
        symbol = signal.get("symbol", "").upper()
        signal_key_raw = signal.get("signalKey", "")
        signal_key = signal_key_raw.replace(".TXT", "")
        if not symbol or not signal_key:
            continue

        dedup_key = _dedup_key(symbol, signal_key)
        if not ignore_recent_signal and dedup_key in _dedup_cache:
            continue
        if not ignore_recent_signal and await has_recent_signal(symbol, signal_key_raw, hours=24):
            _dedup_cache[dedup_key] = True
            continue

        mcap = parse_mcap(signal.get("marketCap", 0))
        if mcap < MIN_MARKET_CAP:
            continue

        if symbol not in seen_symbols:
            seen_symbols.add(symbol)
            unique_symbols.append(symbol)

        filtered.append({
            "signal": signal,
            "symbol": symbol,
            "signal_key_raw": signal_key_raw,
            "signal_key": signal_key,
            "setup_type": _setup_type(signal_key),
            "market_cap": mcap,
            "dedup_key": dedup_key,
        })

    screener_results = await asyncio.gather(*(screener_symbol(symbol) for symbol in unique_symbols))
    screener_map = dict(zip(unique_symbols, screener_results))

    candidates = []
    has_focus_symbols = bool(focus_symbols)

    for item in filtered:
        signal = item["signal"]
        symbol = item["symbol"]
        screener_data = screener_map.get(symbol)
        base_score, final_score, details = await score_signal(
            signal,
            screener_data=screener_data,
            signal_performance=signal_performance,
            symbol_performance=symbol_performance,
        )
        gate_passed, gate_reasons = signal_passes_context_gate(screener_data, market_context)
        medium_score = parse_trend_score(details.get("medium_trend", ""))
        volume_relative = _safe_float(details.get("volume_relative"), 1.0)
        trade_plan = build_trade_plan(signal, screener_data)

        candidate = {
            **item,
            "screener_data": screener_data,
            "base_score": base_score,
            "final_score": final_score,
            "details": details,
            "gate_passed": gate_passed,
            "gate_reasons": gate_reasons,
            "medium_score": medium_score,
            "volume_relative": volume_relative,
            "trade_plan": trade_plan,
            "is_focus": symbol in focus_symbols,
            "premium_eligible": False,
            "premium_lane_reason": None,
            "market_digest_eligible": False,
            "send_premium": False,
            "digest_bucket": None,
            "premium_block_reason": None,
            "signal_id": None,
        }

        premium_eligible, lane_reason = _is_premium_eligible(candidate, has_focus_symbols)
        candidate["premium_eligible"] = premium_eligible
        candidate["premium_lane_reason"] = lane_reason
        candidate["market_digest_eligible"] = _is_market_digest_eligible(candidate)

        details["setup_type"] = candidate["setup_type"]
        details["medium_score"] = medium_score
        details["volume_relative"] = volume_relative
        details["gate_passed"] = gate_passed
        details["gate_reasons"] = gate_reasons
        details["market_context_snapshot"] = (market_context or {}).get("snapshot")

        candidates.append(candidate)

    return candidates, focus_symbols


def _assign_digest_buckets(candidates, focus_symbols):
    digest_candidates = []
    has_focus_symbols = bool(focus_symbols)

    for candidate in candidates:
        if candidate["send_premium"]:
            continue
        if has_focus_symbols and candidate["is_focus"] and candidate["premium_eligible"]:
            candidate["digest_bucket"] = "watchlist"
        elif candidate["market_digest_eligible"]:
            candidate["digest_bucket"] = "market"
        else:
            candidate["digest_bucket"] = None

        if candidate["digest_bucket"]:
            digest_candidates.append(candidate)

    return digest_candidates


async def process_signals(signals, market_context, signal_performance, symbol_performance):
    """Score a scan batch, send premium alerts, and return digest stats."""
    paused = await get_config("paused")
    if paused == "true":
        return {"premium_sent": 0, "digest_candidates": 0}

    candidates, focus_symbols = await _prepare_candidates(
        signals,
        market_context,
        signal_performance,
        symbol_performance,
    )

    recent_alerted_symbols = await get_recent_alerted_symbols(PREMIUM_SYMBOL_COOLDOWN_HOURS)
    premium_alert_count_today = await get_signal_count_today()
    symbol_cooldown_block = set(recent_alerted_symbols)
    ranked_premium = sorted(
        [candidate for candidate in candidates if candidate["premium_eligible"]],
        key=_premium_sort_key,
        reverse=True,
    )

    premium_to_send = []
    for candidate in ranked_premium:
        if premium_alert_count_today >= MAX_PREMIUM_ALERTS_PER_DAY:
            candidate["premium_block_reason"] = "daily premium limit reached"
            continue
        if len(premium_to_send) >= MAX_PREMIUM_ALERTS_PER_SCAN:
            candidate["premium_block_reason"] = "scan premium limit reached"
            continue
        if candidate["symbol"] in symbol_cooldown_block:
            candidate["premium_block_reason"] = "symbol premium cooldown active"
            continue

        candidate["send_premium"] = True
        premium_to_send.append(candidate)
        premium_alert_count_today += 1
        symbol_cooldown_block.add(candidate["symbol"])

    digest_candidates = _assign_digest_buckets(candidates, focus_symbols)
    digest_candidates = _best_candidates_by_symbol(digest_candidates)

    for rank, candidate in enumerate(premium_to_send, start=1):
        candidate["signal_id"] = await log_signal(
            symbol=candidate["symbol"],
            signal_key=candidate["signal_key_raw"],
            signal_name=candidate["signal"].get("signalName", ""),
            direction=candidate["signal"].get("direction", ""),
            score=candidate["base_score"],
            adjusted_score=candidate["final_score"],
            price_at_signal=_safe_float(candidate["signal"].get("lastPrice")),
            market_cap=candidate["market_cap"],
            screener_data=candidate["screener_data"],
            alerted=True,
        )
        _dedup_cache[candidate["dedup_key"]] = True

        ai_analysis = None
        if AI_ALERT_ANALYSIS_ENABLED:
            ai_analysis = await analyze_symbol_setup(
                symbol=candidate["symbol"],
                latest_signal=candidate["signal"],
                screener_data=candidate["screener_data"],
                market_context=market_context,
            )

        msg = format_signal_alert(
            candidate["signal"],
            candidate["details"],
            screener_data=candidate["screener_data"],
            ai_analysis=ai_analysis,
            market_context=market_context,
            alert_lane="premium",
            lane_reason=candidate["premium_lane_reason"],
            rank=rank,
        )
        await notify(msg, score=candidate["final_score"])

        trade_plan = candidate["trade_plan"]
        if candidate["signal_id"] and trade_plan.get("breakout_price"):
            await create_managed_setup(
                signal_id=candidate["signal_id"],
                symbol=candidate["symbol"],
                lane="premium",
                setup_type=candidate["setup_type"],
                breakout_price=trade_plan.get("breakout_price"),
                stop_price=trade_plan.get("stop_price"),
                tp_price=trade_plan.get("tp_price"),
                expires_hours=MANAGED_SETUP_EXPIRY_HOURS,
            )

        logger.info(
            "PREMIUM ALERT: %s — %s — Score %s — %s",
            candidate["symbol"],
            candidate["signal_key"],
            candidate["final_score"],
            candidate["premium_lane_reason"],
        )

    return {
        "premium_sent": len(premium_to_send),
        "digest_candidates": len(digest_candidates[:MAX_DIGEST_CANDIDATES]),
    }


async def _scan_signal_group(signal_types, hours_back):
    market_context, signal_performance, symbol_performance = await _build_scan_context()
    signals = await get_signal_feed(signal_types, direction="BULLISH", hours_back=hours_back)
    results = await process_signals(signals, market_context, signal_performance, symbol_performance)
    return signals, results, market_context


async def scan_breakouts():
    logger.info("Scanning breakout signals...")
    signals, results, market_context = await _scan_signal_group(SIGNAL_TYPES_BREAKOUT, hours_back=1)
    logger.info(
        "Breakout scan: %s signals, %s premium sent, %s digest candidates. Context: %s",
        len(signals),
        results["premium_sent"],
        results["digest_candidates"],
        market_context.get("label", "Neutral"),
    )


async def scan_momentum():
    logger.info("Scanning momentum signals...")
    signals, results, market_context = await _scan_signal_group(SIGNAL_TYPES_MOMENTUM, hours_back=2)
    logger.info(
        "Momentum scan: %s signals, %s premium sent, %s digest candidates. Context: %s",
        len(signals),
        results["premium_sent"],
        results["digest_candidates"],
        market_context.get("label", "Neutral"),
    )


async def scan_pullbacks():
    logger.info("Scanning pullback signals...")
    signals, results, market_context = await _scan_signal_group(SIGNAL_TYPES_PULLBACK, hours_back=4)
    logger.info(
        "Pullback scan: %s signals, %s premium sent, %s digest candidates. Context: %s",
        len(signals),
        results["premium_sent"],
        results["digest_candidates"],
        market_context.get("label", "Neutral"),
    )


async def run_full_scan():
    """Force a full premium scan of all tracked signal types."""
    market_context, signal_performance, symbol_performance = await _build_scan_context()
    signals = await get_signal_feed(ALL_SIGNAL_TYPES, direction="BULLISH", hours_back=6)
    results = await process_signals(signals, market_context, signal_performance, symbol_performance)
    logger.info(
        "Full scan: %s signals, %s premium sent, %s digest candidates. Context: %s",
        len(signals),
        results["premium_sent"],
        results["digest_candidates"],
        market_context.get("label", "Neutral"),
    )
    return results


def _filter_digest_candidates(candidates, recent_digest_scores):
    filtered = []
    for candidate in _best_candidates_by_symbol(candidates):
        recent_score = recent_digest_scores.get(candidate["symbol"], -999)
        if recent_score > -999 and candidate["final_score"] < recent_score + DIGEST_SCORE_IMPROVEMENT_THRESHOLD:
            continue
        filtered.append(candidate)

    filtered.sort(key=_premium_sort_key, reverse=True)
    return filtered[:MAX_DIGEST_CANDIDATES]


async def generate_market_digest(send=True):
    """Build and optionally send the market digest."""
    market_context, signal_performance, symbol_performance = await _build_scan_context()
    signals = await get_signal_feed(ALL_SIGNAL_TYPES, direction="BULLISH", hours_back=MARKET_DIGEST_INTERVAL_HOURS, size=40)
    candidates, focus_symbols = await _prepare_candidates(
        signals,
        market_context,
        signal_performance,
        symbol_performance,
        ignore_recent_signal=True,
    )

    recent_alerted_symbols = await get_recent_alerted_symbols(PREMIUM_SYMBOL_COOLDOWN_HOURS)
    daily_cap_reached = await get_signal_count_today() >= MAX_PREMIUM_ALERTS_PER_DAY

    for candidate in candidates:
        if focus_symbols and candidate["is_focus"] and candidate["premium_eligible"]:
            if daily_cap_reached or candidate["symbol"] in recent_alerted_symbols:
                candidate["digest_bucket"] = "watchlist"
        elif candidate["market_digest_eligible"]:
            candidate["digest_bucket"] = "market"
        elif not focus_symbols and candidate["premium_eligible"] and candidate["symbol"] in recent_alerted_symbols:
            candidate["digest_bucket"] = "market"

    recent_digest_scores = await get_recent_digest_scores(DIGEST_SYMBOL_SUPPRESSION_HOURS)
    watchlist_candidates = _filter_digest_candidates(
        [candidate for candidate in candidates if candidate.get("digest_bucket") == "watchlist"],
        recent_digest_scores,
    )
    market_candidates = _filter_digest_candidates(
        [candidate for candidate in candidates if candidate.get("digest_bucket") == "market"],
        recent_digest_scores,
    )

    combined = sorted(watchlist_candidates + market_candidates, key=_premium_sort_key, reverse=True)
    combined = combined[:MAX_DIGEST_CANDIDATES]
    combined_symbols = {candidate["symbol"] for candidate in combined}
    watchlist_candidates = [candidate for candidate in watchlist_candidates if candidate["symbol"] in combined_symbols]
    market_candidates = [candidate for candidate in market_candidates if candidate["symbol"] in combined_symbols]

    msg = format_market_digest(watchlist_candidates, market_candidates, market_context)
    if combined:
        await log_digest_candidates(combined)
    if send and combined:
        await notify(msg, score=0)
    return msg


async def monitor_managed_setups():
    """Check premium setups and send lifecycle follow-ups."""
    active_setups = await get_active_managed_setups()
    if not active_setups:
        return 0

    symbols = sorted({setup["symbol"] for setup in active_setups})
    price_rows = await get_latest_prices(",".join(symbols))
    latest_by_symbol = {
        str(row.get("symbol", "")).upper(): row
        for row in price_rows
        if str(row.get("symbol", "")).strip()
    }

    updates_sent = 0
    now = datetime.now(timezone.utc)

    for setup in active_setups:
        symbol = setup["symbol"].upper()
        price_row = latest_by_symbol.get(symbol)
        if not price_row:
            price_row = await get_latest_price(symbol)
        if not price_row:
            continue

        latest_price = _safe_float(price_row.get("close"))
        if latest_price <= 0:
            continue

        breakout_price = _safe_float(setup.get("breakout_price"))
        stop_price = _safe_float(setup.get("stop_price"))
        tp_price = _safe_float(setup.get("tp_price"))
        expires_at_raw = setup.get("expires_at")
        expires_at = datetime.fromisoformat(expires_at_raw) if expires_at_raw else now
        status = setup["status"]

        next_status = None
        update_fields = {}

        if status == "armed":
            if breakout_price and latest_price >= breakout_price:
                next_status = "entered"
                update_fields["entry_at"] = now.isoformat()
                update_fields["notified_entry"] = 1
            elif stop_price and latest_price <= stop_price:
                next_status = "invalidated"
                update_fields["closed_at"] = now.isoformat()
                update_fields["notified_invalidation"] = 1
            elif now >= expires_at:
                next_status = "expired"
                update_fields["closed_at"] = now.isoformat()
                update_fields["notified_expired"] = 1
        elif status == "entered":
            if tp_price and latest_price >= tp_price:
                next_status = "tp_hit"
                update_fields["closed_at"] = now.isoformat()
                update_fields["notified_tp"] = 1
            elif stop_price and latest_price <= stop_price:
                next_status = "stopped"
                update_fields["closed_at"] = now.isoformat()
                update_fields["notified_stop"] = 1

        if not next_status:
            continue

        update_fields["status"] = next_status
        await update_managed_setup(setup["id"], **update_fields)
        msg = format_setup_lifecycle_update(setup, next_status, latest_price)
        await notify(msg, score=0)
        updates_sent += 1

    logger.info("Lifecycle monitor sent %s updates.", updates_sent)
    return updates_sent


async def update_accuracy():
    """Background job to check prices for past premium alerts and update accuracy."""
    logger.info("Updating signal accuracy tracking...")
    signals = await get_signals_needing_update()
    for row in signals:
        symbol = row["symbol"]
        created = datetime.fromisoformat(row["created_at"])
        age_hours = (datetime.now(timezone.utc) - created.replace(tzinfo=timezone.utc)).total_seconds() / 3600

        price_data = await get_latest_price(symbol)
        if not price_data:
            continue

        current = _safe_float(price_data.get("close"))
        entry = _safe_float(row["price_at_signal"])
        if current <= 0 or entry <= 0:
            continue

        change_pct = ((current - entry) / entry) * 100
        updates = {}
        if age_hours >= 24 and row.get("price_24h") is None:
            updates["price_24h"] = current
        if age_hours >= 72 and row.get("price_72h") is None:
            updates["price_72h"] = current
        if age_hours >= 168 and row.get("price_7d") is None:
            updates["price_7d"] = current
        if change_pct >= 8:
            updates["hit_tp1"] = 1
        elif change_pct <= -5:
            updates["hit_stop"] = 1

        if updates:
            await update_signal_prices(row["id"], **updates)

    logger.info("Updated accuracy for %s signals.", len(signals))


async def generate_daily_brief():
    """Generate and send the morning daily brief."""
    market_context, signal_performance, symbol_performance = await _build_scan_context()
    signals, news = await asyncio.gather(
        get_signal_feed(ALL_SIGNAL_TYPES, direction="BULLISH", hours_back=24, size=30),
        get_market_news(limit=5),
    )
    candidates, _ = await _prepare_candidates(
        signals,
        market_context,
        signal_performance,
        symbol_performance,
        ignore_recent_signal=True,
    )

    top_candidates = _best_candidates_by_symbol(
        [candidate for candidate in candidates if candidate["setup_type"] != "pullback"]
    )
    top_candidates.sort(key=_premium_sort_key, reverse=True)
    top_signals = []
    for candidate in top_candidates[:5]:
        signal = dict(candidate["signal"])
        signal["adjusted_score"] = candidate["final_score"]
        top_signals.append(signal)

    msg = format_daily_brief(top_signals, market_context, summarize_headlines(news, limit=3))
    await notify(msg, score=0)
    return msg


async def cleanup_dedup_cache():
    """Clear old entries from the in-memory dedup cache daily."""
    global _dedup_cache
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _dedup_cache = {key: value for key, value in _dedup_cache.items() if today in key}
    logger.info("Dedup cache cleaned. %s entries remaining.", len(_dedup_cache))

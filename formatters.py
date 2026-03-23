"""Telegram/WhatsApp message formatting for all alert types."""

from datetime import datetime, timezone
from trade_levels import build_trade_plan, format_percent, format_price


def _append_indicator_lines(lines, screener_data):
    """Append a compact indicator block to a message."""
    if not screener_data:
        return

    add = screener_data.get("additionalData", screener_data)

    def format_value(key, value):
        if isinstance(value, dict):
            levels = [str(v) for v in value.values() if str(v).strip() not in {"", "-"}]
            return ", ".join(levels[:3]) if levels else "-"
        if key == "RSI14":
            try:
                return f"{float(value):.1f}"
            except (TypeError, ValueError):
                return str(value)
        if key == "VOLUME_RELATIVE":
            try:
                return f"{float(value):.2f}x"
            except (TypeError, ValueError):
                return str(value)
        return str(value)

    indicators = [
        ("RSI14", "RSI(14)"),
        ("MACD", "MACD"),
        ("SHORT_TERM_TREND", "Short trend"),
        ("MEDIUM_TERM_TREND", "Medium trend"),
        ("LONG_TERM_TREND", "Long trend"),
        ("VOLUME_RELATIVE", "Relative volume"),
        ("PRICE_CHANGE_1D", "1D change"),
        ("PRICE_CHANGE_1W", "1W change"),
        ("SUPPORT", "Support"),
        ("RESISTANCE", "Resistance"),
    ]

    lines.append("📈 Current Indicators:")
    added = 0
    for key, label in indicators:
        value = add.get(key)
        if value not in (None, ""):
            lines.append(f"├ {label}: {format_value(key, value)}")
            added += 1
    if added:
        lines[-1] = lines[-1].replace("├", "└", 1)
    else:
        lines.pop()


def _append_trade_plan(lines, signal, screener_data):
    """Append mandatory trade-plan fields."""
    trade_plan = build_trade_plan(signal, screener_data)
    profit = format_percent(trade_plan["profit_pct"])
    loss = format_percent(-trade_plan["loss_pct"]) if trade_plan["loss_pct"] is not None else "N/A"

    lines.append("🎯 Trade Plan:")
    lines.append(f"├ Breakout Price: {format_price(trade_plan['breakout_price'])}")
    lines.append(f"├ TP: {format_price(trade_plan['tp_price'])}")
    lines.append(f"├ Profit: {profit}")
    lines.append(f"├ Loss: {loss}")
    if trade_plan["rr_ratio"] is not None:
        lines.append(f"└ Reward/Risk: {trade_plan['rr_ratio']:.2f}R")
    else:
        lines[-1] = lines[-1].replace("├", "└", 1)


def _append_history_edge(lines, score_details):
    """Append historical performance context when enough data exists."""
    history_lines = []

    signal_resolved = score_details.get("signal_history_resolved", 0)
    signal_win_rate = score_details.get("signal_history_win_rate")
    if signal_resolved and signal_win_rate is not None:
        signal_adj = score_details.get("signal_history_adjustment", 0)
        history_lines.append(
            f"├ Signal type edge: {signal_win_rate:.0%} ({signal_resolved} resolved, {signal_adj:+d})"
        )

    symbol_resolved = score_details.get("symbol_history_resolved", 0)
    symbol_win_rate = score_details.get("symbol_history_win_rate")
    if symbol_resolved and symbol_win_rate is not None:
        symbol_adj = score_details.get("symbol_history_adjustment", 0)
        history_lines.append(
            f"├ Symbol edge: {symbol_win_rate:.0%} ({symbol_resolved} resolved, {symbol_adj:+d})"
        )

    if not history_lines:
        return

    history_lines[-1] = history_lines[-1].replace("├", "└", 1)
    lines.append("")
    lines.append("🧠 Historical Edge:")
    lines.extend(history_lines)


def _append_market_context(lines, market_context):
    """Append BTC market context without turning it into a score."""
    if not market_context:
        return

    lines.append("")
    lines.append(f"🧭 Market Context: {market_context.get('label', 'Neutral')}")
    snapshot = market_context.get("snapshot")
    summary = market_context.get("summary")
    if snapshot:
        lines.append(f"├ {snapshot}")
    if summary:
        prefix = "└" if snapshot else "└"
        lines.append(f"{prefix} {summary}")


def format_signal_alert(signal, score_details, screener_data=None, ai_analysis=None, market_context=None):
    """Format a scored signal into a Telegram alert message."""
    symbol = signal.get("symbol", "?")
    name = signal.get("name") or signal.get("symbolName", symbol)
    price = signal.get("lastPrice", "?")
    signal_name = signal.get("signalName", signal.get("signalKey", "?"))
    direction = signal.get("direction", "?")
    mcap = score_details.get("market_cap", 0)
    ta_score = score_details.get("ta_score", 0)
    adj_score = score_details.get("adjusted_score", 0)
    history_adj = score_details.get("history_adjustment", 0)

    # Determine alert emoji based on signal type
    signal_key = score_details.get("signal_type", "")
    if "BREAKOUT" in signal_key or "PATTERN" in signal_key:
        emoji = "🔺"
        label = "BREAKOUT"
    elif "PULLBACK" in signal_key:
        emoji = "📉➡📈"
        label = "PULLBACK BUY"
    elif "MOMENTUM" in signal_key or "MACD" in signal_key:
        emoji = "⚡"
        label = "MOMENTUM"
    else:
        emoji = "📊"
        label = "SIGNAL"

    # Urgency indicator
    if adj_score >= 9:
        urgency = "🔥🔥 MAX CONVICTION"
    elif adj_score >= 8:
        urgency = "🔥 HIGH CONVICTION"
    else:
        urgency = ""

    lines = [f"{emoji} {label} — {symbol} ({name}) — ${price}"]
    if urgency:
        lines.append(f"{urgency}")
    lines.append("")
    lines.append(f"📊 Signal: {signal_name}")

    # TA Details
    ta_lines = []
    rsi = score_details.get("rsi")
    if rsi is not None:
        rsi_note = ""
        if rsi < 30:
            rsi_note = " — oversold"
        elif rsi > 70:
            rsi_note = " — overbought ⚠️"
        elif 40 <= rsi <= 65:
            rsi_note = " — room to run"
        ta_lines.append(f"├ RSI(14): {rsi:.1f}{rsi_note}")

    med = score_details.get("medium_trend", "")
    short = score_details.get("short_trend", "")
    if med:
        ta_lines.append(f"├ Medium trend: {med}")
    if short:
        ta_lines.append(f"├ Short trend: {short}")

    vol = score_details.get("volume_relative")
    if vol and vol > 1.0:
        ta_lines.append(f"├ Volume: {vol:.2f}x average")

    if mcap:
        if mcap > 1_000_000_000:
            mcap_str = f"${mcap / 1e9:.1f}B"
        else:
            mcap_str = f"${mcap / 1e6:.0f}M"
        ta_lines.append(f"├ Market Cap: {mcap_str}")

    confluence = score_details.get("confluence_count", 0)
    if confluence >= 2:
        ta_lines.append(f"└ ⚡ Confluence: {confluence} signals in 24h")
    elif ta_lines:
        ta_lines[-1] = ta_lines[-1].replace("├", "└", 1)

    if ta_lines:
        lines.extend(ta_lines)

    _append_history_edge(lines, score_details)

    lines.append("")
    _append_trade_plan(lines, signal, screener_data)
    _append_market_context(lines, market_context)

    # Score
    lines.append("")
    lines.append(f"📈 Score: {adj_score}/10 (TA: {ta_score}, History: {history_adj:+d})")

    if ai_analysis:
        lines.append("")
        lines.append("🤖 AI View:")
        lines.append(ai_analysis)

    # Timestamp
    now = datetime.now(timezone.utc).strftime("%b %d, %Y %I:%M %p UTC")
    lines.append(f"⏰ {now}")

    return "\n".join(lines)


def format_ta_report(ta_data, screener_data=None, latest_signal=None, ai_analysis=None, market_context=None):
    """Format a technical analysis report for /ta command."""
    if not ta_data and not screener_data and not latest_signal:
        return "No current market snapshot found for this symbol."

    symbol = "?"
    name = symbol

    if latest_signal:
        symbol = latest_signal.get("symbol", symbol)
        name = latest_signal.get("name") or latest_signal.get("symbolName", symbol)
    if screener_data and isinstance(screener_data, dict):
        symbol = screener_data.get("symbol", symbol)
        name = screener_data.get("name", name)

    lines = []

    if ta_data:
        ta = ta_data if isinstance(ta_data, dict) else ta_data[0]
        symbol = ta.get("symbol", symbol)
        name = ta.get("friendlyName", name)
        outlook = ta.get("nearTermOutlook", "?")
        pattern = ta.get("patternType", "?")
        stage = ta.get("patternStage", "?")

        # Parse the HTML description to extract key info
        desc_raw = ta.get("description", "")
        # Strip HTML tags simply
        import re
        desc_clean = re.sub(r"<[^>]+>", " ", desc_raw)
        desc_clean = re.sub(r"\s+", " ", desc_clean).strip()
        # Truncate
        if len(desc_clean) > 600:
            desc_clean = desc_clean[:600] + "..."

        lines.extend([
            f"📊 TA REPORT — {symbol} ({name})",
            "",
            f"📐 Pattern: {pattern} ({stage})",
            f"🔮 Outlook: {outlook}",
            "",
            "📝 Analyst Notes:",
            desc_clean or "No analyst notes available.",
        ])
    else:
        lines.extend([
            f"📊 MARKET SNAPSHOT — {symbol} ({name})",
            "",
        ])
        if latest_signal:
            signal_name = latest_signal.get("signalName", latest_signal.get("signalKey", "?"))
            direction = latest_signal.get("direction", "?")
            price = latest_signal.get("lastPrice", "?")
            timestamp = str(latest_signal.get("timestamp", ""))[:16].replace("T", " ")
            lines.append(f"📡 Latest Signal: {signal_name} ({direction})")
            lines.append(f"💵 Last Price: ${price}")
            if timestamp:
                lines.append(f"🕒 Signal Time: {timestamp} UTC")
        elif screener_data:
            price = screener_data.get("lastPrice")
            if price not in (None, ""):
                lines.append(f"💵 Last Price: ${price}")

    if screener_data:
        lines.append("")
        _append_indicator_lines(lines, screener_data)

    lines.append("")
    _append_trade_plan(lines, latest_signal, screener_data)
    _append_market_context(lines, market_context)

    if ai_analysis:
        lines.append("")
        lines.append("🤖 AI View:")
        lines.append(ai_analysis)

    return "\n".join(lines)


def format_daily_brief(signals_today, market_context, headlines=None):
    """Format the morning daily brief."""
    now = datetime.now(timezone.utc).strftime("%b %d, %Y")
    lines = [
        f"☀️ DAILY BRIEF — {now}",
        "",
        f"🧭 Market Context: {(market_context or {}).get('label', 'Neutral')}",
    ]
    snapshot = (market_context or {}).get("snapshot")
    summary = (market_context or {}).get("summary")
    if snapshot:
        lines.append(f"├ {snapshot}")
    if summary:
        prefix = "└" if snapshot else "└"
        lines.append(f"{prefix} {summary}")
    lines.append("")

    if headlines:
        lines.append("📰 Market Headlines:")
        for h in headlines[:3]:
            lines.append(f"├ {h[:80]}")
        lines[-1] = lines[-1].replace("├", "└", 1)
        lines.append("")

    if signals_today:
        lines.append(f"🔥 Top Signals (last 24h): {len(signals_today)}")
        for i, s in enumerate(signals_today[:5]):
            sym = s.get("symbol", "?")
            sname = s.get("signalName", s.get("signal_name", "?"))
            score = s.get("adjusted_score", s.get("score", "?"))
            lines.append(f"{i+1}. {sym} — {sname} | Score: {score}")
    else:
        lines.append("📭 No high-quality signals in the last 24h.")

    return "\n".join(lines)


def format_accuracy_report(stats):
    """Format signal accuracy stats."""
    if not stats:
        return "📊 No accuracy data yet. Signals need 24h+ to track."

    lines = ["📊 SIGNAL ACCURACY — Last 30 Days", ""]
    total_signals = 0
    total_resolved = 0
    total_wins = 0
    total_losses = 0

    for s in stats:
        key = s["signal_key"].replace(".TXT", "")
        total = s["total"]
        resolved = s.get("resolved", 0) or 0
        wins = s["wins"]
        losses = s["losses"]
        total_signals += total
        total_resolved += resolved
        total_wins += wins
        total_losses += losses
        pct = f"{wins/resolved*100:.0f}%" if resolved > 0 else "N/A"
        lines.append(f"├ {key}: {pct} hit TP ({wins}/{resolved} resolved, {total} total)")

    if total_resolved > 0:
        overall = total_wins / total_resolved * 100
        lines.append("")
        lines.append(f"📈 Overall: {overall:.0f}% ({total_wins}/{total_resolved} resolved)")
        lines.append(f"📦 Total alerts tracked: {total_signals}")
        lines.append(f"🛑 Stopped out: {total_losses}")

    return "\n".join(lines)

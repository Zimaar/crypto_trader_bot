"""Telegram/WhatsApp message formatting for all alert types."""

from datetime import datetime, timezone
from geo_module import geo_label
from signal_scorer import parse_trend_score
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


def format_signal_alert(signal, score_details, screener_data=None, ai_analysis=None):
    """Format a scored signal into a Telegram alert message."""
    symbol = signal.get("symbol", "?")
    name = signal.get("name") or signal.get("symbolName", symbol)
    price = signal.get("lastPrice", "?")
    signal_name = signal.get("signalName", signal.get("signalKey", "?"))
    direction = signal.get("direction", "?")
    mcap = score_details.get("market_cap", 0)
    ta_score = score_details.get("ta_score", 0)
    adj_score = score_details.get("adjusted_score", 0)
    geo = score_details.get("geo_score", 0)
    geo_adj = score_details.get("geo_adjustment", 0)

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

    lines.append("")
    _append_trade_plan(lines, signal, screener_data)

    # Geo context
    lines.append("")
    geo_lbl, geo_note = geo_label(geo)
    lines.append(f"🌍 Geo: {geo_lbl}")
    if geo_adj != 0:
        lines.append(f"├ Score adjusted: {ta_score} → {adj_score} ({geo_adj:+d})")
    lines.append(f"└ {geo_note}")

    # Score
    lines.append("")
    lines.append(f"📈 Score: {adj_score}/10 (TA: {ta_score}, Geo: {geo_adj:+d})")

    if ai_analysis:
        lines.append("")
        lines.append("🤖 AI View:")
        lines.append(ai_analysis)

    # Timestamp
    now = datetime.now(timezone.utc).strftime("%b %d, %Y %I:%M %p UTC")
    lines.append(f"⏰ {now}")

    return "\n".join(lines)


def format_ta_report(ta_data, screener_data=None, latest_signal=None, ai_analysis=None):
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

    if screener_data:
        lines.append("")
        _append_indicator_lines(lines, screener_data)

    lines.append("")
    _append_trade_plan(lines, latest_signal, screener_data)

    if ai_analysis:
        lines.append("")
        lines.append("🤖 AI View:")
        lines.append(ai_analysis)

    return "\n".join(lines)


def format_daily_brief(signals_today, geo_score, geo_headlines, events=None):
    """Format the morning daily brief."""
    now = datetime.now(timezone.utc).strftime("%b %d, %Y")
    geo_lbl, geo_note = geo_label(geo_score)

    lines = [
        f"☀️ DAILY BRIEF — {now}",
        "",
        f"🌍 Geo Score: {geo_score} {geo_lbl}",
    ]
    if geo_headlines:
        for h in geo_headlines[:3]:
            lines.append(f"├ {h[:80]}")
    lines.append(f"└ {geo_note}")
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

    if events:
        lines.append("")
        lines.append("📅 Upcoming Events:")
        for ev in events[:5]:
            title = ev.get("title", "?")[:60]
            date = ev.get("dateEvent", "?")[:10]
            lines.append(f"├ {date}: {title}")

    return "\n".join(lines)


def format_accuracy_report(stats):
    """Format signal accuracy stats."""
    if not stats:
        return "📊 No accuracy data yet. Signals need 24h+ to track."

    lines = ["📊 SIGNAL ACCURACY — Last 30 Days", ""]
    total_signals = 0
    total_wins = 0
    total_losses = 0

    for s in stats:
        key = s["signal_key"].replace(".TXT", "")
        total = s["total"]
        wins = s["wins"]
        losses = s["losses"]
        total_signals += total
        total_wins += wins
        total_losses += losses
        pct = f"{wins/total*100:.0f}%" if total > 0 else "N/A"
        lines.append(f"├ {key}: {pct} hit TP ({wins}/{total})")

    if total_signals > 0:
        overall = total_wins / total_signals * 100
        lines.append("")
        lines.append(f"📈 Overall: {overall:.0f}% ({total_wins}/{total_signals})")
        lines.append(f"🛑 Stopped out: {total_losses}")

    return "\n".join(lines)


def format_geo_alert(old_score, new_score, headlines):
    """Format a geopolitical shift alert."""
    direction = "⬆️" if new_score > old_score else "⬇️"
    old_lbl, _ = geo_label(old_score)
    new_lbl, new_note = geo_label(new_score)

    lines = [
        f"🌍 GEO ALERT — Score shifted: {old_score} → {new_score} {direction}",
        "",
        f"Was: {old_lbl}",
        f"Now: {new_lbl}",
        "",
    ]

    if headlines:
        lines.append("Headlines driving the shift:")
        for h in headlines[:4]:
            lines.append(f"├ {h[:100]}")
        lines.append("")

    lines.append(f"📈 Trading implication: {new_note}")

    return "\n".join(lines)

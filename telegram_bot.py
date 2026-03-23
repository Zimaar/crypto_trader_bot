"""Telegram bot — commands + notification sender. Your control interface."""

import logging
from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, ContextTypes
)
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    MIN_SCORE_ALERT, MIN_SCORE_URGENT, ALL_SIGNAL_TYPES,
    ALLOWED_TELEGRAM_USER_IDS,
)
from database import (
    get_config, set_config, get_accuracy_stats, get_signal_count_today,
    get_last_geo_score, get_focus_symbols, set_focus_symbols
)
from altfins_client import (
    get_technical_analysis, screener_symbol, get_news, get_events, get_signal_feed
)
from ai_module import ai_enabled, analyze_symbol_setup
from geo_module import calculate_geo_score, geo_label
from formatters import (
    format_ta_report, format_accuracy_report, format_daily_brief,
)
from whatsapp_client import send_whatsapp

logger = logging.getLogger(__name__)

app: Application = None
active_chat_id: str | None = TELEGRAM_CHAT_ID or None
allowed_user_ids = {str(user_id) for user_id in ALLOWED_TELEGRAM_USER_IDS}


async def _get_target_chat_id():
    """Resolve the best chat id for outbound notifications."""
    global active_chat_id
    if active_chat_id:
        return str(active_chat_id)
    stored_chat_id = await get_config("telegram_chat_id")
    if stored_chat_id:
        active_chat_id = str(stored_chat_id)
        return active_chat_id
    return None


async def _remember_chat_from_update(update: Update):
    """Persist the latest private chat id after the user messages the bot."""
    global active_chat_id
    if not update or not update.effective_chat:
        return
    if getattr(update.effective_chat, "type", "") != "private":
        return
    chat_id = str(update.effective_chat.id)
    if chat_id == active_chat_id:
        return
    active_chat_id = chat_id
    await set_config("telegram_chat_id", chat_id)
    logger.info("Stored Telegram private chat id for outbound alerts.")


async def _prepare_private_chat(update: Update):
    """Authorize the caller and remember the active private chat."""
    user_id = str(getattr(update.effective_user, "id", ""))
    if allowed_user_ids and user_id not in allowed_user_ids:
        logger.warning(f"Rejected Telegram access from unauthorized user {user_id}.")
        if update and update.effective_message:
            await update.effective_message.reply_text("This bot is private.")
        return False
    await _remember_chat_from_update(update)
    return True


async def init_telegram():
    """Initialize the Telegram bot application."""
    global app, active_chat_id
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    stored_chat_id = await get_config("telegram_chat_id")
    if stored_chat_id:
        active_chat_id = str(stored_chat_id)

    # Register commands
    commands = [
        ("scan", "Force a full signal scan NOW"),
        ("ta", "Full TA for a coin: /ta BTC"),
        ("geo", "Current geopolitical score"),
        ("accuracy", "Signal accuracy stats (30d)"),
        ("signals", "Signal count today"),
        ("news", "Latest crypto news: /news or /news BTC"),
        ("events", "Upcoming catalyst events"),
        ("focus", "View/set focus list: /focus BTC ETH"),
        ("ai", "AI analysis for a coin: /ai BTC"),
        ("brief", "Force daily brief NOW"),
        ("pause", "Pause all alerts"),
        ("resume", "Resume alerts"),
        ("help", "Show all commands"),
    ]
    await app.bot.set_my_commands([BotCommand(c, d) for c, d in commands])

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("ta", cmd_ta))
    app.add_handler(CommandHandler("geo", cmd_geo))
    app.add_handler(CommandHandler("accuracy", cmd_accuracy))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("events", cmd_events))
    app.add_handler(CommandHandler("focus", cmd_focus))
    app.add_handler(CommandHandler("ai", cmd_ai))
    app.add_handler(CommandHandler("brief", cmd_brief))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot started and polling.")
    return app


async def send_telegram(message: str, urgent: bool = False):
    """Send a message to your Telegram chat."""
    if not app:
        return
    chat_id = await _get_target_chat_id()
    if not chat_id:
        logger.warning("Telegram chat id is not known yet. Send /start to the bot once to enable alerts.")
        return
    try:
        # Telegram max message length is 4096
        if len(message) > 4000:
            message = message[:3997] + "..."
        await app.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=None,  # plain text — most reliable
        )
        # Mirror urgent alerts to WhatsApp
        if urgent:
            await send_whatsapp(message)
    except Exception as e:
        logger.error(f"Telegram send error: {e}")


async def notify(message: str, score: int = 0):
    """
    Smart notification router.
    Score >= MIN_SCORE_URGENT → Telegram + WhatsApp
    Score >= MIN_SCORE_ALERT → Telegram only
    """
    paused = await get_config("paused")
    if paused == "true":
        logger.info("Bot paused — skipping notification.")
        return

    urgent = score >= MIN_SCORE_URGENT
    await send_telegram(message, urgent=urgent)


# ──────────────────────────────────────────────────────────────
# Command handlers
# ──────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    await update.message.reply_text(
        "🤖 CryptoEdge Signal Bot — Active\n\n"
        "I scan 2,000+ coins via ALTfins for high-probability signals, "
        "filter through TA + geopolitical context, and alert you.\n\n"
        "Use /help to see all commands."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    await update.message.reply_text(
        "📋 COMMANDS\n\n"
        "/scan — Force full signal scan NOW\n"
        "/ta BTC — Full TA report for any coin\n"
        "/ai BTC — AI analysis for any coin\n"
        "/focus BTC ETH — Alert only on selected symbols\n"
        "/focus all — Clear focus list and scan whole market\n"
        "/geo — Current geopolitical sentiment score\n"
        "/accuracy — Signal accuracy stats (30 days)\n"
        "/signals — How many signals sent today\n"
        "/news — Latest crypto headlines (or /news BTC)\n"
        "/events — Upcoming catalyst events\n"
        "/brief — Force daily brief NOW\n"
        "/pause — Pause all alerts\n"
        "/resume — Resume alerts\n"
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    await update.message.reply_text("🔍 Scanning all signal sources NOW...")
    # Import here to avoid circular imports
    from engine import run_full_scan
    count = await run_full_scan()
    await update.message.reply_text(f"✅ Scan complete. {count} alerts generated.")


async def cmd_ta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /ta BTC")
        return
    symbol = context.args[0].upper()
    await update.message.reply_text(f"🔍 Running TA for {symbol}...")

    ta = await get_technical_analysis(symbol)
    screener = await screener_symbol(symbol)
    recent_signals = await get_signal_feed(
        ALL_SIGNAL_TYPES,
        direction="BULLISH",
        hours_back=168,
        size=5,
        symbols=[symbol],
    )
    latest_signal = recent_signals[0] if recent_signals else None

    ta_data = None
    if ta and isinstance(ta, list) and ta:
        ta_data = ta[0]
    elif ta and isinstance(ta, dict):
        ta_data = ta

    msg = format_ta_report(ta_data, screener, latest_signal=latest_signal)
    await update.message.reply_text(msg)


async def cmd_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /ai BTC")
        return
    if not ai_enabled():
        await update.message.reply_text(
            "AI analysis is not configured yet. Add OPENAI_API_KEY to .env first."
        )
        return

    symbol = context.args[0].upper()
    await update.message.reply_text(f"🤖 Running AI analysis for {symbol}...")

    screener = await screener_symbol(symbol)
    recent_signals = await get_signal_feed(
        ALL_SIGNAL_TYPES,
        direction="BULLISH",
        hours_back=168,
        size=5,
        symbols=[symbol],
    )
    latest_signal = recent_signals[0] if recent_signals else None
    geo_score = await get_last_geo_score()
    ai_analysis = await analyze_symbol_setup(
        symbol=symbol,
        latest_signal=latest_signal,
        screener_data=screener,
        geo_score=geo_score,
    )

    if not ai_analysis:
        await update.message.reply_text("AI analysis is currently unavailable for that symbol.")
        return

    msg = format_ta_report(
        ta_data=None,
        screener_data=screener,
        latest_signal=latest_signal,
        ai_analysis=ai_analysis,
    )
    await update.message.reply_text(msg)


async def cmd_geo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    score = await get_last_geo_score()
    lbl, note = geo_label(score)
    await update.message.reply_text(
        f"🌍 Current Geo Score: {score}\n{lbl}\n\n{note}"
    )


async def cmd_accuracy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    stats = await get_accuracy_stats(days=30)
    msg = format_accuracy_report(stats)
    await update.message.reply_text(msg)


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    count = await get_signal_count_today()
    await update.message.reply_text(f"📊 Signals alerted today: {count}")


async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    symbol = context.args[0].upper() if context.args else None
    await update.message.reply_text("📰 Fetching news...")

    news = await get_news(asset_symbols=symbol)
    if not news:
        await update.message.reply_text("No recent news found.")
        return

    lines = ["📰 LATEST NEWS", ""]
    for n in news[:8]:
        title = n.get("title", "?")[:80]
        source = n.get("newsSource", {}).get("name", "?")
        assets = n.get("assetSymbols", "")
        lines.append(f"• [{source}] {title}")
        if assets:
            lines.append(f"  Tags: {assets}")
    await update.message.reply_text("\n".join(lines))


async def cmd_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    events = await get_events()
    if not events:
        await update.message.reply_text("No upcoming significant events found.")
        return

    lines = ["📅 UPCOMING EVENTS", ""]
    for ev in events[:10]:
        title = ev.get("title", "?")[:60]
        date = str(ev.get("dateEvent", "?"))[:10]
        symbols = ev.get("assetSymbols", "")
        lines.append(f"• {date} — {title}")
        if symbols:
            lines.append(f"  Coins: {symbols}")
    await update.message.reply_text("\n".join(lines))


async def cmd_focus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    if not context.args:
        focus = await get_focus_symbols()
        if focus:
            await update.message.reply_text(
                "🎯 Current focus list:\n" + ", ".join(focus)
            )
        else:
            await update.message.reply_text(
                "🎯 Focus list is empty. The bot is scanning the whole market."
            )
        return

    raw = " ".join(context.args).strip()
    if raw.lower() in {"all", "clear", "off"}:
        await set_focus_symbols([])
        await update.message.reply_text(
            "🎯 Focus list cleared. The bot will scan the whole market again."
        )
        return

    tokens = raw.replace(",", " ").split()
    focus = await set_focus_symbols(tokens)
    if not focus:
        await update.message.reply_text("Usage: /focus BTC ETH SOL")
        return
    await update.message.reply_text(
        "🎯 Focus list updated:\n" + ", ".join(focus)
    )


async def cmd_brief(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    await update.message.reply_text("☀️ Generating daily brief...")
    from engine import generate_daily_brief
    msg = await generate_daily_brief()
    await update.message.reply_text(msg)


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    await set_config("paused", "true")
    await update.message.reply_text("⏸️ Bot paused. No alerts will be sent. Use /resume to restart.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    await set_config("paused", "false")
    await update.message.reply_text("▶️ Bot resumed. Alerts are active.")

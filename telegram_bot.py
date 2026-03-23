"""Telegram bot — commands + notification sender. Your control interface."""

import asyncio
import logging

from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    MIN_SCORE_ALERT,
    MIN_SCORE_URGENT,
    ALL_SIGNAL_TYPES,
    ALLOWED_TELEGRAM_USER_IDS,
)
from database import (
    get_config,
    set_config,
    get_accuracy_stats,
    get_signal_count_today,
    get_focus_symbols,
    set_focus_symbols,
)
from altfins_client import screener_symbol, get_signal_feed
from ai_module import ai_enabled, analyze_symbol_setup
from formatters import format_ta_report, format_accuracy_report
from market_context import get_market_context
from news_client import get_market_news
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
        logger.warning("Rejected Telegram access from unauthorized user %s.", user_id)
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

    commands = [
        ("scan", "Force a full signal scan NOW"),
        ("ta", "Market snapshot for a coin: /ta BTC"),
        ("ai", "AI analysis for a coin: /ai BTC"),
        ("accuracy", "Signal accuracy stats (30d)"),
        ("signals", "Signal count today"),
        ("news", "Latest crypto news: /news or /news BTC"),
        ("focus", "View/set focus list: /focus BTC ETH"),
        ("brief", "Force daily brief NOW"),
        ("pause", "Pause all alerts"),
        ("resume", "Resume alerts"),
        ("help", "Show all commands"),
    ]
    await app.bot.set_my_commands([BotCommand(command, description) for command, description in commands])

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("ta", cmd_ta))
    app.add_handler(CommandHandler("accuracy", cmd_accuracy))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("news", cmd_news))
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
        if len(message) > 4000:
            message = message[:3997] + "..."
        await app.bot.send_message(chat_id=chat_id, text=message, parse_mode=None)
        if urgent:
            await send_whatsapp(message)
    except Exception as exc:
        logger.error("Telegram send error: %s", exc)


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
    if score >= MIN_SCORE_ALERT or score == 0:
        await send_telegram(message, urgent=urgent)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    await update.message.reply_text(
        "🤖 CryptoEdge Signal Bot — Active\n\n"
        "I scan ALTfins for high-probability setups, score them with trend, volume, "
        "and tracked hit-rate data, then alert you when the market context supports the trade.\n\n"
        "Use /help to see all commands."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    await update.message.reply_text(
        "📋 COMMANDS\n\n"
        "/scan — Force full signal scan NOW\n"
        "/ta BTC — Market snapshot for any coin\n"
        "/ai BTC — AI analysis for any coin\n"
        "/focus BTC ETH — Alert only on selected symbols\n"
        "/focus all — Clear focus list and scan whole market\n"
        "/accuracy — Signal accuracy stats (30 days)\n"
        "/signals — How many signals sent today\n"
        "/news — Latest crypto headlines (or /news BTC)\n"
        "/brief — Force daily brief NOW\n"
        "/pause — Pause all alerts\n"
        "/resume — Resume alerts\n"
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    await update.message.reply_text("🔍 Scanning all signal sources NOW...")
    from engine import run_full_scan
    count = await run_full_scan()
    await update.message.reply_text(f"✅ Scan complete. {count} alerts generated.")


async def _load_symbol_snapshot(symbol):
    screener_data, market_context, recent_signals = await asyncio.gather(
        screener_symbol(symbol),
        get_market_context(),
        get_signal_feed(
            ALL_SIGNAL_TYPES,
            direction="BULLISH",
            hours_back=168,
            size=5,
            symbols=[symbol],
        ),
    )
    latest_signal = recent_signals[0] if recent_signals else None
    return screener_data, market_context, latest_signal


async def cmd_ta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /ta BTC")
        return

    symbol = context.args[0].upper()
    await update.message.reply_text(f"🔍 Building market snapshot for {symbol}...")
    screener_data, market_context, latest_signal = await _load_symbol_snapshot(symbol)

    msg = format_ta_report(
        ta_data=None,
        screener_data=screener_data,
        latest_signal=latest_signal,
        market_context=market_context,
    )
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
    screener_data, market_context, latest_signal = await _load_symbol_snapshot(symbol)
    ai_analysis = await analyze_symbol_setup(
        symbol=symbol,
        latest_signal=latest_signal,
        screener_data=screener_data,
        market_context=market_context,
    )

    if not ai_analysis:
        await update.message.reply_text("AI analysis is currently unavailable for that symbol.")
        return

    msg = format_ta_report(
        ta_data=None,
        screener_data=screener_data,
        latest_signal=latest_signal,
        ai_analysis=ai_analysis,
        market_context=market_context,
    )
    await update.message.reply_text(msg)


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

    news = await get_market_news(symbol=symbol, limit=8)
    if not news:
        await update.message.reply_text("No recent market news found.")
        return

    lines = [f"📰 LATEST NEWS{' — ' + symbol if symbol else ''}", ""]
    for article in news[:8]:
        title = article.get("title", "?")[:90]
        source = article.get("source", "?")
        lines.append(f"• [{source}] {title}")
    await update.message.reply_text("\n".join(lines))


async def cmd_focus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    if not context.args:
        focus = await get_focus_symbols()
        if focus:
            await update.message.reply_text("🎯 Current focus list:\n" + ", ".join(focus))
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
    await update.message.reply_text("🎯 Focus list updated:\n" + ", ".join(focus))


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

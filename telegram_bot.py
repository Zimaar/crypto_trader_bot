"""Telegram bot — commands + notification sender. Your control interface."""

import asyncio
from datetime import datetime, timezone
import logging

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    MIN_SCORE_ALERT,
    MIN_SCORE_URGENT,
    ALL_SIGNAL_TYPES,
    ALLOWED_TELEGRAM_USER_IDS,
    SIGNAL_TYPES_BREAKOUT,
    SIGNAL_TYPES_MOMENTUM,
)
from database import (
    add_focus_symbols,
    clear_focus_symbols,
    get_accuracy_stats,
    get_config,
    get_focus_symbols,
    get_signal_count_today,
    remove_focus_symbols,
    set_config,
    set_focus_symbols,
)
from altfins_client import get_signal_feed, screener_symbol
from ai_module import ai_enabled, analyze_symbol_setup
from formatters import format_accuracy_report, format_signal_feed, format_ta_report
from market_context import get_market_context
from news_client import get_market_news
from whatsapp_client import send_whatsapp

logger = logging.getLogger(__name__)

app: Application = None
active_chat_id: str | None = TELEGRAM_CHAT_ID or None
allowed_user_ids = {str(user_id) for user_id in ALLOWED_TELEGRAM_USER_IDS}


def _parse_signal_timestamp(value):
    if not value:
        return None
    cleaned = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sort_signals_by_timestamp(signals):
    return sorted(
        signals,
        key=lambda row: _parse_signal_timestamp(row.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def _extract_fresh_signal(signals, freshness_hours=24):
    sorted_signals = _sort_signals_by_timestamp(signals)
    if not sorted_signals:
        return None, "Recent signal: none in the last 24h"

    latest_signal = sorted_signals[0]
    parsed = _parse_signal_timestamp(latest_signal.get("timestamp"))
    if not parsed:
        return latest_signal, None

    age_hours = (datetime.now(timezone.utc) - parsed).total_seconds() / 3600
    if age_hours <= freshness_hours:
        return latest_signal, None
    return None, "Recent signal: none in the last 24h"


def _resolve_asset_name(symbol, screener_data=None, recent_signals=None):
    if screener_data and isinstance(screener_data, dict):
        for key in ("name", "friendlyName", "symbolName"):
            value = screener_data.get(key)
            if value:
                return value
    for row in recent_signals or []:
        value = row.get("name") or row.get("symbolName")
        if value:
            return value
    return symbol


async def _get_target_chat_id():
    global active_chat_id
    if active_chat_id:
        return str(active_chat_id)
    stored_chat_id = await get_config("telegram_chat_id")
    if stored_chat_id:
        active_chat_id = str(stored_chat_id)
        return active_chat_id
    return None


async def _remember_chat_from_update(update: Update):
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
    user_id = str(getattr(update.effective_user, "id", ""))
    if allowed_user_ids and user_id not in allowed_user_ids:
        logger.warning("Rejected Telegram access from unauthorized user %s.", user_id)
        if update and update.effective_message:
            await update.effective_message.reply_text("This bot is private.")
        return False
    await _remember_chat_from_update(update)
    return True


async def init_telegram():
    global app, active_chat_id
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    stored_chat_id = await get_config("telegram_chat_id")
    if stored_chat_id:
        active_chat_id = str(stored_chat_id)

    commands = [
        ("scan", "Run a full priority scan now"),
        ("digest", "Send the digest now"),
        ("feed", "Latest bullish signals feed"),
        ("ta", "Market snapshot for a coin: /ta BTC"),
        ("accuracy", "Priority alert accuracy stats (30d)"),
        ("signals", "Priority alerts sent today"),
        ("news", "Latest crypto news: /news or /news BTC"),
        ("focus", "Manage focus list: /focus show"),
        ("brief", "Force daily brief now"),
        ("pause", "Pause all alerts"),
        ("resume", "Resume alerts"),
        ("help", "Show all commands"),
    ]
    await app.bot.set_my_commands([BotCommand(command, description) for command, description in commands])

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("feed", cmd_feed))
    app.add_handler(CommandHandler("ta", cmd_ta))
    app.add_handler(CommandHandler("accuracy", cmd_accuracy))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("focus", cmd_focus))
    app.add_handler(CommandHandler("brief", cmd_brief))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot started and polling.")
    return app


async def send_telegram(message: str, urgent: bool = False):
    """Send a message to the active Telegram chat."""
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
        "Use /ta for the current market read, /feed for the latest bullish signals feed, and /focus "
        "to manage your focus list. /help shows the full command set."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    await update.message.reply_text(
        "📋 COMMANDS\n\n"
        "/scan — Force a full priority scan now\n"
        "/digest — Send the digest now\n"
        "/feed — Latest bullish signals feed (or /feed BTC)\n"
        "/ta BTC — Market snapshot with embedded AI when available\n"
        "/focus — Show focus list\n"
        "/focus add BTC ETH — Add symbols to focus list\n"
        "/focus remove BTC ETH — Remove symbols from focus list\n"
        "/focus set BTC ETH — Replace focus list\n"
        "/focus clear — Clear focus list and use market-wide mode\n"
        "/accuracy — Priority alert accuracy stats (30 days)\n"
        "/signals — Priority alerts sent today\n"
        "/news — Latest crypto headlines (or /news BTC)\n"
        "/brief — Force daily brief now\n"
        "/pause — Pause all alerts\n"
        "/resume — Resume alerts\n"
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    await update.message.reply_text("🔍 Running a full priority scan...")
    from engine import run_full_scan

    results = await run_full_scan()
    await update.message.reply_text(
        f"✅ Scan complete.\nPriority alerts sent: {results['premium_sent']}\nDigest candidates found: {results['digest_candidates']}"
    )


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    await update.message.reply_text("📬 Building market digest...")
    from engine import generate_market_digest

    msg = await generate_market_digest(send=False)
    await update.message.reply_text(msg)


async def cmd_feed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    symbol = context.args[0].upper() if context.args else None
    await update.message.reply_text("🧭 Fetching signals feed...")

    rows = await get_signal_feed(
        SIGNAL_TYPES_BREAKOUT + SIGNAL_TYPES_MOMENTUM,
        direction="BULLISH",
        hours_back=24,
        size=10,
        symbols=[symbol] if symbol else None,
        prefer_cache=True,
    )
    rows = _sort_signals_by_timestamp(rows)
    await update.message.reply_text(format_signal_feed(rows, symbol=symbol, limit=10))


async def _load_symbol_snapshot(symbol):
    screener_response, market_context, recent_signals = await asyncio.gather(
        screener_symbol(symbol, return_meta=True),
        get_market_context(prefer_cache=True),
        get_signal_feed(
            ALL_SIGNAL_TYPES,
            direction="BULLISH",
            hours_back=24,
            size=5,
            symbols=[symbol],
            prefer_cache=True,
        ),
    )
    screener_data = (screener_response or {}).get("data")
    latest_signal, recent_signal_note = _extract_fresh_signal(recent_signals, freshness_hours=24)
    snapshot_status = (screener_response or {}).get("source", "unavailable")
    return screener_data, market_context, latest_signal, recent_signal_note, snapshot_status, recent_signals


async def cmd_ta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /ta BTC")
        return

    symbol = context.args[0].upper()
    await update.message.reply_text(f"🔍 Building market snapshot for {symbol}...")
    screener_data, market_context, latest_signal, recent_signal_note, snapshot_status, recent_signals = await _load_symbol_snapshot(symbol)

    ai_analysis = None
    if ai_enabled() and screener_data:
        ai_analysis = await analyze_symbol_setup(
            symbol=symbol,
            latest_signal=latest_signal,
            screener_data=screener_data,
            market_context=market_context,
        )

    msg = format_ta_report(
        ta_data=None,
        screener_data=screener_data,
        latest_signal=latest_signal,
        ai_analysis=ai_analysis,
        market_context=market_context,
        recent_signal_note=recent_signal_note,
        snapshot_status=snapshot_status,
        symbol_hint=_resolve_asset_name(symbol, screener_data=screener_data, recent_signals=recent_signals),
    )
    await update.message.reply_text(msg)


async def cmd_accuracy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    stats = await get_accuracy_stats(days=30)
    await update.message.reply_text(format_accuracy_report(stats))


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    count = await get_signal_count_today()
    await update.message.reply_text(f"📊 Priority alerts sent today: {count}")


async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    symbol = context.args[0].upper() if context.args else None
    await update.message.reply_text("📰 Fetching news...")

    asset_name = None
    if symbol:
        screener_response, recent_signals = await asyncio.gather(
            screener_symbol(symbol, return_meta=True, prefer_cache=True),
            get_signal_feed(
                SIGNAL_TYPES_BREAKOUT + SIGNAL_TYPES_MOMENTUM,
                direction="BULLISH",
                hours_back=72,
                size=3,
                symbols=[symbol],
                prefer_cache=True,
            ),
        )
        asset_name = _resolve_asset_name(
            symbol,
            screener_data=(screener_response or {}).get("data"),
            recent_signals=recent_signals,
        )

    news = await get_market_news(symbol=symbol, asset_name=asset_name, limit=8)
    if not news:
        if symbol:
            await update.message.reply_text(
                f"No coin-specific headlines found for {symbol} in the recent news window."
            )
        else:
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

    args = [arg.upper() for arg in context.args]
    if not args or args[0] == "SHOW":
        focus = await get_focus_symbols()
        if focus:
            await update.message.reply_text("🎯 Focus list:\n" + ", ".join(focus))
        else:
            await update.message.reply_text(
                "🎯 Focus list is empty. The bot is using market-wide mode."
            )
        return

    raw_first = context.args[0].strip().lower()
    if raw_first in {"all", "clear", "off"}:
        focus = await clear_focus_symbols()
        await update.message.reply_text(
            "🎯 Focus list cleared. The bot is back in market-wide mode."
        )
        return

    action = raw_first
    symbols = [token for token in args[1:] if token not in {"SHOW", "ADD", "REMOVE", "SET", "CLEAR"}]

    if action == "add":
        if not symbols:
            await update.message.reply_text("Usage: /focus add BTC ETH")
            return
        focus = await add_focus_symbols(symbols)
        await update.message.reply_text("🎯 Focus list updated:\n" + ", ".join(focus))
        return

    if action == "remove":
        if not symbols:
            await update.message.reply_text("Usage: /focus remove BTC ETH")
            return
        focus = await remove_focus_symbols(symbols)
        if focus:
            await update.message.reply_text("🎯 Focus list updated:\n" + ", ".join(focus))
        else:
            await update.message.reply_text(
                "🎯 Focus list is now empty. The bot is using market-wide mode."
            )
        return

    if action == "set":
        if not symbols:
            await update.message.reply_text("Usage: /focus set BTC ETH")
            return
        focus = await set_focus_symbols(symbols)
        await update.message.reply_text("🎯 Focus list set to:\n" + ", ".join(focus))
        return

    tokens = [arg for arg in args if arg not in {"SHOW", "ADD", "REMOVE", "SET", "CLEAR"}]
    if not tokens:
        await update.message.reply_text("Usage: /focus show | add BTC ETH | remove BTC ETH | set BTC ETH | clear")
        return

    focus = await set_focus_symbols(tokens)
    await update.message.reply_text("🎯 Focus list set to:\n" + ", ".join(focus))


async def cmd_brief(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _prepare_private_chat(update):
        return
    await update.message.reply_text("☀️ Generating daily brief...")
    from engine import generate_daily_brief

    msg = await generate_daily_brief(send=False)
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

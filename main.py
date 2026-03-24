"""
CryptoEdge Signal Bot — Main Entry Point
=========================================
Priority alerts + digest + lifecycle follow-ups → Telegram/WhatsApp

Run: python main.py
Deploy: Railway / VPS / Docker
"""

import asyncio
import logging
import sys
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import (
    ALTFINS_API_KEY,
    TELEGRAM_BOT_TOKEN,
    POLL_INTERVAL_SIGNALS,
    TIMEZONE,
    LIFECYCLE_POLL_INTERVAL_SECONDS,
)
from database import init_db
from engine import (
    cleanup_dedup_cache,
    get_market_context,
    monitor_managed_setups,
    scan_breakouts,
    scan_momentum,
    scan_pullbacks,
    send_scheduled_daily_brief,
    send_scheduled_market_digest,
    update_accuracy,
)
from telegram_bot import init_telegram, send_telegram

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


def validate_config():
    errors = []
    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN is not set")
    if not ALTFINS_API_KEY:
        errors.append("ALTFINS_API_KEY is not set")
    if errors:
        for error in errors:
            logger.error("Config error: %s", error)
        logger.error("Set these in your .env file. See .env.example.")
        sys.exit(1)


async def main():
    validate_config()
    logger.info("=" * 60)
    logger.info("  CryptoEdge Signal Bot — Starting Up")
    logger.info("=" * 60)

    await init_db()
    logger.info("Database initialized.")

    await init_telegram()
    logger.info("Telegram bot connected.")

    local_tz = ZoneInfo(TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=local_tz)
    scheduler.add_job(
        scan_breakouts,
        IntervalTrigger(seconds=180),
        id="breakouts",
        max_instances=1,
        misfire_grace_time=60,
    )
    scheduler.add_job(
        scan_momentum,
        IntervalTrigger(seconds=POLL_INTERVAL_SIGNALS),
        id="momentum",
        max_instances=1,
        misfire_grace_time=60,
    )
    scheduler.add_job(
        scan_pullbacks,
        IntervalTrigger(seconds=600),
        id="pullbacks",
        max_instances=1,
        misfire_grace_time=60,
    )
    scheduler.add_job(
        send_scheduled_market_digest,
        CronTrigger(hour="11,15,19,23", minute=0, timezone=local_tz),
        id="market_digest",
        max_instances=1,
        misfire_grace_time=900,
    )
    scheduler.add_job(
        monitor_managed_setups,
        IntervalTrigger(seconds=LIFECYCLE_POLL_INTERVAL_SECONDS),
        id="managed_setups",
        max_instances=1,
    )
    scheduler.add_job(update_accuracy, IntervalTrigger(hours=6), id="accuracy", max_instances=1)
    scheduler.add_job(
        send_scheduled_daily_brief,
        CronTrigger(hour=7, minute=0, timezone=local_tz),
        id="daily_brief",
        misfire_grace_time=900,
    )
    scheduler.add_job(
        cleanup_dedup_cache,
        CronTrigger(hour=0, minute=0, timezone=local_tz),
        id="cleanup",
    )

    scheduler.start()
    logger.info("Scheduler started with all jobs.")

    await send_telegram(
        "🤖 CryptoEdge Signal Bot — ONLINE\n\n"
        "Priority scans: Breakouts (3m), Momentum (5m)\n"
        "Digest: 11:00, 15:00, 19:00, 23:00 GST\n"
        "Lifecycle tracking: Every 15m\n"
        "Daily brief: 7:00 AM GST\n\n"
        "Use /help for commands."
    )

    logger.info("Warming caches and running initial scan...")
    await get_market_context(prefer_cache=False)
    await scan_breakouts()
    logger.info("Startup warm-up complete.")

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
        scheduler.shutdown()
        await send_telegram("🔴 CryptoEdge Bot — OFFLINE")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")

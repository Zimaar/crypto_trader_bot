"""
CryptoEdge Signal Bot — Main Entry Point
=========================================
Premium watchlist alerts + market digest + lifecycle follow-ups → Telegram/WhatsApp

Run: python main.py
Deploy: Railway / VPS / Docker
"""

import asyncio
import logging
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import (
    ALTFINS_API_KEY,
    TELEGRAM_BOT_TOKEN,
    POLL_INTERVAL_SIGNALS,
    TIMEZONE,
    MARKET_DIGEST_INTERVAL_HOURS,
    LIFECYCLE_POLL_INTERVAL_SECONDS,
)
from database import init_db
from engine import (
    cleanup_dedup_cache,
    generate_daily_brief,
    generate_market_digest,
    monitor_managed_setups,
    scan_breakouts,
    scan_momentum,
    scan_pullbacks,
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

    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
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
        generate_market_digest,
        IntervalTrigger(hours=MARKET_DIGEST_INTERVAL_HOURS),
        id="market_digest",
        max_instances=1,
    )
    scheduler.add_job(
        monitor_managed_setups,
        IntervalTrigger(seconds=LIFECYCLE_POLL_INTERVAL_SECONDS),
        id="managed_setups",
        max_instances=1,
    )
    scheduler.add_job(update_accuracy, IntervalTrigger(hours=6), id="accuracy", max_instances=1)
    scheduler.add_job(generate_daily_brief, CronTrigger(hour=7, minute=0), id="daily_brief")
    scheduler.add_job(cleanup_dedup_cache, CronTrigger(hour=0, minute=0), id="cleanup")

    scheduler.start()
    logger.info("Scheduler started with all jobs.")

    await send_telegram(
        "🤖 CryptoEdge Signal Bot — ONLINE\n\n"
        "Premium lane: Breakouts (3m), Momentum (5m)\n"
        "Market digest: Every 4h\n"
        "Lifecycle tracking: Every 15m\n"
        "Daily brief: 7:00 AM GST\n\n"
        "Use /help for commands."
    )

    logger.info("Running initial scans...")
    await scan_breakouts()
    await generate_market_digest()
    logger.info("Initial scans complete.")

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

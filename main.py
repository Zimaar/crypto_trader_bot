"""
CryptoEdge Signal Bot — Main Entry Point
=========================================
ALTfins signals + Geopolitical war awareness → Telegram/WhatsApp alerts

Run: python main.py
Deploy: Railway / VPS / Docker
"""

import asyncio
import logging
import signal
import sys
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from config import (
    POLL_INTERVAL_SIGNALS, POLL_INTERVAL_SCREENER,
    POLL_INTERVAL_NEWS, POLL_INTERVAL_GEO, TIMEZONE,
    TELEGRAM_BOT_TOKEN, ALTFINS_API_KEY,
)
from database import init_db
from telegram_bot import init_telegram, send_telegram
from engine import (
    scan_breakouts, scan_momentum, scan_pullbacks,
    scan_geo, update_accuracy, generate_daily_brief,
    cleanup_dedup_cache,
)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


def validate_config():
    """Check that required env vars are set."""
    errors = []
    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN is not set")
    if not ALTFINS_API_KEY:
        errors.append("ALTFINS_API_KEY is not set")
    if errors:
        for e in errors:
            logger.error(f"Config error: {e}")
        logger.error("Set these in your .env file. See .env.example.")
        sys.exit(1)


async def main():
    validate_config()
    logger.info("=" * 60)
    logger.info("  CryptoEdge Signal Bot — Starting Up")
    logger.info("=" * 60)

    # 1. Initialize database
    await init_db()
    logger.info("Database initialized.")

    # 2. Initialize Telegram bot
    await init_telegram()
    logger.info("Telegram bot connected.")

    # 3. Setup scheduler
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)

    # Breakout signals — every 3 minutes (most time-sensitive)
    scheduler.add_job(scan_breakouts, IntervalTrigger(seconds=180), id="breakouts",
                      max_instances=1, misfire_grace_time=60)

    # Momentum signals — every 5 minutes
    scheduler.add_job(scan_momentum, IntervalTrigger(seconds=POLL_INTERVAL_SIGNALS), id="momentum",
                      max_instances=1, misfire_grace_time=60)

    # Pullback signals — every 10 minutes
    scheduler.add_job(scan_pullbacks, IntervalTrigger(seconds=600), id="pullbacks",
                      max_instances=1, misfire_grace_time=60)

    # Geopolitical sentiment — every 15 minutes
    scheduler.add_job(scan_geo, IntervalTrigger(seconds=POLL_INTERVAL_GEO), id="geo",
                      max_instances=1, misfire_grace_time=60)

    # Accuracy tracker — every 6 hours
    scheduler.add_job(update_accuracy, IntervalTrigger(hours=6), id="accuracy",
                      max_instances=1)

    # Daily brief — 7:00 AM Dubai time
    scheduler.add_job(generate_daily_brief, CronTrigger(hour=7, minute=0), id="daily_brief")

    # Dedup cache cleanup — midnight
    scheduler.add_job(cleanup_dedup_cache, CronTrigger(hour=0, minute=0), id="cleanup")

    scheduler.start()
    logger.info("Scheduler started with all jobs.")

    # 4. Send startup message
    await send_telegram(
        "🤖 CryptoEdge Signal Bot — ONLINE\n\n"
        "Scanning: Breakouts (3m), Momentum (5m), Pullbacks (10m)\n"
        "Geo check: Every 15m\n"
        "Daily brief: 7:00 AM GST\n\n"
        "Use /help for commands."
    )

    # 5. Run initial scans
    logger.info("Running initial scans...")
    await scan_geo()
    await scan_breakouts()
    logger.info("Initial scans complete.")

    # 6. Keep running forever
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

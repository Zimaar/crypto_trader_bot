"""SQLite database for signal log, accuracy tracking, and config."""

import aiosqlite
import json
import os
from datetime import datetime, timezone

DB_PATH = os.getenv("DB_PATH", "bot_data.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                signal_key TEXT NOT NULL,
                signal_name TEXT NOT NULL,
                direction TEXT NOT NULL,
                score INTEGER NOT NULL,
                geo_score INTEGER DEFAULT 0,
                adjusted_score INTEGER NOT NULL,
                price_at_signal REAL,
                market_cap REAL,
                screener_data TEXT,
                alerted INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                price_24h REAL,
                price_72h REAL,
                price_7d REAL,
                hit_tp1 INTEGER DEFAULT 0,
                hit_stop INTEGER DEFAULT 0,
                UNIQUE(symbol, signal_key, created_at)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS geo_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                score INTEGER NOT NULL,
                headlines TEXT,
                btc_change_24h REAL,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        # Default config
        await db.execute(
            "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
            ("paused", "false"),
        )
        await db.execute(
            "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
            ("min_score", "7"),
        )
        await db.execute(
            "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
            ("focus_symbols", ""),
        )
        await db.commit()


async def log_signal(
    symbol, signal_key, signal_name, direction, score, geo_score,
    adjusted_score, price_at_signal, market_cap, screener_data=None, alerted=False
):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                """INSERT INTO signals
                   (symbol, signal_key, signal_name, direction, score, geo_score,
                    adjusted_score, price_at_signal, market_cap, screener_data, alerted, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (symbol, signal_key, signal_name, direction, score, geo_score,
                 adjusted_score, price_at_signal, market_cap,
                 json.dumps(screener_data) if screener_data else None,
                 1 if alerted else 0, now),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False  # duplicate


async def log_geo(score, headlines, btc_change):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO geo_log (score, headlines, btc_change_24h, created_at) VALUES (?, ?, ?, ?)",
            (score, json.dumps(headlines) if headlines else None, btc_change, now),
        )
        await db.commit()


async def get_last_geo_score():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT score FROM geo_log ORDER BY id DESC LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            return row["score"] if row else 0


async def get_recent_signals(symbol, hours=24):
    """Count recent signals for confluence detection."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM signals WHERE symbol = ? AND created_at > ?",
            (symbol, cutoff),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_accuracy_stats(days=30):
    """Get signal accuracy stats for the last N days."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT signal_key,
                      COUNT(*) as total,
                      SUM(CASE WHEN hit_tp1 = 1 THEN 1 ELSE 0 END) as wins,
                      SUM(CASE WHEN hit_stop = 1 THEN 1 ELSE 0 END) as losses
               FROM signals
               WHERE alerted = 1 AND created_at > ?
               GROUP BY signal_key""",
            (cutoff,),
        ) as cursor:
            return [dict(row) async for row in cursor]


async def get_signals_needing_update():
    """Get signals that need price follow-up (alerted, missing 24h/72h/7d prices)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, symbol, price_at_signal, created_at
               FROM signals
               WHERE alerted = 1 AND (price_24h IS NULL OR price_72h IS NULL OR price_7d IS NULL)
               ORDER BY created_at DESC LIMIT 50"""
        ) as cursor:
            return [dict(row) async for row in cursor]


async def update_signal_prices(signal_id, price_24h=None, price_72h=None, price_7d=None, hit_tp1=None, hit_stop=None):
    async with aiosqlite.connect(DB_PATH) as db:
        updates = []
        params = []
        if price_24h is not None:
            updates.append("price_24h = ?")
            params.append(price_24h)
        if price_72h is not None:
            updates.append("price_72h = ?")
            params.append(price_72h)
        if price_7d is not None:
            updates.append("price_7d = ?")
            params.append(price_7d)
        if hit_tp1 is not None:
            updates.append("hit_tp1 = ?")
            params.append(hit_tp1)
        if hit_stop is not None:
            updates.append("hit_stop = ?")
            params.append(hit_stop)
        if updates:
            params.append(signal_id)
            await db.execute(
                f"UPDATE signals SET {', '.join(updates)} WHERE id = ?", params
            )
            await db.commit()


async def get_config(key):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM config WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def set_config(key, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value))
        )
        await db.commit()


def _normalize_symbols(symbols):
    normalized = []
    seen = set()
    for symbol in symbols:
        clean = "".join(ch for ch in str(symbol).upper() if ch.isalnum())
        if clean and clean not in seen:
            seen.add(clean)
            normalized.append(clean)
    return normalized


async def get_focus_symbols():
    raw = await get_config("focus_symbols")
    if not raw:
        return []
    return _normalize_symbols(raw.split(","))


async def set_focus_symbols(symbols):
    normalized = _normalize_symbols(symbols)
    await set_config("focus_symbols", ",".join(normalized))
    return normalized


async def get_signal_count_today():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM signals WHERE alerted = 1 AND created_at LIKE ?",
            (f"{today}%",),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

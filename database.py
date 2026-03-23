"""SQLite database for signal log, premium lifecycle tracking, and config."""

from datetime import datetime, timedelta, timezone
import json
import os

import aiosqlite

DB_PATH = os.getenv("DB_PATH", "bot_data.db")


def _utc_now():
    return datetime.now(timezone.utc)


def _cutoff_iso(*, hours=None, days=None):
    delta = timedelta(hours=hours or 0, days=days or 0)
    return (_utc_now() - delta).isoformat()


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
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS managed_setups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER,
                symbol TEXT NOT NULL,
                lane TEXT NOT NULL,
                setup_type TEXT NOT NULL,
                breakout_price REAL,
                stop_price REAL,
                tp_price REAL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                entry_at TEXT,
                closed_at TEXT,
                notified_entry INTEGER DEFAULT 0,
                notified_tp INTEGER DEFAULT 0,
                notified_stop INTEGER DEFAULT 0,
                notified_invalidation INTEGER DEFAULT 0,
                notified_expired INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS digest_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                signal_key TEXT NOT NULL,
                bucket TEXT NOT NULL,
                score REAL NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_symbol_created_at ON signals(symbol, created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_signal_key_created_at ON signals(signal_key, created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_alerted_created_at ON signals(alerted, created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_managed_setups_status ON managed_setups(status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_managed_setups_symbol_status ON managed_setups(symbol, status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_digest_log_symbol_created_at ON digest_log(symbol, created_at)"
        )

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
    symbol,
    signal_key,
    signal_name,
    direction,
    score,
    adjusted_score,
    price_at_signal,
    market_cap,
    screener_data=None,
    alerted=False,
):
    now = _utc_now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            cursor = await db.execute(
                """INSERT INTO signals
                   (symbol, signal_key, signal_name, direction, score,
                    adjusted_score, price_at_signal, market_cap, screener_data, alerted, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol,
                    signal_key,
                    signal_name,
                    direction,
                    score,
                    adjusted_score,
                    price_at_signal,
                    market_cap,
                    json.dumps(screener_data) if screener_data else None,
                    1 if alerted else 0,
                    now,
                ),
            )
            await db.commit()
            return cursor.lastrowid
        except aiosqlite.IntegrityError:
            return None


async def get_recent_signals(symbol, hours=24):
    """Count recent premium alerts for confluence detection."""
    cutoff = _cutoff_iso(hours=hours)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM signals WHERE alerted = 1 AND symbol = ? AND created_at > ?",
            (symbol, cutoff),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def has_recent_signal(symbol, signal_key, hours=24):
    """Persisted premium-alert dedup so redeploys do not resend the same setup."""
    cutoff = _cutoff_iso(hours=hours)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT 1
               FROM signals
               WHERE alerted = 1 AND symbol = ? AND signal_key = ? AND created_at > ?
               LIMIT 1""",
            (symbol, signal_key, cutoff),
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None


async def get_recent_alerted_symbols(hours=6):
    """Return the most recent premium alert time per symbol."""
    cutoff = _cutoff_iso(hours=hours)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT symbol, MAX(created_at) AS created_at
               FROM signals
               WHERE alerted = 1 AND created_at > ?
               GROUP BY symbol""",
            (cutoff,),
        ) as cursor:
            return {
                row["symbol"].upper(): row["created_at"]
                async for row in cursor
            }


async def get_recent_digest_scores(hours=12):
    """Return the latest digest score logged for each symbol."""
    cutoff = _cutoff_iso(hours=hours)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT symbol, MAX(score) AS score
               FROM digest_log
               WHERE created_at > ?
               GROUP BY symbol""",
            (cutoff,),
        ) as cursor:
            return {
                row["symbol"].upper(): float(row["score"] or 0)
                async for row in cursor
            }


async def log_digest_candidates(candidates):
    """Persist digest-delivered candidates for suppression logic."""
    now = _utc_now().isoformat()
    rows = [
        (
            candidate["symbol"],
            candidate["signal_key"],
            candidate["digest_bucket"],
            candidate["final_score"],
            now,
        )
        for candidate in candidates
    ]
    if not rows:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            """INSERT INTO digest_log (symbol, signal_key, bucket, score, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            rows,
        )
        await db.commit()


async def create_managed_setup(
    signal_id,
    symbol,
    lane,
    setup_type,
    breakout_price,
    stop_price,
    tp_price,
    expires_hours,
):
    """Persist a premium setup for lifecycle tracking."""
    created_at = _utc_now()
    expires_at = created_at + timedelta(hours=expires_hours)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO managed_setups
               (signal_id, symbol, lane, setup_type, breakout_price, stop_price, tp_price,
                status, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                signal_id,
                symbol,
                lane,
                setup_type,
                breakout_price,
                stop_price,
                tp_price,
                "armed",
                created_at.isoformat(),
                expires_at.isoformat(),
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def get_active_managed_setups():
    """Return managed setups that still need lifecycle monitoring."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT *
               FROM managed_setups
               WHERE status IN ('armed', 'entered')
               ORDER BY created_at ASC"""
        ) as cursor:
            return [dict(row) async for row in cursor]


async def update_managed_setup(setup_id, **fields):
    """Update fields on a managed setup row."""
    if not fields:
        return

    assignments = []
    params = []
    for key, value in fields.items():
        assignments.append(f"{key} = ?")
        params.append(value)
    params.append(setup_id)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE managed_setups SET {', '.join(assignments)} WHERE id = ?",
            params,
        )
        await db.commit()


async def get_accuracy_stats(days=30):
    """Get signal accuracy stats for the last N days."""
    cutoff = _cutoff_iso(days=days)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT signal_key,
                      COUNT(*) AS total,
                      SUM(CASE WHEN hit_tp1 = 1 OR hit_stop = 1 THEN 1 ELSE 0 END) AS resolved,
                      SUM(CASE WHEN hit_tp1 = 1 THEN 1 ELSE 0 END) AS wins,
                      SUM(CASE WHEN hit_stop = 1 THEN 1 ELSE 0 END) AS losses
               FROM signals
               WHERE alerted = 1 AND created_at > ?
               GROUP BY signal_key""",
            (cutoff,),
        ) as cursor:
            return [dict(row) async for row in cursor]


async def get_signals_needing_update():
    """Get premium alerts that need price follow-up."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, symbol, price_at_signal, created_at, price_24h, price_72h, price_7d, hit_tp1, hit_stop
               FROM signals
               WHERE alerted = 1 AND (price_24h IS NULL OR price_72h IS NULL OR price_7d IS NULL)
               ORDER BY created_at DESC LIMIT 50"""
        ) as cursor:
            return [dict(row) async for row in cursor]


async def update_signal_prices(
    signal_id,
    price_24h=None,
    price_72h=None,
    price_7d=None,
    hit_tp1=None,
    hit_stop=None,
):
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
                f"UPDATE signals SET {', '.join(updates)} WHERE id = ?",
                params,
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
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, str(value)),
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


async def add_focus_symbols(symbols):
    current = await get_focus_symbols()
    return await set_focus_symbols(current + list(symbols))


async def remove_focus_symbols(symbols):
    remove_set = set(_normalize_symbols(symbols))
    current = await get_focus_symbols()
    remaining = [symbol for symbol in current if symbol not in remove_set]
    return await set_focus_symbols(remaining)


async def clear_focus_symbols():
    await set_config("focus_symbols", "")
    return []


async def get_signal_count_today():
    today = _utc_now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM signals WHERE alerted = 1 AND created_at LIKE ?",
            (f"{today}%",),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_signal_key_performance(days=60):
    """Return recent resolved performance grouped by signal type."""
    cutoff = _cutoff_iso(days=days)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT signal_key,
                      SUM(CASE WHEN hit_tp1 = 1 THEN 1 ELSE 0 END) AS wins,
                      SUM(CASE WHEN hit_stop = 1 THEN 1 ELSE 0 END) AS losses,
                      SUM(CASE WHEN hit_tp1 = 1 OR hit_stop = 1 THEN 1 ELSE 0 END) AS resolved
               FROM signals
               WHERE alerted = 1 AND created_at > ?
               GROUP BY signal_key""",
            (cutoff,),
        ) as cursor:
            rows = [dict(row) async for row in cursor]

    performance = {}
    for row in rows:
        resolved = row["resolved"] or 0
        wins = row["wins"] or 0
        losses = row["losses"] or 0
        performance[row["signal_key"].replace(".TXT", "")] = {
            "wins": wins,
            "losses": losses,
            "resolved": resolved,
            "win_rate": (wins / resolved) if resolved else 0.0,
        }
    return performance


async def get_symbol_performance(days=45):
    """Return recent resolved performance grouped by symbol."""
    cutoff = _cutoff_iso(days=days)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT symbol,
                      SUM(CASE WHEN hit_tp1 = 1 THEN 1 ELSE 0 END) AS wins,
                      SUM(CASE WHEN hit_stop = 1 THEN 1 ELSE 0 END) AS losses,
                      SUM(CASE WHEN hit_tp1 = 1 OR hit_stop = 1 THEN 1 ELSE 0 END) AS resolved
               FROM signals
               WHERE alerted = 1 AND created_at > ?
               GROUP BY symbol""",
            (cutoff,),
        ) as cursor:
            rows = [dict(row) async for row in cursor]

    performance = {}
    for row in rows:
        resolved = row["resolved"] or 0
        wins = row["wins"] or 0
        losses = row["losses"] or 0
        performance[row["symbol"].upper()] = {
            "wins": wins,
            "losses": losses,
            "resolved": resolved,
            "win_rate": (wins / resolved) if resolved else 0.0,
        }
    return performance

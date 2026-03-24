# CryptoEdge Signal Bot

**ALTfins signals + BTC market context + historical edge tracking → priority alerts, market digests, and lifecycle follow-ups**

Personal crypto signal bot that scans ALTfins for breakout and momentum setups, scores them with trend and liquidity confirmation, learns from tracked outcomes, and sends ranked priority alerts to Telegram while keeping the rest of the market in a digest lane.

## What Changed

The bot now does four things better:

1. **Learns from outcomes**: signal-type and symbol-level hit rates now feed back into scoring.
2. **Separates urgency from noise**: priority alerts are ranked and capped, while secondary setups go into a digest.
3. **Filters randomness**: alerts are gated by BTC-led market context instead of a geopolitical score.
4. **Uses stronger data fallbacks**: `/ta` uses screener snapshots and recent signals, `/feed` exposes the latest bullish Signals Feed rows, and `/news` now handles symbol-specific coverage more cleanly.

## Core Features

- Polls ALTfins every few minutes for breakout and momentum setups, then ranks them before sending
- Uses a two-lane model: priority instant alerts plus a scheduled market digest
- Scores each setup with signal type, trend, RSI, volume, confluence, and historical win-rate feedback
- Uses BTC market regime to tighten or relax alert conditions
- Sends priority Telegram alerts with mandatory `Breakout Price`, `TP`, `Profit`, and `Loss`
- Tracks priority setups after alert with `Entered`, `TP hit`, `Stop hit`, `Invalidated`, and `Expired` follow-ups
- Embeds AI analysis directly inside `/ta BTC` when OpenAI is configured
- Tracks outcomes over 24h, 72h, and 7d for feedback and accuracy reporting
- Supports a focus list with add/remove/set/clear controls

## Telegram Commands

| Command | What It Does |
|---------|-------------|
| `/scan` | Force a full priority scan now |
| `/digest` | Force the market digest now |
| `/feed` | Latest bullish signals feed |
| `/feed BTC` | Latest bullish signals feed filtered for one symbol |
| `/ta BTC` | Live market snapshot for a symbol |
| `/focus` | Show the focus list |
| `/focus add BTC ETH` | Add symbols to the focus list |
| `/focus remove BTC ETH` | Remove symbols from the focus list |
| `/focus set BTC ETH` | Replace the focus list |
| `/focus clear` | Clear the focus list and use market-wide mode |
| `/accuracy` | Signal hit-rate report over the last 30 days |
| `/signals` | Count of priority alerts sent today |
| `/news` | Latest market headlines |
| `/news BTC` | Headlines filtered for one symbol |
| `/brief` | Force the daily brief now |
| `/pause` | Pause alerts |
| `/resume` | Resume alerts |

## How Scoring Works

Every signal now goes through these layers:

1. Base weight by ALTfins signal type
2. Trend alignment from screener data
3. RSI quality check
4. Relative-volume confirmation
5. Confluence bonus for multiple recent signals
6. Historical edge bonus or penalty from tracked outcomes
7. BTC market-context gating before alert delivery

Signals still need to clear the alert threshold, but delivery now depends on lane:

1. Priority instant alerts are breakout-first, ranked by score, relative volume, and market cap
2. Priority alerts are capped per scan and per day
3. Non-focus or overflow setups are pushed into the digest lane instead of interrupting instantly
4. Exact symbol+signal dedup stays at 24h, while cross-signal symbol cooldown applies to priority alerts

## Quick Setup

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Required environment variables:

- `ALTFINS_API_KEY`
- `TELEGRAM_BOT_TOKEN`

Common optional variables:

- `TELEGRAM_CHAT_ID`
- `ALLOWED_TELEGRAM_USER_IDS`
- `NEWSAPI_KEY`
- `OPENAI_API_KEY`
- `AI_ALERT_ANALYSIS_ENABLED=true`
- `FOCUS_SYMBOLS=BTC,ETH,SOL`
- `DB_PATH=/data/bot_data.db`
- `MARKET_DIGEST_INTERVAL_HOURS=4`
- `PREMIUM_SYMBOL_COOLDOWN_HOURS=6`

## Railway Deployment

This bot is Railway-friendly:

- Deploy from the included `Dockerfile`
- Set env vars in Railway
- Mount a persistent volume and point `DB_PATH` to `/data/bot_data.db`
- Keep replicas at `1` so only one polling bot is live
- Keep `.env` local and do not commit secrets

## File Structure

```text
crypto_signal_bot/
├── main.py              # Entry point and scheduler
├── engine.py            # Priority lane, digest lane, and lifecycle loop
├── signal_scorer.py     # Scoring and historical edge weighting
├── market_context.py    # BTC-led market regime logic
├── news_client.py       # NewsAPI + RSS fallback
├── altfins_client.py    # ALTfins signals, screener, and price snapshots
├── telegram_bot.py      # Telegram command handlers
├── formatters.py        # Alert and report formatting
├── trade_levels.py      # Breakout, TP, profit, loss calculation
├── database.py          # SQLite persistence, watchlist, digest, and lifecycle tracking
├── .env.example         # Environment template
└── Dockerfile           # Railway / Docker deployment
```

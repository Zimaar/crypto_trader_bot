# CryptoEdge Signal Bot

**ALTfins signals + BTC market context + historical edge tracking → Telegram alerts**

Personal crypto signal bot that scans ALTfins for breakout, pullback, and momentum setups, scores them with trend and liquidity confirmation, learns from tracked outcomes, and sends focused alerts to Telegram.

## What Changed

The bot now does three things better:

1. **Learns from outcomes**: signal-type and symbol-level hit rates now feed back into scoring.
2. **Filters randomness**: alerts are gated by BTC-led market context instead of a geopolitical score.
3. **Uses stronger data fallbacks**: `/ta` uses screener snapshots and recent signals, while `/news` uses NewsAPI with RSS fallback.

## Core Features

- Polls ALTfins every few minutes for breakout, momentum, and pullback signals
- Scores each setup with signal type, trend, RSI, volume, confluence, and historical win-rate feedback
- Uses BTC market regime to tighten or relax alert conditions
- Sends Telegram alerts with mandatory `Breakout Price`, `TP`, `Profit`, and `Loss`
- Supports AI analysis with OpenAI via `/ai BTC`
- Tracks outcomes over 24h, 72h, and 7d for feedback and accuracy reporting
- Supports a personal focus list with `/focus BTC ETH SOL`

## Telegram Commands

| Command | What It Does |
|---------|-------------|
| `/scan` | Force a full signal scan now |
| `/ta BTC` | Live market snapshot for a symbol |
| `/ai BTC` | AI analysis using the latest setup |
| `/focus BTC ETH SOL` | Alert only on selected symbols |
| `/focus all` | Clear the focus list |
| `/accuracy` | Signal hit-rate report over the last 30 days |
| `/signals` | Count of alerts sent today |
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

Signals still need to clear the alert threshold, but the threshold becomes stricter when BTC is in a risk-off regime.

## Quick Setup

```bash
cp .env.example .env
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

## Railway Deployment

This bot is Railway-friendly:

- Deploy from the included `Dockerfile`
- Set env vars in Railway
- Mount a persistent volume and point `DB_PATH` to `/data/bot_data.db`
- Keep `.env` local and do not commit secrets

## File Structure

```text
crypto_signal_bot/
├── main.py              # Entry point and scheduler
├── engine.py            # Scan, score, filter, alert loop
├── signal_scorer.py     # Scoring and historical edge weighting
├── market_context.py    # BTC-led market regime logic
├── news_client.py       # NewsAPI + RSS fallback
├── altfins_client.py    # ALTfins signals, screener, and price snapshots
├── telegram_bot.py      # Telegram command handlers
├── formatters.py        # Alert and report formatting
├── trade_levels.py      # Breakout, TP, profit, loss calculation
├── database.py          # SQLite persistence and accuracy tracking
├── .env.example         # Environment template
└── Dockerfile           # Railway / Docker deployment
```

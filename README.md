# CryptoEdge Signal Bot

**ALTfins signals + geopolitical war awareness → Telegram/WhatsApp alerts**

Personal trading signal bot that scans 2,000+ crypto assets for high-probability breakouts,
pullbacks, and momentum signals — filtered through TA quality scoring AND real-time
geopolitical context (US-Iran war, macro headlines, sanctions, BTC fear barometer).

---

## What It Does

1. **Polls ALTfins** every 3–10 minutes for breakout, momentum, and pullback signals
2. **Scores each signal 0–10** using multi-layer confirmation (trend, RSI, volume, confluence)
3. **Adjusts for geopolitics** — suppresses signals during escalation, boosts during de-escalation
4. **Sends you alerts** via Telegram (and optionally WhatsApp) with full TA breakdown
5. **Tracks accuracy** — checks if past signals hit targets or stops over 24h/72h/7d

---

## Quick Setup (5 minutes)

### Step 1: Get Your API Keys

| Service | Where | Notes |
|---------|-------|-------|
| **ALTfins API** | [altfins.com](https://altfins.com) → Account → API Keys | Free tier = 1,000 credits/mo |
| **Telegram Bot** | Message [@BotFather](https://t.me/BotFather) → /newbot | Gives you the bot token |
| **Telegram Chat ID** | Message [@userinfobot](https://t.me/userinfobot) | Your numeric chat ID |
| **NewsAPI** (optional) | [newsapi.org](https://newsapi.org) | Free = 100 req/day. For geo scoring |
| **WhatsApp** (optional) | [Meta Business](https://business.facebook.com) → WhatsApp API | Free 1,000 convos/mo |

### Step 2: Configure

```bash
cp .env.example .env
# Edit .env with your API keys
```

### Step 3: Run Locally

```bash
pip install -r requirements.txt
python main.py
```

### Step 4: Deploy (Always-On)

**Option A: Railway (Recommended — $5/mo)**
```bash
# Install Railway CLI: https://docs.railway.app/guides/cli
railway login
railway init
railway up
# Set env vars in Railway dashboard
```

**Option B: Docker (Any VPS)**
```bash
docker build -t crypto-bot .
docker run -d --env-file .env --name crypto-bot crypto-bot
```

**Option C: DigitalOcean / AWS / Any VPS**
```bash
# SSH into your server
git clone <your-repo>
cd crypto_signal_bot
pip install -r requirements.txt
# Use screen/tmux/systemd to keep it running
screen -S bot
python main.py
# Ctrl+A, D to detach
```

---

## Telegram Commands

| Command | What It Does |
|---------|-------------|
| `/scan` | Force a full signal scan NOW |
| `/ta BTC` | Full TA report for any coin |
| `/ai BTC` | AI read on the latest setup for any coin |
| `/focus BTC ETH SOL` | Alert only on selected symbols |
| `/focus all` | Clear the focus list and scan the whole market |
| `/geo` | Current geopolitical sentiment score (-3 to +3) |
| `/accuracy` | Signal hit rate over last 30 days |
| `/signals` | Count of alerts sent today |
| `/news` | Latest crypto headlines (or `/news BTC`) |
| `/events` | Upcoming catalyst events |
| `/brief` | Force daily morning brief |
| `/pause` | Pause all alerts |
| `/resume` | Resume alerts |

---

## How Scoring Works

Every signal passes through 6 layers:

```
Layer 1: ALTfins signal fires (base weight by type)
Layer 2: Screener cross-check (trend + MACD alignment)
Layer 3: RSI sweet spot (40–65 = bonus, >75 = penalty)
Layer 4: Volume confirmation (>1.3x = bonus, >2.5x = double bonus)
Layer 5: Confluence detection (2+ signals on same coin = bonus)
Layer 6: Geopolitical adjustment (-3 to +1 modifier)
```

Only signals scoring **7+** (after geo adjustment) reach your phone.
Signals scoring **9+** hit both Telegram AND WhatsApp.

---

## Geopolitical Score (-3 to +3)

| Score | Meaning | Effect |
|-------|---------|--------|
| +3 | Confirmed de-escalation | Signals boosted +1 |
| +2 | De-escalation hints | Signals boosted +1 |
| +1 | Calm | No change |
| 0 | Mixed/uncertain | No change |
| -1 | Escalation rhetoric | Signals reduced -1 |
| -2 | Active strikes | Only score 9+ passes. Most suppressed. |
| -3 | Major escalation | Buy signals suppressed entirely. |

---

## File Structure

```
crypto_signal_bot/
├── main.py              # Entry point — starts scheduler + Telegram bot
├── config.py            # All settings from .env
├── engine.py            # Core scan/score/alert loop
├── altfins_client.py    # ALTfins API async wrapper
├── signal_scorer.py     # Multi-layer signal scoring
├── geo_module.py        # Geopolitical sentiment (war/macro)
├── formatters.py        # Telegram message templates
├── telegram_bot.py      # Telegram commands + notifications
├── whatsapp_client.py   # WhatsApp Cloud API (optional)
├── database.py          # SQLite for logs + accuracy tracking
├── requirements.txt     # Python dependencies
├── .env.example         # Environment variable template
├── Dockerfile           # For Docker/Railway deployment
└── README.md            # This file
```

---

## Customization

**Change alert threshold:** Set `MIN_SCORE_ALERT` in `.env` (default: 7)

**Change polling frequency:** Adjust `POLL_INTERVAL_*` in `.env`

**Focus alerts on your coins:** Set `FOCUS_SYMBOLS` in `.env` or use `/focus BTC ETH SOL`

**Enable AI analysis:** Set `OPENAI_API_KEY` in `.env` and use `/ai BTC`

**Add new signal types:** Edit `ALL_SIGNAL_TYPES` in `config.py`

**Adjust geo keywords:** Edit `ESCALATION_KEYWORDS` / `DEESCALATION_KEYWORDS` in `geo_module.py`

**Persist SQLite across deploys:** Set `DB_PATH` to a mounted volume path such as `/data/bot_data.db`

---

## Cost

| Service | Cost |
|---------|------|
| ALTfins API (Starter) | $49/mo |
| Railway hosting | $5/mo |
| NewsAPI | Free (100 req/day) |
| WhatsApp Cloud API | Free (1,000 convos/mo) |
| Telegram Bot | Free |
| **Total** | **~$54/mo** |

One good trade pays for a year of this.

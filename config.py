"""Configuration loaded from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()


# --- ALTfins ---
ALTFINS_API_KEY = os.getenv("ALTFINS_API_KEY", "")
ALTFINS_BASE_URL = os.getenv("ALTFINS_BASE_URL", "https://altfins.com")

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ALLOWED_TELEGRAM_USER_IDS = [
    user_id.strip()
    for user_id in os.getenv("ALLOWED_TELEGRAM_USER_IDS", "").split(",")
    if user_id.strip()
]

# --- WhatsApp ---
WHATSAPP_ENABLED = os.getenv("WHATSAPP_ENABLED", "false").lower() == "true"
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID", "")
WHATSAPP_TO_NUMBER = os.getenv("WHATSAPP_TO_NUMBER", "")

# --- News ---
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")

# --- AI / OpenAI ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "low")
AI_ALERT_ANALYSIS_ENABLED = os.getenv("AI_ALERT_ANALYSIS_ENABLED", "false").lower() == "true"

# --- Scoring ---
MIN_SCORE_ALERT = int(os.getenv("MIN_SCORE_ALERT", "7"))
MIN_SCORE_URGENT = int(os.getenv("MIN_SCORE_URGENT", "9"))
MIN_MARKET_CAP = float(os.getenv("MIN_MARKET_CAP", "50000000"))

# --- Polling intervals (seconds) ---
POLL_INTERVAL_SIGNALS = int(os.getenv("POLL_INTERVAL_SIGNALS", "300"))

# --- Timezone ---
TIMEZONE = os.getenv("TIMEZONE", "Asia/Dubai")

# --- Focus list ---
FOCUS_SYMBOLS = [
    symbol.strip().upper()
    for symbol in os.getenv("FOCUS_SYMBOLS", "").split(",")
    if symbol.strip()
]

# --- Signal weights ---
SIGNAL_WEIGHTS = {
    "FRESH_MOMENTUM_MACD_SIGNAL_LINE_CROSSOVER": 3,
    "PULLBACK_UP_DOWN_TREND": 3,
    "PULLBACK_UP_DOWN_TREND_1W": 3,
    "SUPPORT_RESISTANCE_BREAKOUT": 4,
    "UP_DOWN_TREND_AND_FRESH_MOMENTUM_INFLECTION": 3,
    "MOMENTUM_RSI_CONFIRMATION": 4,
    "EMA_12_50_CROSSOVERS": 2,
    "SIGNALS_SUMMARY_PATTERN_BREAKOUTS": 5,
    "SIGNALS_SUMMARY_PATTERN_BREAKOUTS_UPTREND_DOWNTREND": 5,
}

# Signal types to poll
SIGNAL_TYPES_BREAKOUT = [
    "SUPPORT_RESISTANCE_BREAKOUT",
    "SIGNALS_SUMMARY_PATTERN_BREAKOUTS",
    "SIGNALS_SUMMARY_PATTERN_BREAKOUTS_UPTREND_DOWNTREND",
]
SIGNAL_TYPES_MOMENTUM = [
    "FRESH_MOMENTUM_MACD_SIGNAL_LINE_CROSSOVER",
    "UP_DOWN_TREND_AND_FRESH_MOMENTUM_INFLECTION",
    "MOMENTUM_RSI_CONFIRMATION",
    "EMA_12_50_CROSSOVERS",
]
SIGNAL_TYPES_PULLBACK = [
    "PULLBACK_UP_DOWN_TREND",
    "PULLBACK_UP_DOWN_TREND_1W",
]
ALL_SIGNAL_TYPES = SIGNAL_TYPES_BREAKOUT + SIGNAL_TYPES_MOMENTUM + SIGNAL_TYPES_PULLBACK

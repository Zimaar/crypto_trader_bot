"""Optional OpenAI-powered market analysis helpers."""

import asyncio
import json
import logging
from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_REASONING_EFFORT
from trade_levels import build_trade_plan, format_percent, format_price

logger = logging.getLogger(__name__)

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def ai_enabled():
    """Return True when AI analysis is configured."""
    return client is not None


def _compact_screener_data(screener_data):
    if not isinstance(screener_data, dict):
        return {}
    add = screener_data.get("additionalData", screener_data)
    keys = [
        "RSI14", "MACD", "SHORT_TERM_TREND", "MEDIUM_TERM_TREND",
        "LONG_TERM_TREND", "VOLUME_RELATIVE", "PRICE_CHANGE_1D",
        "PRICE_CHANGE_1W", "PRICE_CHANGE_1M", "SUPPORT", "RESISTANCE",
        "MARKET_CAP",
    ]
    return {key: add.get(key) for key in keys if add.get(key) not in (None, "")}


async def analyze_symbol_setup(symbol, latest_signal=None, screener_data=None, market_context=None):
    """Generate a concise AI market read for a symbol."""
    if not client:
        return None

    trade_plan = build_trade_plan(latest_signal, screener_data)
    snapshot = {
        "symbol": symbol,
        "latest_signal": latest_signal or {},
        "screener_data": _compact_screener_data(screener_data or {}),
        "market_context": {
            "label": (market_context or {}).get("label"),
            "summary": (market_context or {}).get("summary"),
            "snapshot": (market_context or {}).get("snapshot"),
            "filter_line": (market_context or {}).get("filter_line"),
        },
        "trade_plan": {
            "breakout_price": format_price(trade_plan["breakout_price"]),
            "tp_price": format_price(trade_plan["tp_price"]),
            "profit": format_percent(trade_plan["profit_pct"]),
            "loss": format_percent(-trade_plan["loss_pct"]) if trade_plan["loss_pct"] is not None else "N/A",
        },
    }

    def _call():
        response = client.responses.create(
            model=OPENAI_MODEL,
            reasoning={"effort": OPENAI_REASONING_EFFORT},
            input=[
                {
                    "role": "developer",
                    "content": (
                        "You are a disciplined crypto market analyst. "
                        "Use only the supplied data. Do not invent prices, catalysts, or indicators. "
                        "Return plain text with five short lines in this exact order: "
                        "Bias:, Breakout Price:, TP:, Profit/Loss:, Why:. "
                        "The Breakout Price, TP, and Profit/Loss values must match the supplied trade plan. "
                        "The Profit/Loss line must include both upside and downside in one line. "
                        "When referencing BTC context, use trader language such as 'BTC filter is Neutral' or "
                        "'supportive backdrop' and explain whether the setup depends on coin-specific strength "
                        "or benefits from market tailwind. "
                        "Do not use phrases like 'mixed market', 'the bot waits', 'currently', 'right now', or "
                        "'at the moment'. "
                        "Keep the full answer under 120 words."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Analyze this trading setup for a Telegram bot user.\n\n"
                        f"{json.dumps(snapshot, ensure_ascii=True, indent=2)}"
                    ),
                },
            ],
        )
        return (response.output_text or "").strip()

    try:
        return await asyncio.to_thread(_call)
    except Exception as e:
        logger.warning(f"OpenAI analysis failed for {symbol}: {e}")
        return None

"""WhatsApp Cloud API client for mirroring critical alerts."""

import httpx
import logging
from config import WHATSAPP_ENABLED, WHATSAPP_TOKEN, WHATSAPP_PHONE_ID, WHATSAPP_TO_NUMBER

logger = logging.getLogger(__name__)

WA_API_URL = f"https://graph.facebook.com/v19.0/{WHATSAPP_PHONE_ID}/messages"


async def send_whatsapp(message: str):
    """Send a text message via WhatsApp Cloud API."""
    if not WHATSAPP_ENABLED:
        return
    if not all([WHATSAPP_TOKEN, WHATSAPP_PHONE_ID, WHATSAPP_TO_NUMBER]):
        logger.warning("WhatsApp credentials not configured.")
        return

    # Truncate if too long (WhatsApp limit ~4096 chars)
    if len(message) > 4000:
        message = message[:3997] + "..."

    payload = {
        "messaging_product": "whatsapp",
        "to": WHATSAPP_TO_NUMBER,
        "type": "text",
        "text": {"body": message},
    }
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(WA_API_URL, json=payload, headers=headers)
            if resp.status_code == 200:
                logger.info("WhatsApp message sent successfully.")
            else:
                logger.error(f"WhatsApp error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"WhatsApp send error: {e}")

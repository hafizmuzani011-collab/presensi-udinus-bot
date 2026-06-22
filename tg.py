"""Telegram API client - async, tidak block event loop."""
import logging
import os
import httpx

from config import BOT_TOKEN

logger = logging.getLogger("telegram_bot")
ALLOWED_CHAT_ID: int | None = None  # legacy, di-set dari bot.py
_logged_in_403 = False


def set_default_chat_id(chat_id: int | None) -> None:
    global ALLOWED_CHAT_ID
    ALLOWED_CHAT_ID = chat_id


async def send_message(text: str, parse_mode: str = "Markdown") -> bool:
    if not ALLOWED_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": ALLOWED_CHAT_ID, "text": text, "parse_mode": parse_mode}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, json=payload)
        if r.status_code == 200:
            from config import STATS
            STATS["messages_sent"] += 1
            logger.info(f"Pesan terkirim ke {ALLOWED_CHAT_ID}")
            return True
        logger.error(f"Gagal kirim pesan: {r.status_code} {r.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"Koneksi error: {e}")
        return False


async def send_photo(photo_path: str) -> bool:
    if not ALLOWED_CHAT_ID or not os.path.exists(photo_path):
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, "rb") as f:
            files = {"photo": (os.path.basename(photo_path), f, "image/png")}
            data = {"chat_id": str(ALLOWED_CHAT_ID)}
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(url, data=data, files=files)
        if r.status_code == 200:
            from config import STATS
            STATS["photos_sent"] += 1
            logger.info(f"Foto terkirim")
            return True
        logger.error(f"Gagal kirim foto: {r.status_code}")
        return False
    except Exception as e:
        logger.error(f"Gagal kirim foto: {e}")
        return False


async def get_updates(offset: int | None = None) -> list:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    try:
        async with httpx.AsyncClient(timeout=35) as client:
            r = await client.get(url, params=params)
        if r.status_code == 200:
            return r.json().get("result", [])
        logger.error(f"getUpdates: {r.status_code} {r.text[:200]}")
        return []
    except Exception as e:
        logger.error(f"Koneksi error: {e}")
        return []

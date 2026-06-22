"""Telegram API client - async, tidak block event loop."""
import logging
import os
import httpx

from config import BOT_TOKEN, inc_stat

logger = logging.getLogger("telegram_bot")


def _get_target_chat_ids(chat_ids: list[int] | None = None) -> list[int]:
    if chat_ids:
        return chat_ids
    from config import ALLOWED_CHAT_IDS
    return ALLOWED_CHAT_IDS


async def send_message(text: str, parse_mode: str = "Markdown", reply_markup: dict | None = None,
                       chat_ids: list[int] | None = None) -> bool:
    targets = _get_target_chat_ids(chat_ids)
    if not targets:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    success = False
    async with httpx.AsyncClient(timeout=30) as client:
        for chat_id in targets:
            payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
            if reply_markup:
                payload["reply_markup"] = reply_markup
            try:
                r = await client.post(url, json=payload)
                if r.status_code == 200:
                    inc_stat("messages_sent")
                    logger.info(f"Pesan terkirim ke {chat_id}")
                    success = True
                else:
                    logger.error(f"Gagal kirim pesan ke {chat_id}: {r.status_code} {r.text[:200]}")
            except Exception as e:
                logger.error(f"Koneksi error ke {chat_id}: {e}")
    return success


def make_inline_keyboard(buttons: list[list[dict]]) -> dict:
    """Helper: bikin inline keyboard markup.
    buttons = [[{"text": "Hadir", "callback_data": "presensi_saya"}], ...]
    """
    return {"inline_keyboard": buttons}


async def answer_callback(callback_query_id: str, text: str = "") -> bool:
    """Acknowledge inline keyboard click."""
    if not BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={"callback_query_id": callback_query_id, "text": text})
        return r.status_code == 200
    except Exception:
        return False


async def edit_message(chat_id: int, message_id: int, text: str) -> bool:
    """Edit message yang udah dikirim (untuk update inline keyboard)."""
    if not BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, json={
                "chat_id": chat_id, "message_id": message_id,
                "text": text, "parse_mode": "Markdown"
            })
        return r.status_code == 200
    except Exception:
        return False


async def send_photo(photo_path: str, chat_ids: list[int] | None = None) -> bool:
    targets = _get_target_chat_ids(chat_ids)
    if not targets or not os.path.exists(photo_path):
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    success = False
    with open(photo_path, "rb") as f:
        file_bytes = f.read()
    async with httpx.AsyncClient(timeout=60) as client:
        for chat_id in targets:
            try:
                files = {"photo": (os.path.basename(photo_path), file_bytes, "image/png")}
                data = {"chat_id": str(chat_id)}
                r = await client.post(url, data=data, files=files)
                if r.status_code == 200:
                    inc_stat("photos_sent")
                    logger.info(f"Foto terkirim ke {chat_id}")
                    success = True
                else:
                    logger.error(f"Gagal kirim foto ke {chat_id}: {r.status_code}")
            except Exception as e:
                logger.error(f"Gagal kirim foto ke {chat_id}: {e}")
    return success


async def get_updates(offset: int | None = None) -> list[dict]:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(url, params=params)
        if r.status_code == 200:
            return r.json().get("result", [])
        logger.error(f"getUpdates: {r.status_code} {r.text[:200]}")
        return []
    except Exception as e:
        logger.error(f"Koneksi error: {e}")
        return []

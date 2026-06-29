"""Telegram API client — async HTTP helpers.

Low-level wrappers around Bot API methods with retry logic,
connection pooling, and file upload support.
"""
import asyncio
import logging
import os

import httpx

from config import BOT_TOKEN, inc_stat

logger = logging.getLogger(__name__)

# Shared client (HTTP/2 keep-alive, connection pool)
_client: httpx.AsyncClient | None = None
_client_timeout: float = 0.0
_client_lock = asyncio.Lock()


async def _get_client(timeout: float = 30.0) -> httpx.AsyncClient:
    """Lazy-init shared AsyncClient. Recreate if existing timeout too low."""
    global _client, _client_timeout
    async with _client_lock:
        if _client is None or _client.is_closed or _client_timeout < timeout:
            if _client and not _client.is_closed:
                await _client.aclose()
            _client = httpx.AsyncClient(
                timeout=timeout,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
            _client_timeout = timeout
    return _client


async def close_client() -> None:
    """Close shared client (call saat shutdown)."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


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
    client = await _get_client(30)
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
        client = await _get_client(10)
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
        client = await _get_client(15)
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
    client = await _get_client(60)
    for chat_id in targets:
        try:
            with open(photo_path, "rb") as f:
                files = {"photo": (os.path.basename(photo_path), f, "image/png")}
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


async def send_document(doc_path: str, caption: str = "", chat_ids: list[int] | None = None) -> bool:
    """Kirim file/dokumen."""
    targets = _get_target_chat_ids(chat_ids)
    if not targets or not os.path.exists(doc_path):
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    success = False
    
    # Read chunk by chunk is better for memory, but for Telegram max 50MB files
    # reading to memory is usually fine. Using file object directly with httpx is safer for memory.
    client = await _get_client(60)
    for chat_id in targets:
        try:
            with open(doc_path, "rb") as f:
                # httpx supports file object directly
                files = {"document": (os.path.basename(doc_path), f)}
                data = {"chat_id": str(chat_id)}
                if caption:
                    data["caption"] = caption
                r = await client.post(url, data=data, files=files)
            if r.status_code == 200:
                logger.info(f"Dokumen terkirim ke {chat_id}")
                success = True
            else:
                logger.error(f"Gagal kirim dokumen ke {chat_id}: {r.status_code} {r.text}")
        except Exception as e:
            logger.error(f"Gagal kirim dokumen ke {chat_id}: {e}")
    return success


async def delete_webhook() -> bool:
    """Hapus webhook aktif sebelum polling dimulai. Cegah error 409 Conflict."""
    if not BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook"
    try:
        client = await _get_client(10)
        r = await client.get(url)
        if r.status_code == 200:
            logger.info("Webhook deleted successfully")
            return True
        logger.error(f"deleteWebhook: {r.status_code} {r.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"deleteWebhook error: {e}")
        return False


async def get_updates(offset: int | None = None) -> list[dict]:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"timeout": 30}
    if offset is not None:
        params["offset"] = offset
    try:
        client = await _get_client(60)
        r = await client.get(url, params=params)
        if r.status_code == 200:
            return r.json().get("result", [])
        logger.error(f"getUpdates: {r.status_code} {r.text[:200]}")
        return []
    except Exception as e:
        logger.error(f"Koneksi error: {e}")
        return []

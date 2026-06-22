"""Voice reminder via TTS (Text-to-Speech).
Pakai edge-tts (Microsoft Edge neural voices, gratis).
Output .mp3, kirim via Telegram sendVoice.
"""
import asyncio
import logging
import os
import tempfile
from datetime import datetime

logger = logging.getLogger("telegram_bot")

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    logger.warning("edge-tts not available")


async def text_to_voice(text: str, voice: str = "id-ID-ArdiNeural") -> str | None:
    """Convert text ke file .mp3. Return path atau None."""
    if not EDGE_TTS_AVAILABLE:
        return None
    try:
        os.makedirs("voices", exist_ok=True)
        path = os.path.join("voices", f"voice_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3")
        comm = edge_tts.Communicate(text, voice=voice)
        await comm.save(path)
        return path
    except Exception as e:
        logger.error(f"TTS error: {e}")
        return None

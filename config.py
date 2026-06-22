"""Konfigurasi bot - credentials dari .env (fallback ke hardcoded untuk dev)."""
import os
from datetime import datetime

def _load_env():
    """Load .env file (jika ada) ke os.environ, tanpa dependency eksternal."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_load_env()

# ==== Telegram ====
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8617660963:AAFc7NYirNw_Q30dxu2eiavh_x-8p1Si9uE")
CHAT_ID_FILE = "chat_id.txt"

# ==== Files ====
SCHEDULES_FILE = "schedules.json"
LOG_FILE = "bot.log"
TASKS_DEADLINE_FILE = "tasks_deadlines.json"
SCREENSHOT_TUGAS = "bukti_tugas.png"
SCREENSHOT_PRESENSI = "bukti_presensi.png"
LOCK_FILE = "bot.lock"
OFFSET_FILE = "telegram_offset.json"

# ==== URLs ====
KULINO_URL = "https://kulino.dinus.ac.id/"
MHS_URL = "https://mhs.dinus.ac.id/"

# ==== Credentials (dari env dengan fallback) ====
KULINO_ACCOUNTS = {
    "saya": {
        "nim": os.getenv("KULINO_SAYA_NIM", "a222503103"),
        "password": os.getenv("KULINO_SAYA_PASS", "Dinus-19082006"),
        "name": "Hafizh",
    },
    "pacar": {
        "nim": os.getenv("KULINO_PACAR_NIM", "a112415549"),
        "password": os.getenv("KULINO_PACAR_PASS", "Nailahazfa20"),
        "name": "Azfa",
    },
}
MHS_ACCOUNTS = {
    "saya": {
        "nim": os.getenv("MHS_SAYA_NIM", "A22.2025.03103"),
        "password": os.getenv("MHS_SAYA_PASS", "Hafiiz12345"),
        "name": "Hafizh",
    },
    "pacar": {
        "nim": os.getenv("MHS_PACAR_NIM", "A11.2024.15549"),
        "password": os.getenv("MHS_PACAR_PASS", "Nailahazfa20"),
        "name": "Azfa",
    },
}

# ==== LLM (optional) ====
CLAUDEFIRE_API_KEY = os.getenv("CLAUDEFIRE_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-flash-free")

# ==== Bot State ====
AUTOPILOT_ENABLED = True
ALLOWED_CHAT_ID = None
ALLOWED_CHAT_IDS: list[int] = []
BOT_START_TIME = datetime.now()
STATS_FILE = "stats.json"
STATS = {
    "messages_received": 0,
    "messages_sent": 0,
    "photos_sent": 0,
    "tugas_checks": 0,
    "presensi_done": 0,
    "errors": 0,
}

def load_stats():
    """Load stats dari file supaya tidak hilang saat restart."""
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE) as f:
                return {**STATS, **json.load(f)}
        except (OSError, json.JSONDecodeError):
            pass
    return STATS

def save_stats():
    """Simpan stats ke file."""
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(STATS, f, indent=2)
    except OSError:
        pass

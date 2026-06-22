"""Konfigurasi bot - credentials WAJIB dari .env, TIDAK ada fallback hardcoded."""
import os
import json
import logging
import threading
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ==== Telegram ====
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN tidak ditemukan! Buat file .env dari .env.example")
CHAT_ID_FILE = "chat_id.txt"

# ==== Files ====
SCHEDULES_FILE = "schedules.json"
LOG_FILE = "bot.log"
TASKS_DEADLINE_FILE = "tasks_deadlines.json"
SCREENSHOT_TUGAS = "bukti_tugas.png"
SCREENSHOT_PRESENSI = "bukti_presensi.png"
LOCK_FILE = "bot.lock"
OFFSET_FILE = "telegram_offset.json"
PRESENSI_DONE_FILE = "presensi_done.json"
PRESENSI_HISTORY_FILE = "presensi_history.json"

# ==== URLs ====
KULINO_URL = "https://kulino.dinus.ac.id/"
MHS_URL = "https://mhs.dinus.ac.id/"

# ==== Credentials (WAJIB dari .env) ====
def _req_env(key, display_name):
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"{display_name} ({key}) tidak ditemukan di .env!")
    return val

KULINO_ACCOUNTS = {
    "saya": {
        "nim": _req_env("KULINO_SAYA_NIM", "Kulino NIM Hafizh"),
        "password": _req_env("KULINO_SAYA_PASS", "Kulino password Hafizh"),
        "name": "Hafizh",
    },
    "pacar": {
        "nim": _req_env("KULINO_PACAR_NIM", "Kulino NIM Azfa"),
        "password": _req_env("KULINO_PACAR_PASS", "Kulino password Azfa"),
        "name": "Azfa",
    },
}
MHS_ACCOUNTS = {
    "saya": {
        "nim": _req_env("MHS_SAYA_NIM", "MHS NIM Hafizh"),
        "password": _req_env("MHS_SAYA_PASS", "MHS password Hafizh"),
        "name": "Hafizh",
    },
    "pacar": {
        "nim": _req_env("MHS_PACAR_NIM", "MHS NIM Azfa"),
        "password": _req_env("MHS_PACAR_PASS", "MHS password Azfa"),
        "name": "Azfa",
    },
}

# ==== LLM (optional) ====
CLAUDEFIRE_API_KEY = os.getenv("CLAUDEFIRE_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-flash-free")

# ==== Bot State ====
ALLOWED_CHAT_ID = None
ALLOWED_CHAT_IDS: list[int] = []
BOT_START_TIME = datetime.now()
LOG_DIR = "logbook"
STATS_FILE = "stats.json"
STATS = {
    "messages_received": 0,
    "messages_sent": 0,
    "photos_sent": 0,
    "tugas_checks": 0,
    "presensi_done": 0,
    "errors": 0,
}
STATS_LOCK = threading.Lock()


def inc_stat(key: str, n: int = 1) -> None:
    """Thread-safe increment STATS[key]."""
    with STATS_LOCK:
        STATS[key] = STATS.get(key, 0) + n


def get_stats_snapshot() -> dict:
    """Thread-safe copy of STATS for dashboard."""
    with STATS_LOCK:
        return dict(STATS)


def save_stats() -> bool:
    """Persist STATS to disk (atomic write)."""
    try:
        snapshot = get_stats_snapshot()
        tmp = STATS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
        os.replace(tmp, STATS_FILE)
        return True
    except OSError as e:
        logging.getLogger(__name__).error(f"Save stats gagal: {e}")
        return False


# ==== Control state (shared between bot.py and web_dashboard.py) ====
# Dipindah ke sini agar tidak circular import.
CONTROL = {"autopilot": True, "trigger_tugas": 0, "last_msg": ""}
CONTROL_LOCK = threading.Lock()


def get_control(key: str, default=None):
    """Thread-safe read CONTROL[key]."""
    with CONTROL_LOCK:
        return CONTROL.get(key, default)


def set_control(key: str, value) -> None:
    """Thread-safe write CONTROL[key]."""
    with CONTROL_LOCK:
        CONTROL[key] = value


def consume_control(key: str, default=None):
    """Thread-safe read & reset CONTROL[key] (atomic). Returns prior value."""
    with CONTROL_LOCK:
        val = CONTROL.get(key, default)
        if key in CONTROL:
            CONTROL[key] = default if default is not None else ""
        return val

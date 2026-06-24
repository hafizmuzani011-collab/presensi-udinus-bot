"""Konfigurasi bot - credentials WAJIB dari .env, TIDAK ada fallback hardcoded."""
import os
import json
import logging
import threading
from datetime import datetime
from dotenv import load_dotenv
from file_utils import atomic_write


load_dotenv()

# ==== Data directory (relative to CWD; override via CONFIG_DATA_DIR env) ====
DATA_DIR = os.environ.get("CONFIG_DATA_DIR", "data")
LOG_DIR = os.path.join(DATA_DIR, "logbook")
LOG_FILE = os.path.join(DATA_DIR, "logs", "bot.log")
RUNTIME_DIR = os.path.join(DATA_DIR, "runtime")
SCREENSHOTS_DIR = os.path.join(DATA_DIR, "screenshots")
VOICES_DIR = os.path.join(DATA_DIR, "voices")

# ==== Telegram ====
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN tidak ditemukan! Buat file .env dari .env.example")
CHAT_ID_FILE = os.path.join(RUNTIME_DIR, "chat_id.txt")

# ==== Files ====
SCHEDULES_FILE = os.path.join(RUNTIME_DIR, "schedules.json")
TASKS_DEADLINE_FILE = os.path.join(RUNTIME_DIR, "tasks_deadlines.json")
LOCK_FILE = os.path.join(RUNTIME_DIR, "bot.lock")
OFFSET_FILE = os.path.join(RUNTIME_DIR, "telegram_offset.json")
PRESENSI_DONE_FILE = os.path.join(RUNTIME_DIR, "presensi_done.json")
PRESENSI_HISTORY_FILE = os.path.join(RUNTIME_DIR, "presensi_history.json")
STATS_FILE = os.path.join(RUNTIME_DIR, "stats.json")
NILAI_FILE = os.path.join(RUNTIME_DIR, "nilai_cache.json")
KHS_HISTORY_FILE = os.path.join(RUNTIME_DIR, "khs_history.json")
MATERIALS_CACHE_FILE = os.path.join(RUNTIME_DIR, "materials_cache.json")

SCREENSHOT_TUGAS = os.path.join(SCREENSHOTS_DIR, "bukti_tugas.png")
SCREENSHOT_PRESENSI = os.path.join(SCREENSHOTS_DIR, "bukti_presensi.png")
SCREENSHOT_JADWAL = os.path.join(SCREENSHOTS_DIR, "bukti_jadwal.png")

# ==== URLs ====
KULINO_URL = "https://kulino.dinus.ac.id/"
MHS_URL = "https://mhs.dinus.ac.id/"

# ==== Display names (bisa dikustom dari .env) ====
NAMA_SAYA = os.getenv("NAMA_SAYA", "Hafizh")
NAMA_PACAR = os.getenv("NAMA_PACAR", "Azfa")

# ==== Admin bootstrap (optional, recommended) ====
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

# ==== Credentials (WAJIB dari .env) ====
def _req_env(key, display_name):
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"{display_name} ({key}) tidak ditemukan di .env!")
    return val

KULINO_ACCOUNTS = {
    "saya": {
        "nim": _req_env("KULINO_SAYA_NIM", "Kulino NIM"),
        "password": _req_env("KULINO_SAYA_PASS", "Kulino password"),
        "name": NAMA_SAYA,
    },
    "pacar": {
        "nim": _req_env("KULINO_PACAR_NIM", "Kulino NIM pacar"),
        "password": _req_env("KULINO_PACAR_PASS", "Kulino password pacar"),
        "name": NAMA_PACAR,
    },
}
MHS_ACCOUNTS = {
    "saya": {
        "nim": _req_env("MHS_SAYA_NIM", "MHS NIM"),
        "password": _req_env("MHS_SAYA_PASS", "MHS password"),
        "name": NAMA_SAYA,
    },
    "pacar": {
        "nim": _req_env("MHS_PACAR_NIM", "MHS NIM pacar"),
        "password": _req_env("MHS_PACAR_PASS", "MHS password pacar"),
        "name": NAMA_PACAR,
    },
}

# ==== LLM (optional) ====
CLAUDEFIRE_API_KEY = os.getenv("CLAUDEFIRE_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-flash-free")

# ==== Bot State ====
ALLOWED_CHAT_ID = None
ALLOWED_CHAT_IDS: list[int] = []
BOT_START_TIME = datetime.now()
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
    """Persist STATS to disk (quick write without fsync — stats is non-critical)."""
    try:
        snapshot = get_stats_snapshot()
        atomic_write(STATS_FILE, json.dumps(snapshot, indent=2), use_fsync=False)
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

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
# ==== Persistent account store ====
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")

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


def _load_accounts_from_env() -> dict:
    """Build default accounts dict from .env."""
    return {
        "kulino": {
            "saya": {"nim": os.getenv("KULINO_SAYA_NIM", ""), "password": os.getenv("KULINO_SAYA_PASS", ""), "name": NAMA_SAYA},
            "pacar": {"nim": os.getenv("KULINO_PACAR_NIM", ""), "password": os.getenv("KULINO_PACAR_PASS", ""), "name": NAMA_PACAR},
        },
        "mhs": {
            "saya": {"nim": os.getenv("MHS_SAYA_NIM", ""), "password": os.getenv("MHS_SAYA_PASS", ""), "name": NAMA_SAYA},
            "pacar": {"nim": os.getenv("MHS_PACAR_NIM", ""), "password": os.getenv("MHS_PACAR_PASS", ""), "name": NAMA_PACAR},
        },
    }

def _load_accounts_from_file() -> dict:
    """Load accounts from accounts.json if it exists."""
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}

def save_accounts_to_file(data: dict) -> None:
    """Persist accounts dict to accounts.json."""
    os.makedirs(os.path.dirname(ACCOUNTS_FILE), exist_ok=True)
    atomic_write(ACCOUNTS_FILE, json.dumps(data, indent=2, ensure_ascii=False))

def reload_accounts() -> None:
    """Reload MHS_ACCOUNTS and KULINO_ACCOUNTS from accounts.json (or .env fallback)."""
    file_data = _load_accounts_from_file()
    env_data = _load_accounts_from_env()

    kulino_src = file_data.get("kulino") or env_data.get("kulino", {})
    mhs_src = file_data.get("mhs") or env_data.get("mhs", {})

    with CONTROL_LOCK:
        KULINO_ACCOUNTS.clear()
        KULINO_ACCOUNTS.update(kulino_src)
        MHS_ACCOUNTS.clear()
        MHS_ACCOUNTS.update(mhs_src)



# Initialize accounts: prefer file, fallback to .env
_accounts = _load_accounts_from_file() or _load_accounts_from_env()
KULINO_ACCOUNTS = _accounts.get("kulino", {})
MHS_ACCOUNTS = _accounts.get("mhs", {})

if not os.path.exists(ACCOUNTS_FILE):
    save_accounts_to_file(_accounts)


# ==== LLM (optional) ====
CLAUDEFIRE_API_KEY = os.getenv("CLAUDEFIRE_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-flash-free")

# ==== Google Calendar iCal (optional) ====
GCAL_SAYA_ICAL_URL = os.getenv("GCAL_SAYA_ICAL_URL", "")
GCAL_PACAR_ICAL_URL = os.getenv("GCAL_PACAR_ICAL_URL", "")

DASH_TOKEN = os.getenv("DASH_TOKEN") or ""
HEALTH_PUBLIC = os.getenv("HEALTH_PUBLIC", "0") == "1"  # Set to "1" for public /health (e.g. load balancer)
ALLOWED_CHAT_ID = None
ALLOWED_CHAT_IDS: set[int] = set()
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

async def asave_stats() -> bool:
    import asyncio
    return await asyncio.to_thread(save_stats)


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
            CONTROL[key] = default
        return val

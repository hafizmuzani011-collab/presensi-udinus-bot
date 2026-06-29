"""Multi-user registration & account management.
Each Telegram user can register with their own Kulino/MHS credentials.
Accounts stored separately from owner's accounts.json.
"""
import json
import logging
import os
import time
from datetime import datetime

from file_utils import atomic_write_json

logger = logging.getLogger(__name__)
USER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "user_accounts.json")
_cache: dict | None = None

# Rate limiting: track register attempts per chat_id
# Format: {chat_id: (attempts, first_attempt_timestamp)}
_register_attempts: dict[str, tuple[int, float]] = {}
_MAX_REGISTER_ATTEMPTS = 5
_REGISTER_WINDOW = 300  # 5 minutes


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    os.makedirs(os.path.dirname(USER_FILE), exist_ok=True)
    if not os.path.exists(USER_FILE):
        _cache = {}
        return _cache
    try:
        with open(USER_FILE, encoding="utf-8") as f:
            _cache = json.load(f)
    except (json.JSONDecodeError, OSError):
        _cache = {}
    return _cache


def _save(data: dict):
    global _cache
    _cache = data
    try:
        atomic_write_json(USER_FILE, data)
    except OSError as e:
        logger.error(f"Failed to save user_accounts.json: {e}")


def _check_rate_limit(chat_id: str) -> bool:
    """Check if register attempt is rate-limited. Returns True if allowed."""
    now = time.time()
    attempts, first_attempt = _register_attempts.get(chat_id, (0, now))

    # Reset if window expired
    if now - first_attempt > _REGISTER_WINDOW:
        _register_attempts[chat_id] = (1, now)
        return True

    # Check limit
    if attempts >= _MAX_REGISTER_ATTEMPTS:
        remaining = int(_REGISTER_WINDOW - (now - first_attempt))
        logger.warning(f"Rate limit hit for chat_id {chat_id}: {attempts} attempts, {remaining}s left")
        return False

    _register_attempts[chat_id] = (attempts + 1, first_attempt)
    return True


def register(chat_id: int, nim: str, password_mhs: str, password_kulino: str, name: str = "") -> bool:
    """Register a new user. Returns False if already registered or rate-limited."""
    cid = str(chat_id)

    # Rate limiting check
    if not _check_rate_limit(cid):
        return False

    data = _load()
    if cid in data:
        return False
    data[cid] = {
        "name": name or nim,
        "registered_at": datetime.now().isoformat(),
        "mhs": {"nim": nim, "password": password_mhs},
        "kulino": {"nim": nim, "password": password_kulino},
    }
    _save(data)
    # Clear rate limit on successful registration
    _register_attempts.pop(cid, None)
    return True


def unregister(chat_id: int) -> bool:
    data = _load()
    cid = str(chat_id)
    if cid not in data:
        return False
    del data[cid]
    _save(data)
    return True


def get_info(chat_id: int) -> dict | None:
    return _load().get(str(chat_id))


def get_nim(chat_id: int, platform: str = "mhs") -> str | None:
    user = get_info(chat_id)
    if user and platform in user:
        return user[platform].get("nim")
    return None


def get_all() -> dict:
    return dict(_load())


def count() -> int:
    return len(_load())


def list_users() -> list[dict]:
    """Return list of user summaries (no passwords)."""
    users = []
    for cid, info in _load().items():
        users.append({
            "chat_id": cid,
            "name": info.get("name", ""),
            "nim": info.get("mhs", {}).get("nim", info.get("kulino", {}).get("nim", "")),
            "registered_at": info.get("registered_at", ""),
        })
    return sorted(users, key=lambda u: u["registered_at"], reverse=True)

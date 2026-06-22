"""Persistence layer - semua file I/O terpusat di sini."""
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from config import (
    CHAT_ID_FILE, LOCK_FILE, OFFSET_FILE,
    SCHEDULES_FILE, TASKS_DEADLINE_FILE, LOG_DIR,
)


# ============ Chat ID ============
def load_chat_ids() -> list[int]:
    if not os.path.exists(CHAT_ID_FILE):
        return []
    data = Path(CHAT_ID_FILE).read_text().strip()
    return [int(p) for p in data.split(",") if p.strip().lstrip("-").isdigit()]


def save_chat_id(chat_id: int) -> None:
    ids = load_chat_ids()
    if chat_id not in ids:
        ids.append(chat_id)
    Path(CHAT_ID_FILE).write_text(",".join(str(i) for i in ids))


# ============ Lock & Offset ============
def acquire_lock(pid: int) -> bool:
    if os.path.exists(LOCK_FILE):
        try:
            old_pid = int(Path(LOCK_FILE).read_text().strip())
            _kill_old_instance(old_pid)
        except (ValueError, OSError):
            pass
    try:
        Path(LOCK_FILE).write_text(str(pid))
        return True
    except OSError:
        return False


def _kill_old_instance(old_pid: int) -> None:
    """Kill old process by PID (helper for backward compat)."""
    import psutil
    if psutil.pid_exists(old_pid):
        try:
            p = psutil.Process(old_pid)
            if p.name().lower().startswith("python"):
                p.terminate()
                try:
                    p.wait(timeout=5)
                except psutil.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=3)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def release_lock(pid: int) -> None:
    try:
        if os.path.exists(LOCK_FILE):
            if Path(LOCK_FILE).read_text().strip() == str(pid):
                os.remove(LOCK_FILE)
    except OSError:
        pass


def save_offset(offset: int) -> None:
    try:
        with open(OFFSET_FILE, "w") as f:
            json.dump({"offset": offset}, f)
    except OSError:
        pass


def load_offset() -> int | None:
    if not os.path.exists(OFFSET_FILE):
        return None
    try:
        with open(OFFSET_FILE) as f:
            return int(json.load(f).get("offset"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


# ============ Schedules ============
def load_schedules() -> dict:
    if not os.path.exists(SCHEDULES_FILE):
        return {}
    with open(SCHEDULES_FILE) as f:
        return json.load(f)


# ============ Tasks Deadlines ============
def load_tasks_deadlines() -> dict:
    if not os.path.exists(TASKS_DEADLINE_FILE):
        return {"notified": {}}
    try:
        with open(TASKS_DEADLINE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"notified": {}}


def save_tasks_deadlines(data: dict) -> None:
    with open(TASKS_DEADLINE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def backup_tasks_deadlines() -> bool:
    """Buat backup .bak. Return True kalau berhasil."""
    if not os.path.exists(TASKS_DEADLINE_FILE):
        return False
    try:
        shutil.copy2(TASKS_DEADLINE_FILE, TASKS_DEADLINE_FILE + ".bak")
        return True
    except OSError:
        return False


def cleanup_expired_deadlines() -> int:
    """Hapus deadline yang sudah lewat. Return jumlah dihapus."""
    cache = load_tasks_deadlines()
    now = datetime.now()
    removed = 0
    for task_key in list(cache.keys()):
        if task_key == "notified":
            continue
        iso = cache[task_key].get("deadline_iso", "")
        if iso:
            try:
                if datetime.fromisoformat(iso) < now:
                    del cache[task_key]
                    notified = cache.get("notified", {})
                    for nk in list(notified.keys()):
                        if nk.startswith(task_key):
                            del notified[nk]
                    removed += 1
            except ValueError:
                pass
    save_tasks_deadlines(cache)
    return removed


def write_logbook(date_str: str, account_key: str, jam: str, matkul: str, ruang: str, status: str) -> None:
    """Catat presensi ke logbook/{date}.md (append mode)."""
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)
    path = os.path.join(LOG_DIR, f"{date_str}.md")
    icon = "✅" if status == "hadir" else "❌"
    line = f"- {jam} - {matkul} {icon} ({account_key}, Ruang {ruang})\n"
    with open(path, "a", encoding="utf-8") as f:
        # Header kalau file baru
        if os.path.getsize(path) == 0:
            from datetime import datetime
            dt = datetime.fromisoformat(date_str)
            f.write(f"## {dt.strftime('%A, %d %B %Y')}\n\n")
        f.write(line)

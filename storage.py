"""Data persistence — chat IDs, schedules, tasks, and logbook.

Async-first storage layer with file locking and atomic writes.
Uses JSON files for persistence with thread-safe operations.
"""
import asyncio
import json
import logging
import os
import re
import shutil
from calendar import monthrange as _monthrange
from datetime import datetime
from pathlib import Path

import threading
from config import (
    CHAT_ID_FILE, OFFSET_FILE,
    SCHEDULES_FILE, TASKS_DEADLINE_FILE, LOG_DIR,
    PRESENSI_DONE_FILE, PRESENSI_HISTORY_FILE, NILAI_FILE,
    MATERIALS_CACHE_FILE, KHS_HISTORY_FILE,
)
from file_utils import atomic_write

logger = logging.getLogger(__name__)

# Global lock for all json storage files to prevent race conditions
# between Flask dashboard thread and Asyncio Bot Loop
_storage_lock = threading.RLock()

# ============ State persistence (reminders, snoozes) ============
_STATE_FILE = os.path.join(os.path.dirname(PRESENSI_DONE_FILE), "state.json")


def load_state() -> dict:
    with _storage_lock:
        if not os.path.exists(_STATE_FILE):
            return {"reminder_sent": [], "snoozed": {}}
        try:
            with open(_STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {"reminder_sent": [], "snoozed": {}}


def save_state(data: dict) -> None:
    with _storage_lock:
        try:
            atomic_write(_STATE_FILE, json.dumps(data, indent=2, ensure_ascii=False))
        except OSError:
            pass


async def aload_state() -> dict:
    return await asyncio.to_thread(load_state)


async def asave_state(data: dict) -> None:
    await asyncio.to_thread(save_state, data)


async def aload_chat_ids() -> list[int]:
    return await asyncio.to_thread(load_chat_ids)


async def asave_chat_id(chat_id: int) -> None:
    await asyncio.to_thread(save_chat_id, chat_id)


async def aload_offset() -> int | None:
    return await asyncio.to_thread(load_offset)


async def asave_offset(offset: int) -> None:
    await asyncio.to_thread(save_offset, offset)


async def aload_schedules() -> dict:
    return await asyncio.to_thread(load_schedules)


async def aload_tasks_deadlines() -> dict:
    return await asyncio.to_thread(load_tasks_deadlines)


async def asave_tasks_deadlines(data: dict) -> None:
    await asyncio.to_thread(save_tasks_deadlines, data)


async def acleanup_expired_deadlines() -> int:
    return await asyncio.to_thread(cleanup_expired_deadlines)


async def awrite_logbook(date_str: str, account_key: str, jam: str, matkul: str, ruang: str, status: str) -> None:
    await asyncio.to_thread(write_logbook, date_str, account_key, jam, matkul, ruang, status)


async def aload_presensi_done() -> dict:
    return await asyncio.to_thread(load_presensi_done)


async def asave_presensi_done(date_str: str, keys: set) -> None:
    await asyncio.to_thread(save_presensi_done, date_str, keys)


async def aload_nilai_cache() -> dict:
    return await asyncio.to_thread(load_nilai_cache)


async def asave_nilai_cache(data: dict) -> None:
    await asyncio.to_thread(save_nilai_cache, data)


async def aload_material_cache() -> dict:
    return await asyncio.to_thread(load_material_cache)


async def asave_material_cache(data: dict) -> None:
    await asyncio.to_thread(save_material_cache, data)


async def aload_khs_history() -> dict:
    return await asyncio.to_thread(load_khs_history)


async def asave_khs_history(data: dict) -> None:
    await asyncio.to_thread(save_khs_history, data)

# ============ Chat ID ============
def load_chat_ids() -> list[int]:
    with _storage_lock:
        if not os.path.exists(CHAT_ID_FILE):
            return []
        data = Path(CHAT_ID_FILE).read_text().strip()
    ids = []
    for p in data.split(","):
        p = p.strip()
        if not p:
            continue
        try:
            ids.append(int(p))
        except ValueError:
            logger.warning(f"Skipping invalid chat_id: {p!r}")
            continue
    return ids


def save_chat_id(chat_id: int) -> None:
    with _storage_lock:
        ids = load_chat_ids()
        if chat_id not in ids:
            ids.append(chat_id)
        atomic_write(CHAT_ID_FILE, ",".join(str(i) for i in ids))


# ============ Offset (lock sekarang di instance_lock.py) ============


def save_offset(offset: int) -> None:
    with _storage_lock:
        try:
            atomic_write(OFFSET_FILE, json.dumps({"offset": offset}))
        except OSError:
            pass


def load_offset() -> int | None:
    with _storage_lock:
        if not os.path.exists(OFFSET_FILE):
            return None
        try:
            with open(OFFSET_FILE) as f:
                return int(json.load(f).get("offset"))
        except (OSError, ValueError, json.JSONDecodeError) as e:
            logger.warning(f"Load offset corrupt: {e}")
            return None


# ============ Schedules ============
def load_schedules() -> dict:
    with _storage_lock:
        if not os.path.exists(SCHEDULES_FILE):
            return {}
        try:
            with open(SCHEDULES_FILE) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"{SCHEDULES_FILE} corrupt, reset: {e}")
            return {}

    # Validasi schema: harus dict of dict of list
    if not isinstance(data, dict):
        logger.warning(f"{SCHEDULES_FILE}: invalid type, reset")
        return {}
    for who, days in data.items():
        if not isinstance(days, dict):
            logger.warning(f"{SCHEDULES_FILE}: {who} not a dict, reset")
            return {}
        for day, slots in days.items():
            if not isinstance(slots, list):
                logger.warning(f"{SCHEDULES_FILE}: {who}/{day} not a list, reset")
                return {}
            for slot in slots:
                if not isinstance(slot, list) or len(slot) != 3:
                    logger.warning(f"{SCHEDULES_FILE}: {who}/{day} invalid slot, reset")
                    return {}
    return data


# ============ Tasks Deadlines ============
def load_tasks_deadlines() -> dict:
    with _storage_lock:
        if not os.path.exists(TASKS_DEADLINE_FILE):
            return {"notified": {}}
        try:
            with open(TASKS_DEADLINE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Tasks deadlines corrupt, reset: {e}")
            return {"notified": {}}


def save_tasks_deadlines(data: dict) -> None:
    with _storage_lock:
        atomic_write(TASKS_DEADLINE_FILE, json.dumps(data, indent=2, ensure_ascii=False))


def backup_tasks_deadlines() -> bool:
    """Buat backup .bak. Return True kalau berhasil."""
    return backup_file(TASKS_DEADLINE_FILE)


def backup_file(path: str) -> bool:
    """Generic: backup file ke .bak. Return True kalau berhasil."""
    if not os.path.exists(path):
        return False
    try:
        shutil.copy2(path, path + ".bak")
        return True
    except OSError:
        return False


def run_backup() -> int:
    """Backup semua file critical. Return jumlah file yg di-backup."""
    count = 0
    for path in [TASKS_DEADLINE_FILE, SCHEDULES_FILE, PRESENSI_HISTORY_FILE]:
        if backup_file(path):
            count += 1
    if count:
        logger.info(f"Backup: {count} file")
    return count


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
    with _storage_lock:
        with open(path, "a", encoding="utf-8") as f:
            # Header kalau file baru
            if os.path.getsize(path) == 0:
                dt = datetime.fromisoformat(date_str)
                f.write(f"## {dt.strftime('%A, %d %B %Y')}\n\n")
            f.write(line)


# ============ Presensi Done (persist across restart) ============
def load_presensi_done() -> dict:
    """Load {_date, keys: [...]}. Auto-reset jika tanggal berbeda."""
    with _storage_lock:
        if not os.path.exists(PRESENSI_DONE_FILE):
            return {"date": "", "keys": []}
        try:
            with open(PRESENSI_DONE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            logger.warning(f"Presensi done corrupt, reset: {e}")
            return {"date": "", "keys": []}


def save_presensi_done(date_str: str, keys: set) -> None:
    """Simpan sesi presensi yang sudah selesai. Auto-reset besoknya."""
    with _storage_lock:
        try:
            atomic_write(
                PRESENSI_DONE_FILE,
                json.dumps({"date": date_str, "keys": sorted(keys)}, indent=2, ensure_ascii=False),
            )
        except OSError:
            pass


# ============ Presensi Stats / Attendance Tracker ============
_LOGBOOK_LINE = re.compile(
    r"- (\S+) - (.+?) ([✅❌]) \(([^,]+), Ruang ([^)]+)\)"
)


def parse_logbook_line(line: str) -> dict | None:
    """Parse line: '- 14:10-15:50 - BASIS DATA ✅ (saya, Ruang D.2.J)'"""
    m = _LOGBOOK_LINE.match(line.strip())
    if not m:
        return None
    jam, matkul, icon, account, ruang = m.groups()
    return {
        "jam": jam, "matkul": matkul, "hadir": icon == "✅",
        "account": account, "ruang": ruang,
    }


def load_logbook_entries(year: int, month: int, account_key: str = "") -> list[dict]:
    """Load logbook entries for a given month, optionally filter by account_key."""
    entries = []
    if not os.path.exists(LOG_DIR):
        return entries
    for fn in os.listdir(LOG_DIR):
        if not fn.endswith(".md"):
            continue
        date_str = fn[:-3]
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if dt.year != year or dt.month != month:
            continue
        with open(os.path.join(LOG_DIR, fn), encoding="utf-8") as f:
            for line in f:
                entry = parse_logbook_line(line.strip())
                if entry and (not account_key or entry["account"] == account_key):
                    entry["date"] = date_str
                    entries.append(entry)
    return entries


def compute_attendance(
    schedules: dict, account_key: str, year: int, month: int,
) -> list[dict]:
    """Compute attendance per course for a given account/month.

    Returns [{"matkul": str, "total": int, "hadir": int, "pct": float}, ...]
    """
    from constants import HARI_ID

    if account_key not in schedules:
        return []

    account_sched = schedules[account_key]
    logbook = load_logbook_entries(year, month, account_key)

    # Count total classes per course for this month
    total_per_course: dict[str, int] = {}
    _, days_in_month = _monthrange(year, month)
    # Only count up to today if current month
    today = datetime.now()
    max_day = days_in_month if (year != today.year or month != today.month) else today.day

    # Pre-calculate counts of each day-of-week in the range
    day_counts: dict[str, int] = {}
    for day in range(1, max_day + 1):
        dt = datetime(year, month, day)
        day_name = dt.strftime("%A").lower()
        hari_id = HARI_ID.get(day_name, "")
        if hari_id:
            day_counts[hari_id] = day_counts.get(hari_id, 0) + 1

    for hari_id, slots in account_sched.items():
        multiplier = day_counts.get(hari_id, 0)
        if multiplier > 0:
            for _, mk, _ in slots:
                total_per_course[mk] = total_per_course.get(mk, 0) + multiplier

    # Count attended from logbook
    hadir_per_course: dict[str, int] = {}
    for e in logbook:
        if e["hadir"]:
            hadir_per_course[e["matkul"]] = hadir_per_course.get(e["matkul"], 0) + 1

    # Merge
    result = []
    for mk in sorted(total_per_course.keys()):
        total = total_per_course[mk]
        hadir = hadir_per_course.get(mk, 0)
        pct = round((hadir / total) * 100, 1) if total > 0 else 0.0
        result.append({"matkul": mk, "total": total, "hadir": hadir, "pct": pct})

    return result


def attendance_alert(results: list[dict], threshold: float = 75.0) -> list[str]:
    """Return warning messages for courses below threshold."""
    warnings = []
    for r in results:
        if r["pct"] < threshold and r["total"] >= 3:
            need = int((threshold / 100) * r["total"]) - r["hadir"]
            if need > 0:
                warnings.append(
                    f"{r['matkul']}: {r['pct']}% hadir "
                    f"({r['hadir']}/{r['total']}) — "
                    f"butuh {need}x lagi biar aman {threshold}%"
                )
    return warnings


# ============ Nilai Cache (untuk auto-detect nilai baru) ============
def load_nilai_cache() -> dict:
    """Load cache nilai terakhir per akun: {akun: {kdmk: {huruf, matkul, ...}}}."""
    with _storage_lock:
        if not os.path.exists(NILAI_FILE):
            return {}
        try:
            with open(NILAI_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError, json.JSONDecodeError):
            return {}


def save_nilai_cache(data: dict) -> None:
    with _storage_lock:
        try:
            atomic_write(NILAI_FILE, json.dumps(data, indent=2, ensure_ascii=False))
        except OSError:
            pass


def diff_nilai(old: dict, new: dict) -> list[dict]:
    """Return list nilai baru (berdasarkan kdmk) antara old vs new per akun."""
    added = []
    for akun, new_courses in new.items():
        old_courses = old.get(akun, {})
        for kdmk, info in new_courses.items():
            old_info = old_courses.get(kdmk, {})
            old_huruf = old_info.get("huruf", "")
            new_huruf = info.get("huruf", "")
            if new_huruf and new_huruf != old_huruf:
                added.append({"akun": akun, "kdmk": kdmk, **info,
                              "old": old_huruf, "new": new_huruf})
    return added


def load_material_cache() -> dict:
    with _storage_lock:
        if not os.path.exists(MATERIALS_CACHE_FILE):
            return {}
        try:
            with open(MATERIALS_CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError, json.JSONDecodeError):
            return {}


def save_material_cache(data: dict) -> None:
    with _storage_lock:
        try:
            atomic_write(MATERIALS_CACHE_FILE, json.dumps(data, indent=2, ensure_ascii=False))
        except OSError:
            pass


# ============ KHS History ============
def load_khs_history() -> dict:
    """Load history KHS: {akun: {semester_id: {ipk, ips, total_sks}}}."""
    with _storage_lock:
        if not os.path.exists(KHS_HISTORY_FILE):
            return {}
        try:
            with open(KHS_HISTORY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError, json.JSONDecodeError):
            return {}


def save_khs_history(data: dict) -> None:
    with _storage_lock:
        try:
            atomic_write(KHS_HISTORY_FILE, json.dumps(data, indent=2, ensure_ascii=False))
        except OSError:
            pass

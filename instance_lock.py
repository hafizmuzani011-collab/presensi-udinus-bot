"""Single-instance lock via PID file + Windows Named Mutex."""

import ctypes
import json
import os
import sys
import psutil
from pathlib import Path

LOCK_FILE = "bot.lock"
OFFSET_FILE = "telegram_offset.json"
_MUTEX_NAME = "PresensiUdinusBot-Lock"


def _check_named_mutex() -> bool:
    """Cek apakah instance lain sudah acquire mutex. Return True kalau sudah ada."""
    try:
        import msvcrt
        # Coba buka mutex existing
        handle = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, _MUTEX_NAME)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        return False


def _create_or_die_mutex() -> bool:
    """Coba buat mutex. Return True kalau sukses (ini instance pertama)."""
    try:
        mutex = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
        err = ctypes.windll.kernel32.GetLastError()
        if mutex and err == 183:  # ERROR_ALREADY_EXISTS
            ctypes.windll.kernel32.CloseHandle(mutex)
            return False
        if not mutex:
            return False
        return True
    except Exception:
        return False


def acquire_lock() -> bool:
    """Cegah multiple instance. Pakai PID file + Named Mutex (atomic)."""
    # Named Mutex adalah single source of truth
    if not _create_or_die_mutex():
        return False

    my_pid = os.getpid()
    tmp = LOCK_FILE + ".tmp"
    try:
        Path(tmp).write_text(str(my_pid))
        os.replace(tmp, LOCK_FILE)
        return True
    except OSError as e:
        return False


def release_lock() -> None:
    """Hapus lock file saat exit."""
    try:
        if os.path.exists(LOCK_FILE):
            old = Path(LOCK_FILE).read_text().strip()
            if old == str(os.getpid()):
                os.remove(LOCK_FILE)
    except OSError:
        pass


def save_offset(offset: int) -> None:
    """Simpan offset update Telegram agar tidak duplicate saat restart."""
    try:
        with open(OFFSET_FILE, "w") as f:
            json.dump({"offset": offset}, f)
    except OSError:
        pass


def load_offset() -> int | None:
    """Load offset tersimpan."""
    if not os.path.exists(OFFSET_FILE):
        return None
    try:
        with open(OFFSET_FILE) as f:
            return int(json.load(f).get("offset"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None

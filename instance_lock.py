"""Single-instance lock via PID file + Windows Named Mutex."""

import ctypes
import os
from pathlib import Path

LOCK_FILE = "bot.lock"
_MUTEX_NAME = "PresensiUdinusBot-Lock"
_mutex_handle = None


def _create_or_die_mutex() -> bool:
    """Coba buat mutex. Return True kalau sukses (ini instance pertama),
    dan simpan handle untuk di-release nanti."""
    global _mutex_handle
    try:
        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
        err = ctypes.windll.kernel32.GetLastError()
        if _mutex_handle and err == 183:  # ERROR_ALREADY_EXISTS
            ctypes.windll.kernel32.CloseHandle(_mutex_handle)
            _mutex_handle = None
            return False
        return _mutex_handle is not None
    except Exception:
        return False


def acquire_lock() -> bool:
    """Cegah multiple instance. Pakai Named Mutex + PID file."""
    if not _create_or_die_mutex():
        return False

    my_pid = os.getpid()
    tmp = LOCK_FILE + ".tmp"
    try:
        Path(tmp).write_text(str(my_pid))
        os.replace(tmp, LOCK_FILE)
        return True
    except OSError:
        return False


def release_lock() -> None:
    """Hapus lock file dan release mutex handle."""
    global _mutex_handle
    try:
        if os.path.exists(LOCK_FILE):
            old = Path(LOCK_FILE).read_text().strip()
            if old == str(os.getpid()):
                os.remove(LOCK_FILE)
    except OSError:
        pass

    if _mutex_handle:
        try:
            ctypes.windll.kernel32.ReleaseMutex(_mutex_handle)
            ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        except Exception:
            pass
        _mutex_handle = None

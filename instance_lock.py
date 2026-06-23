"""Single-instance lock — Windows (Named Mutex) + POSIX (fcntl)."""
import os
import sys
from pathlib import Path

from config import LOCK_FILE

_MUTEX_NAME = "PresensiUdinusBot-Lock"
_mutex_handle = None
_fcntl_fd = None


def _acquire_windows() -> bool:
    """Acquire Windows Named Mutex."""
    global _mutex_handle
    try:
        import ctypes
        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
        err = ctypes.windll.kernel32.GetLastError()
        if _mutex_handle and err == 183:  # ERROR_ALREADY_EXISTS
            ctypes.windll.kernel32.CloseHandle(_mutex_handle)
            _mutex_handle = None
            return False
        return _mutex_handle is not None
    except Exception:
        return False


def _acquire_posix() -> bool:
    """Acquire POSIX flock via fcntl."""
    global _fcntl_fd
    try:
        import fcntl
        _fcntl_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(_fcntl_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (OSError, ImportError):
        if _fcntl_fd is not None:
            try:
                os.close(_fcntl_fd)
            except OSError:
                pass
            _fcntl_fd = None
        return False


def _release_windows() -> None:
    global _mutex_handle
    if _mutex_handle:
        try:
            import ctypes
            ctypes.windll.kernel32.ReleaseMutex(_mutex_handle)
            ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        except Exception:
            pass
        _mutex_handle = None


def _release_posix() -> None:
    global _fcntl_fd
    if _fcntl_fd is not None:
        try:
            import fcntl
            fcntl.flock(_fcntl_fd, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            os.close(_fcntl_fd)
        except OSError:
            pass
        _fcntl_fd = None


def acquire_lock() -> bool:
    """Cegah multiple instance. Cross-platform."""
    if sys.platform == "win32":
        if not _acquire_windows():
            return False
    else:
        if not _acquire_posix():
            return False

    my_pid = os.getpid()
    tmp = LOCK_FILE + ".tmp"
    try:
        Path(tmp).write_text(str(my_pid))
        os.replace(tmp, LOCK_FILE)
        return True
    except OSError:
        # cleanup on failure
        if sys.platform == "win32":
            _release_windows()
        else:
            _release_posix()
        return False


def release_lock() -> None:
    """Hapus lock file dan release mutex/flock."""
    global _mutex_handle, _fcntl_fd
    try:
        if os.path.exists(LOCK_FILE):
            old = Path(LOCK_FILE).read_text().strip()
            if old == str(os.getpid()):
                os.remove(LOCK_FILE)
    except OSError:
        pass

    if sys.platform == "win32":
        _release_windows()
    else:
        _release_posix()

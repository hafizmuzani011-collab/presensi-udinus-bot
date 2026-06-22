"""Single-instance lock & update offset persistence."""

import os
import sys
import json
import psutil
from pathlib import Path

LOCK_FILE = "bot.lock"
OFFSET_FILE = "telegram_offset.json"


def acquire_lock() -> bool:
    """Cegah multiple instance. Kill process lama kalau perlu."""
    if os.path.exists(LOCK_FILE):
        try:
            old_pid = int(Path(LOCK_FILE).read_text().strip())
            if psutil.pid_exists(old_pid):
                try:
                    p = psutil.Process(old_pid)
                    if p.name().lower().startswith("python"):
                        print(f"Bot lama PID {old_pid} masih jalan. Menghentikan...")
                        p.terminate()
                        try:
                            p.wait(timeout=5)
                        except psutil.TimeoutExpired:
                            print(f"PID {old_pid} tidak terminate, kill paksa...")
                            p.kill()
                            p.wait(timeout=3)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except (ValueError, OSError):
            pass
    try:
        Path(LOCK_FILE).write_text(str(os.getpid()))
        return True
    except OSError as e:
        print(f"Gagal tulis lock file: {e}")
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

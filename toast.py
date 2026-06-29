"""Windows desktop toast notifications via win10toast.
Fallback graceful kalau library/modul tidak tersedia.
"""
import logging
import threading

logger = logging.getLogger(__name__)

_AVAILABLE = False
try:
    from win10toast import ToastNotifier
    _AVAILABLE = True
except ImportError:
    logger.info("win10toast not available, desktop notifications disabled")


def send_toast(title: str, message: str, duration: int = 8) -> bool:
    """Show a Windows toast notification. Thread-safe, non-blocking.

    Args:
        title: Notification title
        message: Notification body
        duration: Display duration in seconds (default 8)

    Returns:
        True if notification was sent, False if unavailable
    """
    if not _AVAILABLE:
        return False

    def _show():
        try:
            from win10toast import ToastNotifier
            n = ToastNotifier()
            n.show_toast(title, message, duration=duration, threaded=True)
            logger.info(f"Toast: {title} — {message[:60]}")
        except Exception as e:
            logger.error(f"Toast failed: {e}")

    threading.Thread(target=_show, daemon=True).start()
    return True

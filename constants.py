"""Shared constants - single source of truth for day mappings, magic numbers."""

HARI_ID = {
    "monday": "senin", "tuesday": "selasa", "wednesday": "rabu",
    "thursday": "kamis", "friday": "jumat", "saturday": "sabtu",
    "sunday": "minggu",
}

HARI_MAP = {
    "senin": "senin", "selasa": "selasa", "rabu": "rabu", "kamis": "kamis",
    "jumat": "jumat", "sabtu": "sabtu", "minggu": "minggu",
    "monday": "senin", "tuesday": "selasa", "wednesday": "rabu",
    "thursday": "kamis", "friday": "jumat", "saturday": "sabtu",
    "sunday": "minggu",
    "mon": "senin", "tue": "selasa", "wed": "rabu", "thu": "kamis",
    "fri": "jumat", "sat": "sabtu", "sun": "minggu",
}

KATA_HARI = ["senin", "selasa", "rabu", "kamis", "jumat", "sabtu", "minggu",
             "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

HARI_INDONESIA = {
    "senin": "Senin", "selasa": "Selasa", "rabu": "Rabu",
    "kamis": "Kamis", "jumat": "Jumat", "sabtu": "Sabtu", "minggu": "Minggu",
}

HARI_ORDER = ["senin", "selasa", "rabu", "kamis", "jumat", "sabtu", "minggu"]

BROWSER_SETTLE_MS = 5000
BROWSER_NAV_TIMEOUT = 60000
BROWSER_NETWORK_IDLE_TIMEOUT = 30000
BROWSER_MAX_RETRIES = 3

PROACTIVE_INTERVAL_SECONDS = 30
REMINDER_WINDOW_MINUTES = 30
POLLING_BACKOFF_MAX_SECONDS = 60
SNOOZE_DURATION_SECONDS = 600  # 10 minutes

PRESENSI_SUCCESS_PATTERNS = [
    "berhasil", "sukses", "success", "hadir", "telah melakukan presensi",
    "presensi berhasil", "successfully", "recorded", "tercatat",
]
PRESENSI_FAIL_PATTERNS = [
    "gagal", "failed", "error", "tidak berhasil", "tidak dapat", "denied",
    "tolak", "duplikat", "sudah pernah", "already", "tidak memenuhi",
    "tidak dalam jadwal",
]
PRESENSI_SUCCESS_SELECTOR = ".alert-success, .bg-green-100, [class*='success']"

"""Helper untuk jadwal & deadline tasks."""
import re
from datetime import datetime, timedelta

from config import SCHEDULES_FILE, KULINO_ACCOUNTS, SCREENSHOT_TUGAS
from storage import load_schedules, load_tasks_deadlines, save_tasks_deadlines

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


def get_schedule_for(name: str, hari: str) -> str:
    """Ambil jadwal kuliah untuk hari tertentu ('senin', 'besok', 'hari ini')."""
    schedules = load_schedules()
    if name not in schedules:
        return f"Jadwal {name.title()} tidak ditemukan."

    hari_id = hari.lower().strip()
    if hari_id in ("besok", "tomorrow"):
        target = datetime.now() + timedelta(days=1)
    elif hari_id in ("hari ini", "today", "skrg", "sekarang", "now"):
        target = datetime.now()
    elif hari_id in HARI_MAP:
        target = None
        hari_id = HARI_MAP[hari_id]
    else:
        return f"Hari '{hari}' tidak dikenali."

    if target is not None:
        hari_id = HARI_ID.get(target.strftime("%A").lower(), hari_id)
        tanggal_str = target.strftime("%d-%m-%Y")
    else:
        tanggal_str = datetime.now().strftime("%d-%m-%Y")

    jadwal = schedules[name].get(hari_id, [])
    if not jadwal:
        return f"🎉 {hari_id.title()} ({tanggal_str}) tidak ada jadwal {name.title()}."

    lines = [f"📅 *Jadwal {name.title()} - {hari_id.title()} ({tanggal_str})*\n"]
    for jam, mk, ruang in jadwal:
        lines.append(f"▪️ 🕐 {jam}")
        lines.append(f"   📖 {mk}")
        lines.append(f"   🏫 {ruang}\n")
    return "\n".join(lines)


# ============ Deadline Parser ============
EN_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
ID_MONTHS = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "agustus": 8, "september": 9, "oktober": 10, "november": 11, "desember": 12,
}
ALL_MONTHS = {**EN_MONTHS, **ID_MONTHS}


def parse_deadline(raw_deadline: str, now: datetime) -> str | None:
    """Parse string deadline ke ISO 'YYYY-MM-DDTHH:MM:SS' atau None."""
    if not raw_deadline:
        return None
    text = raw_deadline.strip()
    lower = text.lower()

    # "Tomorrow" / "Today" / "Besok" / "Hari ini"
    if "tomorrow" in lower or "besok" in lower:
        t = now + timedelta(days=1)
        h, m = _extract_time(lower, 23, 59)
        return f"{t.strftime('%Y-%m-%d')}T{h:02d}:{m:02d}:00"
    if "today" in lower or "hari ini" in lower:
        h, m = _extract_time(lower, 23, 59)
        return f"{now.strftime('%Y-%m-%d')}T{h:02d}:{m:02d}:00"

    # "27 Mei 2026 12:30 PM" / "Wednesday, 24 June, 12:30 PM"
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)(?:,?\s+(\d{4}))?(?:[,\s]+(?:pukul\s*)?(\d{1,2})[.:](\d{2})\s*(am|pm)?)?", text, re.I)
    if m:
        day = int(m.group(1))
        month_str = m.group(2).lower()
        month = ALL_MONTHS.get(month_str)
        if not month:
            return None
        year = int(m.group(3)) if m.group(3) else now.year
        if not m.group(3) and datetime(year, month, day) < now - timedelta(days=1):
            year += 1
        jam, menit = 23, 59
        if m.group(4) and m.group(5):
            jam = int(m.group(4))
            menit = int(m.group(5))
            ampm = m.group(6)
            if ampm and ampm.lower() == "pm" and jam < 12:
                jam += 12
            elif ampm and ampm.lower() == "am" and jam == 12:
                jam = 0
        try:
            return datetime(year, month, day, jam, menit).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None

    # ISO "2026-06-17 12:30"
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})[T\s](\d{1,2})[.:](\d{2})", text)
    if m:
        try:
            return datetime(*map(int, m.groups())).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    return None


def _extract_time(text: str, default_h: int, default_m: int) -> tuple[int, int]:
    m = re.search(r"(\d{1,2})[.:](\d{2})\s*(am|pm)?", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        ampm = m.group(3)
        if ampm == "pm" and h < 12: h += 12
        elif ampm == "am" and h == 12: h = 0
        return h, mi
    return default_h, default_m


async def process_and_remind_deadlines(tasks: list[dict], account_key: str, send_message) -> None:
    """Parse deadline dari tugas, simpan ke cache, kirim reminder H-12/H-6."""
    from config import KULINO_ACCOUNTS

    account_name = KULINO_ACCOUNTS[account_key]["name"]
    cache = load_tasks_deadlines()
    now = datetime.now()
    reminded = False

    for task in tasks:
        raw = (task.get("deadline") or "").strip()
        name = (task.get("name") or "").strip()
        course = (task.get("course") or "").strip()
        if not raw or not name:
            continue
        task_key = f"{account_key}:{name}"
        existing = cache.get(task_key)
        if existing and existing.get("deadline_raw") == raw:
            continue
        parsed = parse_deadline(raw, now)
        if not parsed:
            continue
        cache[task_key] = {
            "name": name, "course": course, "account": account_name,
            "deadline_raw": raw, "deadline_iso": parsed,
        }
        save_tasks_deadlines(cache)
        import logging
        logging.getLogger("telegram_bot").info(f"Deadline: {task_key} -> {parsed}")

    for task_key, data in cache.items():
        if task_key == "notified":
            continue
        iso = data.get("deadline_iso")
        if not iso:
            continue
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            continue
        diff = (dt - now).total_seconds()
        if diff <= 0:
            continue
        hours = diff / 3600
        notified = cache.get("notified", {})

        if hours <= 6 and not notified.get(f"{task_key}:h6"):
            msg = (f"🔴 *Deadline Mendekat!* ({int(hours)} jam)\n\n"
                   f"📖 *{data['name']}*\n👤 {data['account']}\n📚 {data['course']}\n"
                   f"📅 {data['deadline_raw']}")
            if await send_message(msg):
                notified[f"{task_key}:h6"] = True
                cache["notified"] = notified
                save_tasks_deadlines(cache)
                reminded = True

        elif hours <= 12 and not notified.get(f"{task_key}:h12"):
            msg = (f"⚠️ *Pengingat Deadline* H-12\n\n"
                   f"📖 *{data['name']}*\n👤 {data['account']}\n📚 {data['course']}\n"
                   f"📅 {data['deadline_raw']}")
            if await send_message(msg):
                notified[f"{task_key}:h12"] = True
                cache["notified"] = notified
                save_tasks_deadlines(cache)
                reminded = True

    if reminded:
        import logging
        logging.getLogger("telegram_bot").info("Deadline reminders sent")

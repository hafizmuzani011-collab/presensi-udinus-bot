"""Helper untuk jadwal & deadline tasks."""
import logging
import re
from datetime import datetime, timedelta
import httpx

from config import KULINO_ACCOUNTS
from constants import HARI_ID, HARI_MAP
from storage import aload_tasks_deadlines, asave_tasks_deadlines

logger = logging.getLogger(__name__)


def get_schedule_for(name: str, hari: str, schedules: dict = None) -> str:
    """Ambil jadwal kuliah untuk hari tertentu ('senin', 'besok', 'hari ini')."""
    if schedules is None:
        import storage
        schedules = storage.load_schedules()
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
        return f"🎉 {hari_id.title()} ({tanggal_str}) Libur!"

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

    # "27 Mei 2026 12:30 PM" / "Wednesday, 24 June, 12:30 PM" / "1 July 2026 at 1:00 PM"
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)(?:,?\s+(\d{4}))?(?:[,\s]+(?:pukul\s*|at\s+)?(\d{1,2})[.:](\d{2})\s*(am|pm)?)?", text, re.I)
    if m:
        day = int(m.group(1))
        month_str = m.group(2).lower()
        month = ALL_MONTHS.get(month_str)
        if not month:
            return None
        year = int(m.group(3)) if m.group(3) else now.year
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
            y, mo, d, h, mi = map(int, m.groups())
            return datetime(y, mo, d, h, mi).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    return None


def _extract_time(text: str, default_h: int, default_m: int) -> tuple[int, int]:
    m = re.search(r"(\d{1,2})[.:](\d{2})\s*(am|pm)?", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        ampm = m.group(3)
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return h, mi
    return default_h, default_m


async def process_and_remind_deadlines(tasks: list[dict], account_key: str, send_message) -> None:
    """Parse deadline dari tugas, simpan ke cache, kirim reminder H-12/H-6."""
    account_name = KULINO_ACCOUNTS[account_key]["name"]
    cache = await aload_tasks_deadlines()
    now = datetime.now()

    cache, dirty = _update_deadlines_cache(cache, tasks, account_key, account_name, now)
    cache, reminded = await _send_due_reminders(cache, account_key, send_message, now)

    if dirty or reminded:
        cache["notified"] = cache.get("notified", {})
        await asave_tasks_deadlines(cache)
        if reminded:
            count = sum(1 for k in cache if k != "notified")
            logger.info(f"Deadline reminders sent | tasks updated: {count}")


def _update_deadlines_cache(
    cache: dict, tasks: list[dict], account_key: str, account_name: str, now: datetime,
) -> tuple[dict, bool]:
    """Insert/update tasks in cache. Return (cache, dirty)."""
    dirty = False
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
        dirty = True
        logger.info(f"Deadline: {task_key} -> {parsed}")
    return cache, dirty


async def _send_due_reminders(
    cache: dict, account_key: str, send_message, now: datetime,
) -> tuple[dict, bool]:
    """Check all deadlines, send h12/h6 reminders if due. Return (cache, reminded)."""
    reminded = False
    notified = cache.get("notified", {})

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

        if hours <= 6 and not notified.get(f"{task_key}:h6"):
            if await _send_h6_reminder(data, int(hours), send_message):
                notified[f"{task_key}:h6"] = True
                reminded = True
        elif 6 < hours <= 12 and not notified.get(f"{task_key}:h12"):
            if await _send_h12_reminder(data, send_message):
                notified[f"{task_key}:h12"] = True
                reminded = True

    cache["notified"] = notified
    return cache, reminded


async def _send_h6_reminder(data: dict, hours_left: int, send_message) -> bool:
    msg = (f"🔴 *Deadline Mendekat!* ({hours_left} jam)\n\n"
           f"📖 *{data['name']}*\n👤 {data['account']}\n📚 {data['course']}\n"
           f"📅 {data['deadline_raw']}")
    if not await send_message(msg):
        return False
    try:
        from tts import text_to_voice, EDGE_TTS_AVAILABLE
        if EDGE_TTS_AVAILABLE:
            voice_text = (f"Hai kak {data['account']}, tugas {data['name']} "
                          f"deadline tinggal {hours_left} jam lagi, jangan lupa dikerjakan ya!")
            await text_to_voice(voice_text)
    except Exception:
        pass
    return True


async def _send_h12_reminder(data: dict, send_message) -> bool:
    msg = (f"⚠️ *Pengingat Deadline* H-12\n\n"
           f"📖 *{data['name']}*\n👤 {data['account']}\n📚 {data['course']}\n"
           f"📅 {data['deadline_raw']}")
    return await send_message(msg)


async def get_weather_info() -> str:
    """Get today's weather forecast for Semarang from Open-Meteo."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": -6.98,
                    "longitude": 110.41,
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,weathercode",
                    "timezone": "Asia/Jakarta",
                    "forecast_days": 1
                }
            )
            resp.raise_for_status()
            data = resp.json().get("daily", {})
            if not data:
                return ""

            t_max = data["temperature_2m_max"][0]
            t_min = data["temperature_2m_min"][0]
            rain_prob = data["precipitation_probability_max"][0]
            rain_sum = data["precipitation_sum"][0]
            wcode = data["weathercode"][0]

            desc = _weather_code_to_desc(wcode)
            emoji = _weather_code_to_emoji(wcode)

            msg = f"{emoji} *Cuaca Hari Ini:*\n"
            msg += f"   Suhu: {t_min:.0f}°C - {t_max:.0f}°C\n"
            msg += f"   Kondisi: {desc}\n"

            if rain_prob > 0:
                msg += f"   Peluang Hujan: {rain_prob}%"
                if rain_sum > 0:
                    msg += f" ({rain_sum:.1f} mm)"
                msg += "\n"

            if rain_prob >= 50:
                msg += "   ⚠️ *Jangan lupa bawa payung!*"
            elif rain_prob >= 20:
                msg += "   🧥 Siap-siap jaket/payung."
            else:
                msg += "   🌞 Cuaca cerah, semangat kuliah!"

            return msg
    except Exception as e:
        logger.warning(f"Gagal fetch cuaca: {e}")
        return ""

def _weather_code_to_desc(code: int) -> str:
    codes = {
        0: "Cerah", 1: "Sebagian Cerah", 2: "Berawan", 3: "Mendung",
        45: "Berkabut", 48: "Berkabut", 51: "Gerimis Ringan", 53: "Gerimis Sedang",
        55: "Gerimis Lebat", 61: "Hujan Ringan", 63: "Hujan Sedang", 65: "Hujan Lebat",
        71: "Salju Ringan", 73: "Salju Sedang", 75: "Salju Lebat",
        80: "Hujan Ringan", 81: "Hujan Sedang", 82: "Hujan Sangat Lebat",
        95: "Badai Petir", 96: "Badai Petir + Hujan Es", 99: "Badai Petir + Hujan Es Lebat",
    }
    return codes.get(code, "Tidak Diketahui")

def _weather_code_to_emoji(code: int) -> str:
    if code == 0:
        return "☀️"
    if code in (1, 2, 3):
        return "⛅"
    if code in (45, 48):
        return "🌫️"
    if code in (51, 53, 55, 61, 63, 65, 80, 81, 82):
        return "🌧️"
    if code in (71, 73, 75):
        return "❄️"
    if code in (95, 96, 99):
        return "⛈️"
    return "🌡️"

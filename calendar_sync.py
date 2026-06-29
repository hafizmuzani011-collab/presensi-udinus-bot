"""Google Calendar iCal Sync module.

Downloads and parses Google Calendar ICS feed to populate schedules.json.
"""
import logging
from datetime import datetime, timedelta, timezone
import httpx
from icalendar import Calendar

from config import GCAL_SAYA_ICAL_URL, GCAL_PACAR_ICAL_URL, SCHEDULES_FILE
from constants import HARI_ID
from storage import load_schedules, _storage_lock
from file_utils import atomic_write_json

logger = logging.getLogger(__name__)


def get_ical_url(account_key: str) -> str:
    if account_key == "saya":
        return GCAL_SAYA_ICAL_URL
    elif account_key == "pacar":
        return GCAL_PACAR_ICAL_URL
    return ""


async def sync_gcal_schedule(account_key: str) -> tuple[bool, str]:
    """Sync schedule from Google Calendar iCal for target account."""
    url = get_ical_url(account_key)
    if not url:
        return False, f"URL iCal untuk '{account_key}' belum diset di .env"

    logger.info(f"Syncing Google Calendar for {account_key}...")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return False, f"Gagal download ICS: HTTP {r.status_code}"
            ics_data = r.content
    except Exception as e:
        logger.error(f"GCal download error: {e}")
        return False, f"Koneksi error: {e}"

    try:
        cal = Calendar.from_ical(ics_data)
    except Exception as e:
        logger.error(f"GCal parse error: {e}")
        return False, f"Format ICS tidak valid: {e}"

    # We scrape schedules for a 7-day window starting today to construct a weekly schedule
    now = datetime.now()
    start_date = now.date()
    end_date = start_date + timedelta(days=7)

    # Initialize empty weekly schedule
    # Note: we need a Set to prevent duplicates in case multiple RRULE instances fall on same day?
    # Actually, a 7-day window covers 1 week. So we should get 1 instance of a weekly recurring event.
    weekly_schedule = {day: [] for day in set(HARI_ID.values())}

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        summary = str(component.get("summary", "Kuliah"))
        location = str(component.get("location", "-"))
        dtstart = component.get("dtstart")
        dtend = component.get("dtend")

        if not dtstart or not dtend:
            continue

        start_val = dtstart.dt
        end_val = dtend.dt

        if not isinstance(start_val, datetime):
            start_dt = datetime.combine(start_val, datetime.min.time())
        else:
            # Convert to WIB first if timezone-aware
            start_dt = start_val.astimezone(tz=timezone(timedelta(hours=7))).replace(tzinfo=None) if start_val.tzinfo else start_val.replace(tzinfo=None)

        if not isinstance(end_val, datetime):
            end_dt = datetime.combine(end_val, datetime.min.time())
        else:
            end_dt = end_val.astimezone(tz=timezone(timedelta(hours=7))).replace(tzinfo=None) if end_val.tzinfo else end_val.replace(tzinfo=None)

        event_date = start_dt.date()
        if start_date <= event_date < end_date:
            day_name = start_dt.strftime("%A").lower()
            hari_id = HARI_ID.get(day_name)
            if not hari_id:
                continue

            jam_mulai = start_dt.strftime("%H:%M")
            jam_selesai = end_dt.strftime("%H:%M")
            time_str = f"{jam_mulai}-{jam_selesai}"

            # Avoid exact duplicates
            slot = [time_str, summary, location]
            if slot not in weekly_schedule[hari_id]:
                weekly_schedule[hari_id].append(slot)

    for day in weekly_schedule:
        weekly_schedule[day].sort(key=lambda x: x[0])

    try:
        schedules = load_schedules()
        existing = schedules.get(account_key, {})
        for day, slots in weekly_schedule.items():
            if slots:
                existing[day] = slots
        schedules[account_key] = existing

        with _storage_lock:
            atomic_write_json(SCHEDULES_FILE, schedules)

        total_classes = sum(len(slots) for slots in weekly_schedule.values())
        logger.info(f"Sync GCal success for {account_key}: {total_classes} classes synced")
        return True, f"Berhasil sinkron {total_classes} kelas dari Google Calendar!"
    except Exception as e:
        logger.error(f"Failed to save synced schedule: {e}")
        return False, f"Gagal simpan jadwal: {e}"

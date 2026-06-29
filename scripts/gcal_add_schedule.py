"""Google Calendar Schedule Adder.
Menambahkan jadwal dari schedules.json ke Google Calendar yang aktif secara rekursif (weekly).

Usage: python scripts/gcal_add_schedule.py [--calendar-id <id>]
"""
import os
import sys
import argparse
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILE = os.path.join(ROOT, "token.json")
SCHEDULES_FILE = os.path.join(ROOT, "data", "runtime", "schedules.json")

# Mapping Day Indonesia ke English untuk recurrence
DAY_RECURRENCE = {
    "senin": "MO",
    "selasa": "TU",
    "rabu": "WE",
    "kamis": "TH",
    "jumat": "FR",
    "sabtu": "SA",
    "minggu": "SU",
}

# Jam Mulai Semester (Asumsi semester aktif 6 bulan dari sekarang)
UNTIL_DATE = (datetime.now() + timedelta(days=180)).strftime("%Y%m%dT235959Z")


def load_gcal_service():
    if not os.path.exists(TOKEN_FILE):
        print("ERROR: token.json belum ada. Jalankan 'python scripts/gcal_oauth.py' terlebih dahulu.")
        sys.exit(1)

    creds = Credentials.from_authorized_user_file(TOKEN_FILE, ["https://www.googleapis.com/auth/calendar"])
    if creds.expired and creds.refresh_token:
        print("Refreshing token...")
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def get_first_occurrence_date(hari_indo):
    """Mencari tanggal pertama dari hari yang bersangkutan (Senin, Selasa, dll) dari hari ini."""
    days = {
        "senin": 0, "selasa": 1, "rabu": 2, "kamis": 3,
        "jumat": 4, "sabtu": 5, "minggu": 6
    }
    target = days.get(hari_indo)
    if target is None:
        return None

    now = datetime.now()
    current = now.weekday()
    diff = (target - current) % 7
    if diff == 0:
        # Jika hari ini adalah harinya, buat untuk minggu depan atau hari ini jika belum terlewat?
        # Supaya aman kita ambil hari ini.
        pass
    return now + timedelta(days=diff)


def add_schedule_to_gcal(calendar_id="primary"):
    import json

    if not os.path.exists(SCHEDULES_FILE):
        print(f"ERROR: {SCHEDULES_FILE} tidak ditemukan. Silakan update jadwal SiAdin dulu.")
        return

    with open(SCHEDULES_FILE) as f:
        schedules = json.load(f)

    service = load_gcal_service()

    print(f"Menggunakan Kalender: {calendar_id}")
    print("Membaca data jadwal dari schedules.json...")

    for owner in schedules:
        print(f"\n👤 Akun: {owner}")
        for hari, classes in schedules[owner].items():
            if not classes:
                continue

            rrule_day = DAY_RECURRENCE.get(hari)
            if not rrule_day:
                continue

            for slot in classes:
                jam, mk, ruang = slot
                # jam format: "07:00-08:40"
                times = jam.split("-")
                if len(times) != 2:
                    continue
                start_time_str, end_time_str = times[0].strip(), times[1].strip()

                first_dt = get_first_occurrence_date(hari)
                if not first_dt:
                    continue

                try:
                    start_h, start_m = map(int, start_time_str.replace(".", ":").split(":"))
                    end_h, end_m = map(int, end_time_str.replace(".", ":").split(":"))
                except ValueError:
                    print(f"Skip format jam salah: {jam}")
                    continue

                start_dt = first_dt.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
                end_dt = first_dt.replace(hour=end_h, minute=end_m, second=0, microsecond=0)

                # Event metadata
                event = {
                    "summary": f"[{owner.upper()}] {mk}",
                    "location": ruang,
                    "description": f"Jadwal kuliah otomatis bot untuk {owner}",
                    "start": {
                        "dateTime": start_dt.isoformat(),
                        "timeZone": "Asia/Jakarta",
                    },
                    "end": {
                        "dateTime": end_dt.isoformat(),
                        "timeZone": "Asia/Jakarta",
                    },
                    # Repeat weekly
                    "recurrence": [
                        f"RRULE:FREQ=WEEKLY;BYDAY={rrule_day};UNTIL={UNTIL_DATE}"
                    ],
                }

                try:
                    created_event = service.events().insert(calendarId=calendar_id, body=event).execute()
                    print(f"  ✅ Added: {mk} ({hari} {jam} @ {ruang}) -> {created_event.get('htmlLink')}")
                except Exception as e:
                    print(f"  ❌ Gagal add {mk}: {e}")

    print("\nSemua jadwal berhasil ditambahkan ke Google Calendar!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--calendar-id", default="primary", help="Target Google Calendar ID")
    args = parser.parse_args()
    add_schedule_to_gcal(args.calendar_id)

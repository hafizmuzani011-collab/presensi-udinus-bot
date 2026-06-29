"""Telegram bot — main polling loop and webhook integration.

Handles bot lifecycle, updates processing, and scheduled tasks like
daily reminders and autopilot checks.
"""
import asyncio
import json
import logging
import logging.handlers
import os
import signal
import sys
import time
from datetime import datetime

import instance_lock
import scrapers as tb
from scrapers import format_khs_message
from browser import close_browser, get_page
from config import (
    ADMIN_CHAT_ID, KULINO_ACCOUNTS, MHS_ACCOUNTS, KULINO_URL, LOG_FILE, STATS_FILE, SCREENSHOT_JADWAL, SCREENSHOT_PRESENSI,
    SCREENSHOT_TUGAS, SCHEDULES_FILE, BOT_TOKEN, inc_stat, save_stats,
    get_control, set_control, consume_control,
)
from constants import (
    BROWSER_NAV_TIMEOUT, BROWSER_NETWORK_IDLE_TIMEOUT, BROWSER_SETTLE_MS,
    HARI_ID, HARI_INDONESIA, POLLING_BACKOFF_MAX_SECONDS, PROACTIVE_INTERVAL_SECONDS,
    SNOOZE_DURATION_SECONDS,
)
from storage import (
    aload_chat_ids, asave_chat_id, aload_offset, asave_offset,
    aload_presensi_done, asave_presensi_done, aload_schedules,
    aload_nilai_cache, asave_nilai_cache, awrite_logbook, diff_nilai,
    aload_khs_history, asave_khs_history, aload_material_cache, asave_material_cache,
    aload_state, asave_state, run_backup,
)
from tg import answer_callback, delete_webhook, get_updates, make_inline_keyboard, send_message, send_photo, send_document
from utils import process_and_remind_deadlines
from calendar_sync import sync_gcal_schedule
from toast import send_toast
import handlers

# Load saved stats
if os.path.exists(STATS_FILE):
    try:
        with open(STATS_FILE, encoding="utf-8") as _f:
            stats_loaded = json.load(_f)
            from config import STATS, STATS_LOCK
            with STATS_LOCK:
                STATS.update(stats_loaded)
    except (OSError, json.JSONDecodeError) as e:
        print(f"WARN: Load stats gagal: {e}")

# Load holidays
HOLIDAYS_FILE = "holidays.json"
HOLIDAY_CACHE: dict[str, str] = {}


def load_holidays() -> dict[str, str]:
    """Load holidays dari file. Return {date_str: summary}."""
    if not os.path.exists(HOLIDAYS_FILE):
        return {}
    try:
        with open(HOLIDAYS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {k: v.get("summary", "?") for k, v in data.get("holidays", {}).items()}
    except Exception:
        return {}


def is_holiday(date_str: str) -> str | None:
    """Return nama hari libur kalau date_str adalah libur nasional."""
    global HOLIDAY_CACHE
    if not HOLIDAY_CACHE:
        HOLIDAY_CACHE = load_holidays()
    return HOLIDAY_CACHE.get(date_str)


def get_today_holiday() -> str | None:
    """Return nama hari libur hari ini (None kalau bukan libur)."""
    return is_holiday(datetime.now().strftime("%Y-%m-%d"))


# Setup logging dengan rotation (max 10MB per file, 5 backups = 50MB total)
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FORMAT_JSON = '{"time":"%(asctime)s","name":"%(name)s","level":"%(levelname)s","msg":"%(message)s"}'

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        ),
        logging.StreamHandler(),
    ],
)

# Also write structured JSON log file for parsing/monitoring
_json_log = logging.handlers.RotatingFileHandler(
    LOG_FILE + ".json", maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_json_log.setFormatter(logging.Formatter(LOG_FORMAT_JSON))
logging.getLogger().addHandler(_json_log)

logger = logging.getLogger(__name__)

# Bot state
ALLOWED_CHAT_ID = None
ALLOWED_CHAT_IDS: set[int] = set()
_polling_backoff = 1.0


def get_autopilot() -> bool:
    """Single source of truth untuk status autopilot presensi."""
    return bool(get_control("autopilot", True))


def set_autopilot(enabled: bool) -> None:
    """Set autopilot dari Telegram command atau dashboard."""
    set_control("autopilot", bool(enabled))
    logger.info(f"Autopilot -> {enabled}")


# ============ Browser Scrapers ============
def _detect_semester() -> str:
    """Generate semester label like '2024/2025 Genap'."""
    now = datetime.now()
    year = now.year
    month = now.month
    if month <= 6:
        return f"{year-1}/{year} Genap"
    return f"{year}/{year+1} Ganjil"


async def _save_khs_history(who: str, khs: dict) -> None:
    """Simpan riwayat IP/IPK per semester."""
    ips = khs.get("ip_semester")
    ipk = khs.get("ipk")
    total_sks = khs.get("total_sks", 0)
    if ips is None and ipk is None:
        return
    history = await aload_khs_history()
    semester = _detect_semester()
    entry = history.setdefault(who, {}).setdefault(semester, {})
    if ips is not None:
        entry["ips"] = ips
    if ipk is not None:
        entry["ipk"] = ipk
    if total_sks:
        entry["total_sks"] = total_sks
    await asave_khs_history(history)
    logger.info(f"KHS history saved: {who} {semester} IP={ips} IPK={ipk}")


async def login_kulino_and_get_tugas(account_key: str) -> list[dict]:
    """Login Kulino, ambil tugas dari Upcoming Events."""
    account = KULINO_ACCOUNTS[account_key]
    await send_message(f"⏳ Menghubungi Kulino {account['name']}...")

    # Hapus screenshot lama sebelum scrape tugas
    if os.path.exists(SCREENSHOT_TUGAS):
        try:
            os.remove(SCREENSHOT_TUGAS)
        except OSError:
            pass

    async with get_page() as page:
        try:
            await page.goto(KULINO_URL, wait_until="domcontentloaded", timeout=BROWSER_NAV_TIMEOUT)
            await page.wait_for_load_state("networkidle", timeout=BROWSER_NETWORK_IDLE_TIMEOUT)
            await page.fill("#inputName", account["nim"])
            await page.fill("#inputPassword", account["password"])
            await page.click("button:has-text('Log in')")
            await page.wait_for_timeout(BROWSER_SETTLE_MS)

            login_form = await page.query_selector("#inputName")

            # Ambil screenshot dulu sebagai bukti (walaupun gagal login)
            try:
                await page.screenshot(path=SCREENSHOT_TUGAS, full_page=True)
            except Exception:
                pass

            if login_form:
                logger.warning("Login Kulino gagal: form login masih muncul")
                return []

            tugas = await tb.scrape_kulino_tugas(page, account)
            return tugas
        except Exception as e:
            logger.error(f"Error Kulino: {e}")
            # Ambil screenshot error sebagai bukti
            try:
                await page.screenshot(path=SCREENSHOT_TUGAS, full_page=True)
            except Exception:
                pass
            return []


async def do_presensi_siadin(account_key: str) -> tuple[bool, str]:
    """Login MHS & klik presensi dengan auto-retry."""
    account = MHS_ACCOUNTS[account_key]
    max_retries = 3
    last_msg = ""

    # Hapus screenshot lama sebelum presensi
    if os.path.exists(SCREENSHOT_PRESENSI):
        try:
            os.remove(SCREENSHOT_PRESENSI)
        except OSError:
            pass

    for attempt in range(1, max_retries + 1):
        if attempt == 1:
            await send_message(f"🤖 Autopilot {account['name']} - presensi dimulai...")
        else:
            await send_message(f"🔄 Retry presensi {account['name']} (Percobaan {attempt}/{max_retries})...")

        async with get_page() as page:
            try:
                ok, msg = await tb.scrape_siadin_presensi(page, account)
                if ok:
                    return True, msg
                else:
                    logger.warning(f"Presensi {account_key} gagal pada percobaan {attempt}: {msg}")
                    last_msg = msg
            except Exception as e:
                logger.error(f"Error presensi {account_key} (percobaan {attempt}): {e}")
                last_msg = str(e)

        if attempt < max_retries:
            await asyncio.sleep(5)  # Backoff sebentar sebelum retry

    return False, f"Gagal setelah {max_retries} percobaan. Error terakhir: {last_msg}"


async def update_schedules_from_mhs() -> tuple[bool, str]:
    """Sinkron jadwal dari MHS untuk kedua akun."""
    new_schedules = {"saya": {}, "pacar": {}}
    for key, akun in MHS_ACCOUNTS.items():
        try:
            async with get_page() as page:
                jadwal = await tb.login_mhs_and_scrape_jadwal(page, akun)
                new_schedules[key] = jadwal
        except Exception as e:
            logger.error(f"Gagal scrape {key}: {e}")
            new_schedules[key] = {}

    with open(SCHEDULES_FILE, "w", encoding="utf-8") as f:
        json.dump(new_schedules, f, indent=2, ensure_ascii=False)
    total = sum(sum(len(v) for v in s.values()) for s in new_schedules.values())
    return True, f"Jadwal diperbarui: {total} slot"


# ============ Proactive State ============
_tugas_checked_today: str | None = None
_morning_reminder_date: str | None = None
_presensi_done: set[str] = set()
_presensi_done_date: str = ""
_presensi_done_lock = asyncio.Lock()
_reminder_sent: set[str] = set()
# snoozed reminders: {reminder_key: expire_timestamp}
_snoozed_reminders: dict[str, float] = {}


async def _load_presensi_done_for_today(today_str: str) -> set:
    """Load presensi_done keys. Reset kalau tanggal ganti (hari baru)."""
    global _presensi_done, _presensi_done_date
    data = await aload_presensi_done()
    if data.get("date") != today_str:
        _presensi_done = set()
        _presensi_done_date = today_str
    else:
        _presensi_done = set(data.get("keys", []))
        _presensi_done_date = today_str
    return _presensi_done


async def _save_presensi_done(today_str: str) -> None:
    """Persist ke file."""
    await asave_presensi_done(today_str, _presensi_done)


# ============ Proactive Loop Helpers ============
async def _handle_dashboard_triggers() -> None:
    trigger_tugas_count = int(consume_control("trigger_tugas", 0) or 0)
    if trigger_tugas_count > 0:
        send_toast("🔍 Cek Tugas", f"Triggered dari dashboard... ({trigger_tugas_count}x)")
        await send_message(f"🔔 Trigger dari dashboard: cek tugas ({trigger_tugas_count}x)...")
        for key in KULINO_ACCOUNTS:
            nama = KULINO_ACCOUNTS[key]["name"]
            await send_message(f"⏳ Menghubungi Kulino {nama}...")
            tugas = await login_kulino_and_get_tugas(key)
            if tugas:
                rows = [f"{'No':<4} {'Tugas':<40} {'Deadline':<20}"]
                rows.append("-" * 70)
                for i, t in enumerate(tugas, 1):
                    rows.append(f"{i:<4} {t.get('name','?')[:38]:<40} {t.get('deadline','?')[:18]:<20}")
                await send_message(f"📝 *Tugas {nama}*\n```\n" + "\n".join(rows) + "\n```")
                if os.path.exists(SCREENSHOT_TUGAS):
                    await send_photo(SCREENSHOT_TUGAS)
                await process_and_remind_deadlines(tugas, key, send_message)
            else:
                await send_message(f"📭 Tidak ada tugas aktif untuk {nama}.")
                if os.path.exists(SCREENSHOT_TUGAS):
                    await send_photo(SCREENSHOT_TUGAS)

    trigger_presensi_who = consume_control("trigger_presensi", "")
    if trigger_presensi_who in MHS_ACCOUNTS:
        nama = MHS_ACCOUNTS[trigger_presensi_who]["name"]
        send_toast("👤 Presensi", f"Triggered manual presensi {nama} dari dashboard...")
        await send_message(f"🔔 Trigger dari dashboard: presensi {nama}...")
        ok, msg = await do_presensi_siadin(trigger_presensi_who)
        if ok:
            await send_message(f"✅ Presensi {nama} berhasil!")
            await send_photo(SCREENSHOT_PRESENSI)
        else:
            await send_message(f"⚠️ Presensi {nama} gagal: {msg}")


async def _auto_sync_schedules(day_name: str, hour: int, minute: int) -> None:
    if day_name == "sunday" and hour == 22 and minute < 2:
        logger.info("Auto-sync jadwal mingguan...")
        ok, msg = await update_schedules_from_mhs()
        logger.info(f"Auto-sync jadwal MHS: {msg}")
        await send_message(f"🔄 Auto-sync jadwal mingguan (MHS):\n{msg}")

        # Auto-sync from Google Calendar
        for who in MHS_ACCOUNTS:
            gcal_ok, gcal_msg = await sync_gcal_schedule(who)
            if gcal_ok:
                logger.info(f"Auto-sync GCal {who}: {gcal_msg}")
                await send_message(f"🔄 Auto-sync Google Calendar {MHS_ACCOUNTS[who]['name']}:\n{gcal_msg}")


async def _check_daily_tugas(hour: int, minute: int, today_str: str) -> None:
    global _tugas_checked_today
    if hour == 17 and minute < 2 and _tugas_checked_today != today_str:
        _tugas_checked_today = today_str
        send_toast("📝 Cek Tugas", "17:00 - Cek tugas otomatis...")
        await send_message("⏰ 17:00 - Cek tugas otomatis...")
        for key in KULINO_ACCOUNTS:
            tugas = await login_kulino_and_get_tugas(key)
            if tugas:
                lines = [f"📝 *Tugas {KULINO_ACCOUNTS[key]['name']}*\n"]
                for i, t in enumerate(tugas, 1):
                    lines.append(f"{i}. {t.get('name','?')} - {t.get('deadline','?')}")
                await send_message("\n".join(lines))
            await process_and_remind_deadlines(tugas, key, send_message)


async def _check_deadlines(minute: int) -> None:
    if minute % 30 == 0:
        for key in KULINO_ACCOUNTS:
            await process_and_remind_deadlines([], key, send_message)


async def _auto_backup(minute: int) -> None:
    if minute == 0:
        run_backup()
        save_stats()


async def _check_class_reminders(hour: int, minute: int, hari_id: str) -> None:
    global _reminder_sent
    if not hari_id:
        return
    schedules = await aload_schedules()
    now_min = hour * 60 + minute
    for who in MHS_ACCOUNTS:
        today_sched = schedules.get(who, {}).get(hari_id, [])
        for jam, mk, ruang in today_sched:
            parts = jam.split("-")
            if len(parts) != 2:
                continue
            jam_mulai = parts[0].strip().replace(".", ":")
            try:
                h, m = map(int, jam_mulai.split(":"))
            except ValueError:
                continue
            start_min = h * 60 + m
            if start_min - 30 == now_min:
                reminder_key = f"reminder:{who}:{hari_id}:{jam_mulai}"
                if reminder_key in _reminder_sent:
                    continue
                _reminder_sent.add(reminder_key)
                await asave_state({
                    "reminder_sent": list(_reminder_sent),
                    "snoozed": _snoozed_reminders,
                    "morning_reminder_date": _morning_reminder_date,
                })
                nama = MHS_ACCOUNTS[who]["name"]
                send_toast(f"⏰ {mk} ({ruang})", f"Kelas dimulai 30 menit lagi — {nama}")
                buttons = [
                    [{"text": "✅ Presensi", "callback_data": "presensi:hadir:" + who}],
                    [{"text": "⏰ Snooze 10m", "callback_data": f"snooze:{who}:{hari_id}:{jam_mulai}"}],
                ]
                kb = make_inline_keyboard(buttons)
                await send_message(
                    f"⏰ *Reminder 30 menit*\n\n"
                    f"📖 {mk}\n"
                    f"🕐 {jam}\n"
                    f"🏫 Ruang {ruang}\n"
                    f"👤 {nama}\n\n"
                    f"_Jangan lupa presensi ya!_",
                    reply_markup=kb,
                )


async def _check_snoozed_reminders(hari_id: str) -> None:
    """Fire ulang reminder yang di-snooze dan sudah expire."""
    global _snoozed_reminders
    if not _snoozed_reminders or not hari_id:
        return
    now_ts = time.time()
    schedules = await aload_schedules()
    expired = [k for k, exp in _snoozed_reminders.items() if exp <= now_ts]
    for key in expired:
        # key format: "snoozed:{who}:{hari_id}:{jam_mulai}"
        parts = key.split(":", 3)
        if len(parts) != 4:
            _snoozed_reminders.pop(key, None)
            continue
        _, who, key_hari, jam_mulai = parts
        if who not in MHS_ACCOUNTS or key_hari != hari_id:
            _snoozed_reminders.pop(key, None)
            continue
        # Find matching schedule
        today_sched = schedules.get(who, {}).get(hari_id, [])
        match = next((s for s in today_sched if s[0].split("-")[0].strip().replace(".", ":") == jam_mulai), None)
        if not match:
            _snoozed_reminders.pop(key, None)
            continue
        _, mk, ruang = match
        nama = MHS_ACCOUNTS[who]["name"]
        buttons = [
            [{"text": "✅ Presensi", "callback_data": "presensi:hadir:" + who}],
            [{"text": "⏰ Snooze 10m", "callback_data": f"snooze:{who}:{hari_id}:{jam_mulai}"}],
        ]
        kb = make_inline_keyboard(buttons)
        await send_message(
            f"⏰ *Snooze selesai*\n\n"
            f"📖 {mk}\n"
            f"🕐 {jam_mulai}\n"
            f"🏫 Ruang {ruang}\n"
            f"👤 {nama}\n\n"
            f"_10 menit sudah lewat, jangan lupa presensi!_",
            reply_markup=kb,
        )
        _snoozed_reminders.pop(key, None)
        logger.info(f"Snoozed reminder fired: {key}")


async def _check_nilai_update(minute: int) -> None:
    """Cek nilai baru setiap 30 menit (minute 0 & 30)."""
    if minute not in (0, 30):
        return
    cache = await aload_nilai_cache()
    if cache is None:
        cache = {}

    for who in MHS_ACCOUNTS:
        cached_courses = cache.get(who, {})
        is_initial = not bool(cached_courses)

        try:
            async with get_page() as page:
                account = MHS_ACCOUNTS[who]
                await page.goto("https://mhs.dinus.ac.id/", wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2000)
                await page.fill("#username", account["nim"])
                await page.fill("#password", account["password"])
                async with page.expect_navigation(timeout=30000, wait_until="networkidle"):
                    await page.click("button:has-text('Masuk ke SiAdin')")
                khs = await tb.scrape_khs(page, account)
            new_map = {m["kdmk"]: m for m in khs["matkul"]}
            await _save_khs_history(who, khs)

            if not is_initial:
                diff = diff_nilai({who: cached_courses}, {who: new_map})
                if diff:
                    send_toast("🔔 Nilai Baru", f"Cek SiAdin {account['name']} — Ada update nilai")
                    lines = [f"🔔 *Nilai Baru!* ({account['name']})"]
                    for d in diff:
                        lines.append(f"  • {d['matkul']}: {d['old']} → *{d['new']}*")
                    if khs.get("ip_semester") is not None:
                        lines.append(f"\n  📊 IP: {khs['ip_semester']}")
                    await send_message("\n".join(lines))
                    await send_message(format_khs_message(khs, account["name"]))

            cache[who] = new_map
            await asave_nilai_cache(cache)
        except Exception as e:
            logger.error(f"Nilai check {who}: {e}")


async def _check_attendance_alerts(hour: int, minute: int, hari_id: str) -> None:
    """Kirim warning jika ada matkul dengan presensi < 75%. (tiap jam 20:00)."""
    if hour != 20 or minute >= 2 or not hari_id:
        return
    from storage import compute_attendance, attendance_alert

    now = datetime.now()
    for who in MHS_ACCOUNTS:
        schedules = await aload_schedules()
        results = compute_attendance(schedules, who, now.year, now.month)
        warnings = attendance_alert(results)
        if warnings:
            msg = "\U000026A0 *Peringatan Kehadiran* ({})\n".format(MHS_ACCOUNTS[who]["name"])
            msg += "\n".join(f"  \u2022 {w}" for w in warnings)
            await send_message(msg)


async def _run_autopilot_presensi(
    now: datetime, hour: int, minute: int, hari_id: str, today_holiday: str | None, today_str: str,
) -> None:
    global _presensi_done
    autopilot_on = get_autopilot()
    if not (autopilot_on and hari_id and not today_holiday):
        return
    schedules = await aload_schedules()
    for who in MHS_ACCOUNTS:
        today_sched = schedules.get(who, {}).get(hari_id, [])
        for jam, mk, ruang in today_sched:
            parts = jam.split("-")
            if len(parts) != 2:
                continue
            jam_mulai = parts[0].strip().replace(".", ":")
            jam_selesai = parts[1].strip().replace(".", ":")
            try:
                h, m = map(int, jam_mulai.split(":"))
                h_end, m_end = map(int, jam_selesai.split(":"))
            except ValueError:
                continue
            sesi_key = f"{who}:{hari_id}:{jam_mulai}"
            if sesi_key in _presensi_done:
                continue
            now_min = hour * 60 + minute
            start_min = h * 60 + m
            end_min = h_end * 60 + m_end
            if start_min <= now_min < end_min:
                async with _presensi_done_lock:
                    _presensi_done.add(sesi_key)
                    await _save_presensi_done(today_str)
                # Clear any snoozed reminders for this sesi
                snooze_key = f"snoozed:{who}:{hari_id}:{jam_mulai}"
                _snoozed_reminders.pop(snooze_key, None)
                logger.info(f"Presensi: {who} - {mk}")
                await send_message(f"🤖 Presensi {MHS_ACCOUNTS[who]['name']} - {mk} {jam}")
                ok, msg = await do_presensi_siadin(who)
                if ok:
                    send_toast(f"✅ Presensi {MHS_ACCOUNTS[who]['name']}", f"{mk} {jam} — {ruang}")
                    await send_message(
                        f"✅ Presensi {MHS_ACCOUNTS[who]['name']} berhasil!"
                        f"\n📖 {mk}\n🕐 {jam}\n🏫 {ruang}"
                    )
                    await send_photo(SCREENSHOT_PRESENSI)
                    try:
                        await awrite_logbook(now.strftime("%Y-%m-%d"), who, jam, mk, ruang, "hadir")
                    except Exception as e:
                        logger.error(f"Logbook error: {e}")
                else:
                    send_toast(f"⚠️ Presensi {MHS_ACCOUNTS[who]['name']} Gagal", f"{mk} — {msg}")
                    await send_message(f"⚠️ Presensi {MHS_ACCOUNTS[who]['name']} gagal: {msg}")


async def _check_morning_reminder(
    hour: int, minute: int, hari_id: str, today_holiday: str | None, today_str: str,
) -> None:
    """Kirim reminder pagi jam 07:00 dengan jadwal hari ini + screenshot."""
    global _morning_reminder_date
    if hour != 7 or minute >= 2 or _morning_reminder_date == today_str:
        return
    if not hari_id or today_holiday:
        return
    schedules = await aload_schedules()
    total = sum(len(schedules.get(w, {}).get(hari_id, [])) for w in MHS_ACCOUNTS.keys())
    if total == 0:
        return

    _morning_reminder_date = today_str
    send_toast("☀️ Selamat Pagi", f"Hari ini ada {total} kelas.")

    from utils import get_weather_info
    weather_text = await get_weather_info()

    header = (
        "☀️ *Selamat pagi!*\n\n"
    )
    if weather_text:
        header += f"{weather_text}\n"

    header += f"📅 Jadwal hari ini ({HARI_INDONESIA.get(hari_id, hari_id)}):\n"
    for who in MHS_ACCOUNTS:
        nama = MHS_ACCOUNTS[who]["name"]
        slots = schedules.get(who, {}).get(hari_id, [])
        if not slots:
            continue
        header += f"\n👤 *{nama}* ({len(slots)} kelas):\n"
        for jam, mk, ruang in slots:
            header += f"  🕐 {jam} — {mk} ({ruang})\n"
    header += "\n_Autopilot akan jalan otomatis. Selamat kuliah!_"
    await send_message(header)

    # Kirim screenshot jadwal
    from render import get_today_jadwal_png
    try:
        if await get_today_jadwal_png(schedules, SCREENSHOT_JADWAL) and os.path.exists(SCREENSHOT_JADWAL):
            await send_photo(SCREENSHOT_JADWAL)
    except Exception as e:
        logger.error(f"Morning reminder screenshot gagal: {e}")


async def proactive_check() -> None:
    _proactive_backoff = 1.0
    while True:
        try:
            _proactive_backoff = 1.0
            now = datetime.now()
            hour, minute = now.hour, now.minute
            today_str = now.strftime("%Y-%m-%d")
            day_name = now.strftime("%A").lower()
            hari_id = HARI_ID.get(day_name, "")

            await _load_presensi_done_for_today(today_str)

            await _handle_dashboard_triggers()
            await _auto_sync_schedules(day_name, hour, minute)
            await _check_daily_tugas(hour, minute, today_str)
            await _check_deadlines(minute)
            await _auto_backup(minute)
            await _check_class_reminders(hour, minute, hari_id)
            await _check_snoozed_reminders(hari_id)
            await _check_nilai_update(minute)
            await _check_attendance_alerts(hour, minute, hari_id)

            # Cek materi tiap jam (pada menit ke-15)
            if minute == 15 and hari_id:
                for target in KULINO_ACCOUNTS:
                    await check_materials_for(target, silent=True)

            today_holiday = get_today_holiday()
            await _check_morning_reminder(hour, minute, hari_id, today_holiday, today_str)
            await _run_autopilot_presensi(now, hour, minute, hari_id, today_holiday, today_str)

            # Persist volatile state
            await asave_state({
                "reminder_sent": list(_reminder_sent),
                "snoozed": _snoozed_reminders,
                "morning_reminder_date": _morning_reminder_date,
            })

            await asyncio.sleep(PROACTIVE_INTERVAL_SECONDS)
        except Exception as e:
            inc_stat("errors")
            logger.error(f"Proactive error: {e}")
            sleep_s = min(_proactive_backoff, 300)
            logger.warning(f"Proactive backoff: sleeping {sleep_s:.0f}s")
            await asyncio.sleep(sleep_s)
            _proactive_backoff = min(_proactive_backoff * 2, 300)


# ============ Materi Kulino ============
async def check_materials_for(account_key: str, course_query: str = "", silent: bool = False) -> None:
    from scrapers.kulino import check_new_materials
    account = KULINO_ACCOUNTS[account_key]

    if not silent:
        if course_query:
            await send_message(f"⏳ Cek materi *{course_query}* untuk {account['name']}...")
        else:
            await send_message(f"⏳ Cek semua materi Kulino {account['name']}...")

    cache = await aload_material_cache()
    async with get_page(".browser_state/kulino_" + account_key + ".json") as page:
        try:
            await page.goto(KULINO_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            if await page.query_selector("#inputName"):
                await page.fill("#inputName", account["nim"])
                await page.fill("#inputPassword", account["password"])
                await page.click("button:has-text('Log in')")
                await page.wait_for_timeout(3000)
            else:
                logger.info(f"Kulino: already logged in for {account['nim']}")

            new_files, found_courses = await check_new_materials(page, account, cache, course_query)
            await asave_material_cache(cache)

            if course_query and not found_courses:
                if not silent:
                    await send_message(f"❌ Mata kuliah \"{course_query}\" tidak ditemukan untuk {account['name']}.")
                return

            if not new_files:
                if not silent:
                    if course_query:
                        await send_message(f"📭 Tidak ada materi baru di *{course_query}* ({account['name']}).")
                    else:
                        await send_message(f"📭 Tidak ada materi baru untuk {account['name']}.")
                return

            await send_message(f"📚 *Materi Baru ({account['name']})*\nAda {len(new_files)} file baru.")

            for f in new_files:
                caption = f"📖 *{f['course_name']}*\n📄 {f['name']}"
                if "local_path" in f and os.path.exists(f["local_path"]):
                    ok = await send_document(f["local_path"], caption=caption)
                    if ok:
                        os.remove(f["local_path"])
                else:
                    await send_message(caption + f"\n🔗 Link: {f['file_url']}")

        except Exception as e:
            logger.error(f"Error check materials {account_key}: {e}")
            await send_message(f"❌ Gagal cek materi: {e}")


# handle_command is now imported from handlers; register callbacks in main()
# Re-export for backward compatibility with tests
handle_command = handlers.handle_command
# ============ Main ============
async def main() -> None:
    global ALLOWED_CHAT_ID, ALLOWED_CHAT_IDS, _polling_backoff

    if not BOT_TOKEN:
        sys.exit(1)

    if not instance_lock.acquire_lock():
        logger.error("Instance lain sedang berjalan")
        sys.exit(1)

    await delete_webhook()

    # Restore volatile state
    state = await aload_state()
    global _reminder_sent, _snoozed_reminders, _morning_reminder_date
    _reminder_sent = set(state.get("reminder_sent", []))
    _snoozed_reminders = state.get("snoozed", {})
    _morning_reminder_date = state.get("morning_reminder_date")

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _signal_handler():
        logger.info("Shutdown signal received")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    # Load chat IDs
    from config import ALLOWED_CHAT_IDS as _CFG_IDS

    loaded = await aload_chat_ids()
    # Bootstrap with ADMIN_CHAT_ID if set
    if ADMIN_CHAT_ID and ADMIN_CHAT_ID not in loaded:
        loaded.append(ADMIN_CHAT_ID)
        await asave_chat_id(ADMIN_CHAT_ID)

    ALLOWED_CHAT_IDS = set(loaded)
    _CFG_IDS.clear()
    _CFG_IDS.update(loaded)
    ALLOWED_CHAT_ID = next(iter(_CFG_IDS), None)
    logger.info(f"Chat IDs: {ALLOWED_CHAT_IDS}" if ALLOWED_CHAT_IDS else "Tunggu /addchid dari admin...")

    handlers.register_callbacks(
        get_autopilot_fn=get_autopilot,
        set_autopilot_fn=set_autopilot,
        do_presensi_fn=do_presensi_siadin,
        login_kulino_fn=login_kulino_and_get_tugas,
        update_schedules_fn=update_schedules_from_mhs,
        check_materials_fn=check_materials_for,
        syncgcal_fn=sync_gcal_schedule,
    )
    logger.info("Handler callbacks registered")

    asyncio.create_task(proactive_check())
    logger.info("Proactive loop started")

    # Sync GCal on startup in background
    for target_who in MHS_ACCOUNTS:
        asyncio.create_task(sync_gcal_schedule(target_who))

    if os.environ.get("DASHBOARD_DISABLE", "0") != "1":
        try:
            from web_dashboard import run_in_thread

            run_in_thread(port=8787)
            logger.info("Web dashboard started at http://localhost:8787")
        except Exception as e:
            logger.error(f"Web dashboard gagal: {e}")
    else:
        logger.info("Web dashboard DISABLED")

    offset = await aload_offset()
    try:
        while True:
            if shutdown_event.is_set():
                logger.info("Shutting down polling loop")
                break
            try:
                updates = await get_updates(offset)
                _polling_backoff = 1.0
                for update in updates:
                    offset = update["update_id"] + 1
                    await asave_offset(offset)

                    if "callback_query" in update:
                        cb = update["callback_query"]
                        cb_id = cb.get("id")
                        cb_data = cb.get("data", "")
                        cb_chat_id = cb.get("from", {}).get("id")
                        if cb_chat_id and cb_chat_id not in ALLOWED_CHAT_IDS:
                            continue
                        await answer_callback(cb_id, "⏳ Memproses...")
                        if cb_data.startswith("snooze:"):
                            parts = cb_data.split(":")
                            if len(parts) >= 4:
                                who, key_hari, jam_mulai = parts[1], parts[2], parts[3]
                                if who not in MHS_ACCOUNTS:
                                    await answer_callback(cb_id, "❌ Akun tidak dikenal")
                                    continue
                                key = f"snoozed:{who}:{key_hari}:{jam_mulai}"
                                if len(cb_data.encode()) > 64:
                                    await answer_callback(cb_id, "❌ Data terlalu panjang")
                                    continue
                                _snoozed_reminders[key] = time.time() + SNOOZE_DURATION_SECONDS
                                await asave_state({
                                    "reminder_sent": list(_reminder_sent),
                                    "snoozed": _snoozed_reminders,
                                    "morning_reminder_date": _morning_reminder_date,
                                })
                                logger.info(f"Snooze set: {key} until {_snoozed_reminders[key]:.0f}")
                                nama = MHS_ACCOUNTS[who]["name"]
                                await answer_callback(cb_id, f"⏰ Akan diingatkan 10 menit lagi ({nama})")
                            else:
                                await answer_callback(cb_id, "❌ Format snooze salah")
                        elif cb_data.startswith("presensi:hadir:"):
                            who = cb_data.split(":")[-1]
                            if who in MHS_ACCOUNTS:
                                # Clear any snoozed reminders for this akun
                                keys_to_clear = [k for k in _snoozed_reminders if k.startswith(f"snoozed:{who}:")]
                                for k in keys_to_clear:
                                    _snoozed_reminders.pop(k, None)
                                await send_message(f"⏳ Presensi {MHS_ACCOUNTS[who]['name']}...")
                                ok, msg = await do_presensi_siadin(who)
                                if ok:
                                    inc_stat("presensi_done")
                                    await send_message(f"✅ Presensi {MHS_ACCOUNTS[who]['name']} berhasil!")
                                    await send_photo(SCREENSHOT_PRESENSI)
                                else:
                                    await send_message(f"⚠️ Presensi gagal: {msg}")
                        elif cb_data.startswith("cmd:"):
                            from tg import edit_message
                            cmd = cb_data.split(":", 1)[-1]
                            await edit_message(cb_chat_id, cb.get("message", {}).get("message_id"), f"⏳ Menjalankan /{cmd}...")
                            await handlers.handle_command(f"/{cmd}", cb_chat_id)
                        continue

                    msg = update.get("message", {})
                    chat = msg.get("chat", {})
                    chat_id = chat.get("id")
                    text = msg.get("text", "")

                    if not text:
                        continue
                    if not ALLOWED_CHAT_IDS:
                        if ADMIN_CHAT_ID and chat_id == ADMIN_CHAT_ID:
                            await asave_chat_id(chat_id)
                            ALLOWED_CHAT_IDS = {chat_id}
                            ALLOWED_CHAT_ID = chat_id
                            from config import ALLOWED_CHAT_IDS as __C

                            __C.clear()
                            __C.add(chat_id)
                        else:
                            logger.info(f"Ditolak: {chat_id} (belum terdaftar, ADMIN_CHAT_ID={ADMIN_CHAT_ID})")
                            continue
                    if chat_id not in ALLOWED_CHAT_IDS:
                        continue

                    inc_stat("messages_received")
                    # Sanitize sensitive info in logs (no passwords in register messages)
                    log_text = text if not text.lower().startswith("/register") else text.split()[0]
                    logger.info(f"{chat_id}: {log_text}")
                    await handlers.handle_command(text, chat_id)
            except Exception as e:
                inc_stat("errors")
                logger.error(f"Polling error: {e}")
                await asyncio.sleep(min(_polling_backoff, POLLING_BACKOFF_MAX_SECONDS))
                _polling_backoff = min(_polling_backoff * 2, POLLING_BACKOFF_MAX_SECONDS)
    finally:
        save_stats()
        instance_lock.release_lock()
        await close_browser()
        from tg import close_client

        await close_client()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        instance_lock.release_lock()
        asyncio.run(close_browser())
        from tg import close_client

        asyncio.run(close_client())

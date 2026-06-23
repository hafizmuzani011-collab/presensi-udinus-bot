"""Presensi Udinus Bot - Main entry point.
Dependencies: config.py, storage.py, utils.py, tg.py, telegram_bot.py, instance_lock.py
"""
import asyncio
import json
import logging
import logging.handlers
import os
import signal
import sys
import time
from datetime import datetime, timedelta

import instance_lock
import telegram_bot as tb
from telegram_bot import format_attendance_message, format_khs_message
from aliases import add_alias, remove_alias, resolve_alias
from browser import close_browser, get_page
from config import (
    ADMIN_CHAT_ID, KULINO_ACCOUNTS, MHS_ACCOUNTS, KULINO_URL, LOG_FILE, LOG_DIR,
    NAMA_PACAR, NAMA_SAYA, STATS_FILE, SCREENSHOT_JADWAL, SCREENSHOT_PRESENSI,
    SCREENSHOT_TUGAS, SCHEDULES_FILE, BOT_TOKEN, ALLOWED_CHAT_IDS,
    get_stats_snapshot, save_stats, inc_stat,
)
from constants import (
    BROWSER_NAV_TIMEOUT, BROWSER_NETWORK_IDLE_TIMEOUT, BROWSER_SETTLE_MS,
    HARI_ID, HARI_INDONESIA, POLLING_BACKOFF_MAX_SECONDS, PROACTIVE_INTERVAL_SECONDS,
    SNOOZE_DURATION_SECONDS,
)
from storage import (
    cleanup_expired_deadlines, load_chat_ids, load_nilai_cache, load_offset, load_presensi_done,
    load_schedules, load_tasks_deadlines, run_backup, save_chat_id, save_nilai_cache,
    save_offset, save_presensi_done, save_tasks_deadlines, write_logbook, diff_nilai,
    load_khs_history, save_khs_history,
)
from tg import answer_callback, get_updates, make_inline_keyboard, send_message, send_photo, send_document
from utils import get_schedule_for, process_and_remind_deadlines
from storage import load_material_cache, save_material_cache

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


# Dashboard control (shared with web dashboard) - circular safe
# Use fallbacks if web_dashboard can't be imported (e.g. import error).
DASH_CONTROL: dict = {"autopilot": True, "trigger_tugas": 0, "trigger_presensi": 0}


def get_control(key, default=None):
    return DASH_CONTROL.get(key, default)


def set_control(key, value):
    DASH_CONTROL[key] = value


def consume_control(key, default=None):
    val = DASH_CONTROL.get(key, default)
    DASH_CONTROL[key] = default if default is not None else ""
    return val


try:
    from web_dashboard import (  # type: ignore[import-not-found]
        CONTROL as _REAL_CONTROL,
        get_control as _real_get_control,
    )
    from web_dashboard import consume_control as _real_consume_control  # type: ignore[import-not-found]
    DASH_CONTROL = _REAL_CONTROL
    get_control = _real_get_control  # type: ignore[assignment]
    consume_control = _real_consume_control  # type: ignore[assignment]
except Exception:
    pass

# Setup logging dengan rotation (max 10MB per file, 5 backups = 50MB total)
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# Bot state
ALLOWED_CHAT_ID = None
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
        return f"{year-1}/{year} Genap" if month >= 2 else f"{year-1}/{year} Ganjil"
    return f"{year}/{year+1} Ganjil"


def _save_khs_history(who: str, khs: dict) -> None:
    """Simpan riwayat IP/IPK per semester."""
    ips = khs.get("ip_semester")
    ipk = khs.get("ipk")
    total_sks = khs.get("total_sks", 0)
    if ips is None and ipk is None:
        return
    history = load_khs_history()
    semester = _detect_semester()
    entry = history.setdefault(who, {}).setdefault(semester, {})
    if ips is not None:
        entry["ips"] = ips
    if ipk is not None:
        entry["ipk"] = ipk
    if total_sks:
        entry["total_sks"] = total_sks
    save_khs_history(history)
    logger.info(f"KHS history saved: {who} {semester} IP={ips} IPK={ipk}")


async def login_kulino_and_get_tugas(account_key: str) -> list[dict]:
    """Login Kulino, ambil tugas dari Upcoming Events."""
    account = KULINO_ACCOUNTS[account_key]
    await send_message(f"⏳ Menghubungi Kulino {account['name']}...")

    async with get_page() as page:
        try:
            await page.goto(KULINO_URL, wait_until="domcontentloaded", timeout=BROWSER_NAV_TIMEOUT)
            await page.wait_for_load_state("networkidle", timeout=BROWSER_NETWORK_IDLE_TIMEOUT)
            await page.fill("#inputName", account["nim"])
            await page.fill("#inputPassword", account["password"])
            await page.click("button:has-text('Log in')")
            await page.wait_for_timeout(BROWSER_SETTLE_MS)

            login_form = await page.query_selector("#inputName")
            if login_form:
                logger.warning("Login Kulino gagal: form login masih muncul")
                return []

            tugas = await tb.scrape_kulino_tugas(page, account)
            return tugas
        except Exception as e:
            logger.error(f"Error Kulino: {e}")
            return []


async def do_presensi_siadin(account_key: str) -> tuple[bool, str]:
    """Login MHS & klik presensi dengan auto-retry."""
    account = MHS_ACCOUNTS[account_key]
    max_retries = 3
    last_msg = ""
    
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


def _load_presensi_done_for_today(today_str: str) -> set:
    """Load presensi_done keys. Reset kalau tanggal ganti (hari baru)."""
    global _presensi_done, _presensi_done_date
    data = load_presensi_done()
    if data.get("date") != today_str:
        _presensi_done = set()
        _presensi_done_date = today_str
    else:
        _presensi_done = set(data.get("keys", []))
        _presensi_done_date = today_str
    return _presensi_done


def _save_presensi_done(today_str: str) -> None:
    """Persist ke file."""
    save_presensi_done(today_str, _presensi_done)


# ============ Proactive Loop Helpers ============
async def _handle_dashboard_triggers() -> None:
    trigger_tugas_count = int(consume_control("trigger_tugas", 0) or 0)
    if trigger_tugas_count > 0:
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

    trigger_presensi_who = consume_control("trigger_presensi", "")
    if trigger_presensi_who in ("saya", "pacar"):
        nama = MHS_ACCOUNTS[trigger_presensi_who]["name"]
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
        logger.info(f"Auto-sync jadwal: {msg}")
        await send_message(f"🔄 Auto-sync jadwal mingguan:\n{msg}")


async def _check_daily_tugas(hour: int, minute: int, today_str: str) -> None:
    global _tugas_checked_today
    if hour == 17 and minute < 2 and _tugas_checked_today != today_str:
        _tugas_checked_today = today_str
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
    schedules = load_schedules()
    now_min = hour * 60 + minute
    for who in ("saya", "pacar"):
        if who not in MHS_ACCOUNTS:
            continue
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
                nama = MHS_ACCOUNTS[who]["name"]
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
    schedules = load_schedules()
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
    cache = load_nilai_cache()
    if not cache:
        logger.info("Nilai cache kosong, skip auto-check")
        return
    for who in ("saya", "pacar"):
        if who not in MHS_ACCOUNTS:
            continue
        cached_courses = cache.get(who, {})
        if not cached_courses:
            continue
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
            _save_khs_history(who, khs)
            diff = diff_nilai({who: cached_courses}, {who: new_map})
            if diff:
                lines = [f"🔔 *Nilai Baru!* ({account['name']})"]
                for d in diff:
                    lines.append(f"  • {d['matkul']}: {d['old']} → *{d['new']}*")
                if khs.get("ip_semester") is not None:
                    lines.append(f"\n  📊 IP: {khs['ip_semester']}")
                await send_message("\n".join(lines))
                await send_message(format_khs_message(khs, account["name"]))
            cache[who] = new_map
            save_nilai_cache(cache)
        except Exception as e:
            logger.error(f"Nilai check {who}: {e}")


async def _check_attendance_alerts(hour: int, minute: int, hari_id: str) -> None:
    """Kirim warning jika ada matkul dengan presensi < 75%. (tiap jam 20:00)."""
    if hour != 20 or minute >= 2 or not hari_id:
        return
    from storage import compute_attendance, attendance_alert

    now = datetime.now()
    for who in ("saya", "pacar"):
        if who not in MHS_ACCOUNTS:
            continue
        schedules = load_schedules()
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
    schedules = load_schedules()
    for who in ("saya", "pacar"):
        if who not in MHS_ACCOUNTS:
            continue
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
                    _save_presensi_done(today_str)
                # Clear any snoozed reminders for this sesi
                snooze_key = f"snoozed:{who}:{hari_id}:{jam_mulai}"
                _snoozed_reminders.pop(snooze_key, None)
                logger.info(f"Presensi: {who} - {mk}")
                await send_message(f"🤖 Presensi {MHS_ACCOUNTS[who]['name']} - {mk} {jam}")
                ok, msg = await do_presensi_siadin(who)
                if ok:
                    await send_message(
                        f"✅ Presensi {MHS_ACCOUNTS[who]['name']} berhasil!"
                        f"\n📖 {mk}\n🕐 {jam}\n🏫 {ruang}"
                    )
                    await send_photo(SCREENSHOT_PRESENSI)
                    try:
                        write_logbook(now.strftime("%Y-%m-%d"), who, jam, mk, ruang, "hadir")
                    except Exception as e:
                        logger.error(f"Logbook error: {e}")
                else:
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
    schedules = load_schedules()
    total = sum(len(schedules.get(w, {}).get(hari_id, [])) for w in ("saya", "pacar"))
    if total == 0:
        return

    _morning_reminder_date = today_str
    header = (
        f"☀️ *Selamat pagi!*\n\n"
        f"📅 Jadwal hari ini ({HARI_INDONESIA.get(hari_id, hari_id)}):\n"
    )
    for who in ("saya", "pacar"):
        nama = NAMA_SAYA if who == "saya" else NAMA_PACAR
        slots = schedules.get(who, {}).get(hari_id, [])
        if not slots:
            continue
        header += f"\n👤 *{nama}* ({total} kelas):\n"
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

            _load_presensi_done_for_today(today_str)

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
            if minute == 15:
                for target in ("saya", "pacar"):
                    await check_materials_for(target)

            today_holiday = get_today_holiday()
            await _check_morning_reminder(hour, minute, hari_id, today_holiday, today_str)
            await _run_autopilot_presensi(now, hour, minute, hari_id, today_holiday, today_str)

            await asyncio.sleep(PROACTIVE_INTERVAL_SECONDS)
        except Exception as e:
            inc_stat("errors")
            logger.error(f"Proactive error: {e}")
            sleep_s = min(_proactive_backoff, 300)
            logger.warning(f"Proactive backoff: sleeping {sleep_s:.0f}s")
            await asyncio.sleep(sleep_s)
            _proactive_backoff = min(_proactive_backoff * 2, 300)


# ============ Command Handlers ============
async def handle_command(text: str, chat_id: int | None = None) -> None:
    text = text.strip()
    t = text.lower()

    alias_cmd = resolve_alias(text)
    if alias_cmd:
        text = alias_cmd
        t = text.lower()

    if t in ("/start", "start", "halo", "hai", "hi"):
        await send_message("Halo! 👋 Saya Asisten Presensi Udinus. Ketik `help` untuk bantuan.")

    elif t in ("/help", "help", "bantuan"):
        await send_message(
            "🆘 *Bantuan Presensi Udinus Bot*\n\n"
            "📅 *Jadwal & Ujian*\n"
            "`jadwal [hari]` — Jadwal kuliah hari ini/besok/senin\n"
            "`jadwal gambar` — Screenshot jadwal hari ini (PNG)\n"
            "`jadwal update` — Sinkron jadwal dari MHS\n"
            "`ujian` / `ujian pacar` — Jadwal UTS/UAS\n"
            "`libur` — Daftar hari libur nasional 2026\n\n"
            "📝 *Tugas & Deadline*\n"
            "`cek tugas` / `cek tugas pacar` — Tugas Kulino\n"
            "`deadline` — List deadline tersimpan\n"
            "`statustugas <nama>` — Tandai tugas selesai\n"
            "`cleanup` — Hapus deadline yang sudah lewat\n\n"
            "🤖 *Presensi*\n"
            "`presensi` / `presensi pacar` — Presensi manual sekarang\n"
            "`autopilot on/off` — Nyalakan/matikan autopilot\n"
            "*(autopilot akan jalan otomatis 30 menit sebelum kelas)*\n\n"
            "📊 *Info*\n"
            "`status` — Status bot & statistik\n"
            "`quickstats` / `ringkasan` — Ringkasan cepat\n"
            "`nilai` / `khs` — Nilai & IP terbaru dari SiAdin\n"
            "`tanggal` — Tanggal & hari ini\n"
            "`logbook` — Riwayat presensi\n\n"
            "🔧 *Settings*\n"
            "`addalias <nama> <perintah>` — Bikin alias\n"
            "`delalias <nama>` — Hapus alias\n\n"
            "💡 *Tips:*\n"
            "• Pagi jam 07:00 bot kirim reminder jadwal + screenshot otomatis\n"
            "• Nilai baru terdeteksi otomatis — bot akan notify tanpa diminta!\n"
            "• Tap ⏰ Snooze 10m di reminder kelas untuk tunda 10 menit\n"
            "• Kirim `presensi pacar` buat absen akun Azfa\n"
            "• Kirim `ujian pacar` buat lihat jadwal ujian Azfa\n"
            "• Dashboard: `http://localhost:8787?token=presensi123`"
        )

    elif t in ("/status", "status", "stats"):
        from config import BOT_START_TIME

        uptime = datetime.now() - BOT_START_TIME
        d, r = uptime.days, uptime.seconds
        h, m = r // 3600, (r % 3600) // 60
        cache = load_tasks_deadlines()
        active = sum(1 for k in cache if k != "notified")
        snap = get_stats_snapshot()
        await send_message(
            f"🤖 *Status*\n"
            f"⏱ {d}h {h}j {m}m\n"
            f"📥 {snap['messages_received']} | 📤 {snap['messages_sent']}\n"
            f"📝 {snap['tugas_checks']} | ✅ {snap['presensi_done']}\n"
            f"⚠ {snap['errors']} | 📋 {active}\n"
            f"👥 {len(ALLOWED_CHAT_IDS)} user\n"
            f"🤖 {'Aktif' if get_autopilot() else 'Nonaktif'}"
        )

    elif t in ("/tanggal", "tanggal", "kalender"):
        now = datetime.now()
        esok = now + timedelta(days=1)
        await send_message(
            f"📅 Hari ini: {now.strftime('%A, %d %B %Y')}\n"
            f"🕐 {now.strftime('%H:%M')} WIB\n"
            f"🗓 Besok: {esok.strftime('%A, %d %B %Y')}\n\n"
            f"{get_schedule_for('saya', 'hari ini')}"
        )

    elif t.startswith("jadwal") and ("update" in t or "refresh" in t or "sinkron" in t):
        await send_message("⏳ Sinkron jadwal...")
        ok, msg = await update_schedules_from_mhs()
        await send_message(f"{'✅' if ok else '❌'} {msg}")

    elif t.startswith("jadwal") and ("gambar" in t or "foto" in t or "image" in t or "screenshot" in t):
        await send_message("⏳ Render jadwal...")
        from render import render_jadwal_png
        arg = t.replace("jadwal", "", 1).replace("gambar", "").replace("foto", "")
        arg = arg.replace("image", "").replace("screenshot", "").strip()
        if not arg or arg == "hari ini":
            day_name = datetime.now().strftime("%A").lower()
            target_hari = HARI_ID.get(day_name, "")
        elif arg in HARI_INDONESIA or arg in HARI_ID:
            target_hari = HARI_ID.get(arg, arg)
        else:
            target_hari = ""
        if not target_hari:
            await send_message("Hari tidak dikenali. Coba: `jadwal gambar senin`")
        else:
            schedules = load_schedules()
            async with get_page() as page:
                ok = await render_jadwal_png(page, schedules, target_hari, SCREENSHOT_JADWAL)
            if ok and os.path.exists(SCREENSHOT_JADWAL):
                await send_photo(SCREENSHOT_JADWAL)
            else:
                await send_message("❌ Gagal render jadwal.")

    elif t.startswith("jadwal"):
        arg = t.replace("jadwal", "", 1).strip()
        for w in ("saya", "pacar"):
            await send_message(get_schedule_for(w, arg or "hari ini"))

    elif t in ("deadline", "tugas deadline", "list deadline"):
        cache = load_tasks_deadlines()
        items = [k for k in cache if k != "notified"]
        if not items:
            await send_message("📭 Belum ada deadline.")
        else:
            lines = ["📋 *Deadline*\n"]
            for k in items:
                d = cache[k]
                lines.append(f"▪️ *{d['name']}* - {d['deadline_raw']}")
            await send_message("\n".join(lines))

    elif t.startswith("statustugas") or t.startswith("done"):
        keyword = t.replace("statustugas", "").replace("done", "").strip()
        if not keyword:
            await send_message("Gunakan: `statustugas <nama>`")
        else:
            cache = load_tasks_deadlines()
            found = None
            for k in list(cache.keys()):
                if k != "notified" and keyword.lower() in cache[k]["name"].lower():
                    found = k
                    break
            if found:
                name = cache[found]["name"]
                del cache[found]
                for nk in list(cache.get("notified", {}).keys()):
                    if nk.startswith(found):
                        del cache["notified"][nk]
                save_tasks_deadlines(cache)
                await send_message(f"✅ *{name}* ditandai selesai")
            else:
                await send_message(f"❌ `{keyword}` tidak ditemukan")

    elif t in ("cleanup", "bersihkan", "hapus deadline"):
        removed = cleanup_expired_deadlines()
        if removed:
            active = sum(1 for k in load_tasks_deadlines() if k != "notified")
            await send_message(f"🧹 {removed} dihapus. {active} tersisa.")
        else:
            await send_message("🧹 Tidak ada yang expired.")

    elif t in ("quickstats", "quick", "ringkasan", "stats cepat"):
        snap = get_stats_snapshot()
        cache = load_tasks_deadlines()
        now = datetime.now()
        nearest = []
        for k, v in cache.items():
            if k == "notified":
                continue
            iso = v.get("deadline_iso")
            if not iso:
                continue
            try:
                dt = datetime.fromisoformat(iso)
            except ValueError:
                continue
            if dt > now:
                nearest.append((dt, v.get("name", k), v.get("account", "")))
        nearest.sort()
        active_deadline = len(nearest)
        nearest_lines = []
        for dt, name, acc in nearest[:3]:
            jam = int((dt - now).total_seconds() / 3600)
            nearest_lines.append(f"  • {acc}: {name} ({jam}j)")
        nearest_text = "\n".join(nearest_lines) if nearest_lines else "  (tidak ada)"

        day_name = now.strftime("%A").lower()
        hari_id = HARI_ID.get(day_name, "")
        schedules = load_schedules()
        total_classes = sum(
            len(schedules.get(w, {}).get(hari_id, [])) for w in ("saya", "pacar")
        ) if hari_id else 0
        today_holiday = get_today_holiday()
        holiday_text = f"🎉 {today_holiday}" if today_holiday else ""

        msg = (
            f"📊 *Quick Stats*\n\n"
            f"🤖 Autopilot: {'Aktif' if get_autopilot() else 'OFF'}\n"
            f"📅 Hari ini: {total_classes} kelas {holiday_text}\n"
            f"📋 Deadline: {active_deadline} aktif\n"
            f"{nearest_text}\n\n"
            f"📨 Pesan: {snap.get('messages_received', 0)} | "
            f"✅ Presensi: {snap.get('presensi_done', 0)} | "
            f"📝 Cek tugas: {snap.get('tugas_checks', 0)}\n"
            f"⚠ Error: {snap.get('errors', 0)}"
        )
        await send_message(msg)

    elif t in ("logbook", "catatan"):
        if not os.path.exists(LOG_DIR):
            await send_message("📓 Logbook kosong.")
        else:
            files = sorted([f for f in os.listdir(LOG_DIR) if f.endswith(".md")], reverse=True)[:3]
            if not files:
                await send_message("📓 Logbook kosong.")
            else:
                text = "📓 *Logbook* (3 terakhir):\n\n"
                for f in files:
                    p = os.path.join(LOG_DIR, f)
                    with open(p, encoding="utf-8") as fp:
                        text += f"📅 {f[:-3]}\n```\n{fp.read()[:500]}\n```\n"
                await send_message(text)

    elif t.startswith("addalias") or t.startswith("/addalias"):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await send_message("Gunakan: `addalias <nama> <perintah>`")
        else:
            name = parts[1]
            cmd = parts[2]
            add_alias(name, cmd)
            await send_message(f"✅ Alias `/{name}` → `{cmd}`")

    elif t.startswith("delalias") or t.startswith("/delalias"):
        parts = text.split()
        if len(parts) < 2:
            await send_message("Gunakan: `delalias <nama>`")
        else:
            if remove_alias(parts[1].lower()):
                await send_message(f"✅ Alias `/{parts[1]}` dihapus.")
            else:
                await send_message(f"❌ Alias `{parts[1]}` tidak ditemukan.")

    elif t.startswith("/addchid") or t.startswith("addchid"):
        parts = text.split()
        if len(parts) < 2:
            await send_message("Gunakan: `/addchid <chat_id>`")
        else:
            try:
                new_id = int(parts[1])
                if new_id in ALLOWED_CHAT_IDS:
                    await send_message(f"ℹ️ {new_id} sudah ada.")
                else:
                    ALLOWED_CHAT_IDS.append(new_id)
                    save_chat_id(new_id)
                    await send_message(f"✅ {new_id} ditambahkan!")
            except ValueError:
                await send_message("Format salah")

    elif "tugas" in t or "cek tugas" in t:
        target = "pacar" if "azfa" in t or "pacar" in t else "saya"
        inc_stat("tugas_checks")
        await send_message("⏳ Cek tugas...")
        tugas = await login_kulino_and_get_tugas(target)
        if tugas:
            rows = [f"{'No':<4} {'Tugas':<40} {'Deadline':<20}"]
            rows.append("-" * 70)
            for i, t in enumerate(tugas, 1):
                rows.append(f"{i:<4} {t.get('name','?')[:38]:<40} {t.get('deadline','?')[:18]:<20}")
            await send_message(f"📝 *Tugas {KULINO_ACCOUNTS[target]['name']}*\n```\n" + "\n".join(rows) + "\n```")
            if os.path.exists(SCREENSHOT_TUGAS):
                await send_photo(SCREENSHOT_TUGAS)
        else:
            await send_message("📭 Tidak ada tugas aktif.")
        await process_and_remind_deadlines(tugas, target, send_message)

    elif t.startswith("cek") and "materi" in t:
        target = "pacar" if "azfa" in t or "pacar" in t else "saya"
        # Extract course query after command
        cmd_text = text.replace("cek materi", "", 1).replace("pacar", "").replace("azfa", "").strip()
        query = cmd_text if cmd_text else ""
        await check_materials_for(target, course_query=query)

    elif "autopilot" in t:
        if "nonaktif" in t or "off" in t:
            set_autopilot(False)
            await send_message("🤖 Autopilot: NONAKTIF")
        else:
            set_autopilot(True)
            await send_message("🤖 Autopilot: AKTIF")

    elif "presensi" in t or "hadir" in t:
        today_h = get_today_holiday()
        if today_h:
            await send_message(f"📢 Hari ini libur: *{today_h}*.\nTidak perlu presensi.")
            return
        target = "pacar" if "azfa" in t or "pacar" in t else "saya"
        ok, msg = await do_presensi_siadin(target)
        if ok:
            inc_stat("presensi_done")
            await send_message(f"✅ Presensi {MHS_ACCOUNTS[target]['name']} berhasil!")
            await send_photo(SCREENSHOT_PRESENSI)
        else:
            await send_message(f"❌ {msg}")

    elif t in ("nilai", "khs", "cek nilai", "daftarnilai", "hasil studi"):
        target = "pacar" if "azfa" in t or "pacar" in t else "saya"
        account = MHS_ACCOUNTS[target]
        await send_message(f"⏳ Ambil KHS {account['name']}...")
        try:
            async with get_page() as page:
                await page.goto("https://mhs.dinus.ac.id/", wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2000)
                await page.fill("#username", account["nim"])
                await page.fill("#password", account["password"])
                async with page.expect_navigation(timeout=30000, wait_until="networkidle"):
                    await page.click("button:has-text('Masuk ke SiAdin')")
                khs = await tb.scrape_khs(page, account)
            await send_message(format_khs_message(khs, account["name"]))
            _save_khs_history(target, khs)
            # Save to cache for diff detection
            cache = load_nilai_cache()
            cache[target] = {m["kdmk"]: m for m in khs["matkul"]}
            save_nilai_cache(cache)
        except Exception as e:
            logger.error(f"KHS error: {e}")
            await send_message(f"❌ Gagal ambil KHS: {e}")

    elif t.startswith("ujian"):
        target = "pacar" if "azfa" in t or "pacar" in t else "saya"
        await send_message(f"⏳ Cek jadwal ujian {MHS_ACCOUNTS[target]['name']}...")
        try:
            async with get_page() as page:
                account = MHS_ACCOUNTS[target]
                await page.goto("https://mhs.dinus.ac.id/", wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2000)
                await page.fill("#username", account["nim"])
                await page.fill("#password", account["password"])
                async with page.expect_navigation(timeout=30000, wait_until="networkidle"):
                    await page.click("button:has-text('Masuk ke SiAdin')")
                items, _ = await tb.scrape_jadwal_ujian(page, account)
            if items:
                lines = [f"📋 *Jadwal Ujian {MHS_ACCOUNTS[target]['name']}*\n"]
                for i, item in enumerate(items, 1):
                    lines.append(
                        f"{i}. *{item.get('matkul', '?')}*\n"
                        f"   📅 {item.get('hari_tanggal', '?')}\n"
                        f"   🕐 {item.get('jam', '?')} | 🏫 {item.get('ruang', '-')}\n"
                        f"   📝 {item.get('ujian', '-')}"
                    )
                await send_message("\n\n".join(lines))
            else:
                await send_message("📭 Belum ada jadwal ujian.")
        except Exception as e:
            logger.error(f"ujian error: {e}")
            await send_message(f"❌ Gagal cek jadwal ujian: {e}")

    elif t in ("libur", "libur 2026", "hari libur", "tanggal merah"):
        if not HOLIDAY_CACHE:
            HOLIDAY_CACHE.update(load_holidays())
        today_h = get_today_holiday()
        msg_parts = []
        if today_h:
            msg_parts.append(f"📢 *Hari ini LIBUR*\n{today_h}\n")

        now = datetime.now()
        upcoming = []
        for date_str in sorted(HOLIDAY_CACHE.keys()):
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            if dt < now:
                continue
            name = HOLIDAY_CACHE[date_str]
            hari_en = dt.strftime("%A").lower()
            day_name = HARI_INDONESIA.get(HARI_ID.get(hari_en, ""), "?")
            upcoming.append(f"  {day_name}, {date_str}: {name}")

        msg_parts.append("📅 *Libur 2026 (sisa)*")
        msg_parts.extend(upcoming[:15])
        await send_message("\n".join(msg_parts))

    elif t in ("statpresensi", "rekap", "presensi stat", "statpres"):
        from storage import compute_attendance, attendance_alert

        target = "pacar" if "azfa" in t or "pacar" in t else "saya"
        now = datetime.now()
        year, month = now.year, now.month
        await send_message(f"⏳ Hitung statistik presensi {MHS_ACCOUNTS[target]['name']}...")
        schedules = load_schedules()
        results = compute_attendance(schedules, target, year, month)
        msg = format_attendance_message(results, MHS_ACCOUNTS[target]["name"], year, month)
        await send_message(msg)
        warnings = attendance_alert(results)
        if warnings:
            await send_message(
                "\U000026A0 *Peringatan Kehadiran*\n" + "\n".join(f"  \u2022 {w}" for w in warnings)
            )

    else:
        try:
            from nlp import parse_question, answer_jadwal, answer_presensi

            intent = parse_question(text)
            if intent["intent"] in ("jadwal", "presensi") and intent["hari"]:
                schedules = load_schedules()
                if intent["intent"] == "jadwal":
                    reply = answer_jadwal(intent, schedules, "saya")
                else:
                    reply = answer_presensi(intent, schedules)
                await send_message(reply)
            elif intent["intent"] == "deadline":
                cache = load_tasks_deadlines()
                items = [v for k, v in cache.items() if k != "notified"]
                if intent["keyword"]:
                    items = [i for i in items if intent["keyword"].lower() in i.get("name", "").lower()]
                if items:
                    lines = ["📋 Deadline" + (f" (cari: {intent['keyword']})" if intent.get('keyword') else "") + ":"]
                    for i in items[:10]:
                        lines.append(f"  • {i.get('name','')} - {i.get('deadline_raw','')}")
                    await send_message("\n".join(lines))
                else:
                    await send_message("📭 Deadline tidak ditemukan.")
            else:
                await send_message("Halo! Ketik `help` untuk lihat perintah.")
        except Exception as e:
            logger.error(f"NLP error: {e}")
            await send_message("Halo! Ketik `help` untuk lihat perintah.")


# ============ Main ============
async def main() -> None:
    global ALLOWED_CHAT_ID, ALLOWED_CHAT_IDS, _polling_backoff

    if not BOT_TOKEN:
        sys.exit(1)

    if not instance_lock.acquire_lock():
        logger.error("Instance lain sedang berjalan")
        sys.exit(1)

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

    loaded = load_chat_ids()
    # Bootstrap with ADMIN_CHAT_ID if set
    if ADMIN_CHAT_ID and ADMIN_CHAT_ID not in loaded:
        loaded.append(ADMIN_CHAT_ID)
        save_chat_id(ADMIN_CHAT_ID)

    ALLOWED_CHAT_IDS = loaded
    _CFG_IDS.clear()
    _CFG_IDS.extend(loaded)
    ALLOWED_CHAT_ID = _CFG_IDS[0] if _CFG_IDS else None
    logger.info(f"Chat IDs: {ALLOWED_CHAT_IDS}" if ALLOWED_CHAT_IDS else "Tunggu /addchid dari admin...")

    asyncio.create_task(proactive_check())
    logger.info("Proactive loop started")

    if os.environ.get("DASHBOARD_DISABLE", "0") != "1":
        try:
            from web_dashboard import run_in_thread

            run_in_thread(port=8787)
            logger.info("Web dashboard started at http://localhost:8787")
        except Exception as e:
            logger.error(f"Web dashboard gagal: {e}")
    else:
        logger.info("Web dashboard DISABLED")

    offset = load_offset()
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
                    save_offset(offset)

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
                                if who in MHS_ACCOUNTS:
                                    key = f"snoozed:{who}:{key_hari}:{jam_mulai}"
                                    _snoozed_reminders[key] = time.time() + SNOOZE_DURATION_SECONDS
                                    logger.info(f"Snooze set: {key} until {_snoozed_reminders[key]:.0f}")
                                    nama = MHS_ACCOUNTS[who]["name"]
                                    await answer_callback(cb_id, f"⏰ Akan diingatkan 10 menit lagi ({nama})")
                                else:
                                    await answer_callback(cb_id, "❌ Akun tidak dikenal")
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
                        continue

                    msg = update.get("message", {})
                    chat = msg.get("chat", {})
                    chat_id = chat.get("id")
                    text = msg.get("text", "")

                    if not text:
                        continue
                    if not ALLOWED_CHAT_IDS:
                        if ADMIN_CHAT_ID and chat_id == ADMIN_CHAT_ID:
                            save_chat_id(chat_id)
                            ALLOWED_CHAT_IDS = [chat_id]
                            ALLOWED_CHAT_ID = chat_id
                            from config import ALLOWED_CHAT_IDS as __C

                            __C.clear()
                            __C.append(chat_id)
                        else:
                            logger.info(f"Ditolak: {chat_id} (belum terdaftar, ADMIN_CHAT_ID={ADMIN_CHAT_ID})")
                            continue
                    if chat_id not in ALLOWED_CHAT_IDS:
                        continue

                    inc_stat("messages_received")
                    logger.info(f"{chat_id}: {text}")
                    await handle_command(text, chat_id)
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
async def check_materials_for(account_key: str, course_query: str = "") -> None:
    from scrapers.kulino import check_new_materials
    account = KULINO_ACCOUNTS[account_key]

    if course_query:
        await send_message(f"⏳ Cek materi *{course_query}* untuk {account['name']}...")
    else:
        await send_message(f"⏳ Cek semua materi Kulino {account['name']}...")
    
    cache = load_material_cache()
    async with get_page() as page:
        try:
            await page.goto(KULINO_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            await page.fill("#inputName", account["nim"])
            await page.fill("#inputPassword", account["password"])
            await page.click("button:has-text('Log in')")
            await page.wait_for_timeout(3000)
            
            new_files, found_courses = await check_new_materials(page, account, cache, course_query)
            save_material_cache(cache)

            if course_query and not found_courses:
                await send_message(f"❌ Mata kuliah \"{course_query}\" tidak ditemukan untuk {account['name']}.")
                return
            
            if not new_files:
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

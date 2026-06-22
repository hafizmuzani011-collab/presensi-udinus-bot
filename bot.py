"""Presensi Udinus Bot - Main entry point.
Dependencies: config.py, storage.py, utils.py, tg.py, telegram_bot.py, instance_lock.py
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta

import instance_lock
from config import *
from storage import *
from tg import send_message, send_photo, get_updates, set_default_chat_id

# Load saved stats
import json as _json
if os.path.exists("stats.json"):
    try:
        with open("stats.json") as _f:
            STATS.update(_json.load(_f))
    except:
        pass
from utils import get_schedule_for, process_and_remind_deadlines

# Dashboard control (shared with web dashboard)
try:
    from web_dashboard import CONTROL as DASH_CONTROL
except ImportError:
    DASH_CONTROL = {"autopilot": True, "trigger_tugas": False}

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("telegram_bot")

# ============ Browser Scrapers ============
async def login_kulino_and_get_tugas(account_key: str) -> list[dict]:
    """Login Kulino, ambil tugas dari Upcoming Events."""
    import telegram_bot as tb
    from browser import get_page
    account = KULINO_ACCOUNTS[account_key]
    await send_message(f"⏳ Menghubungi Kulino {account['name']}...")

    async with get_page() as page:
        try:
            await page.goto(KULINO_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_load_state("networkidle", timeout=30000)
            await page.fill("#inputName", account["nim"])
            await page.fill("#inputPassword", account["password"])
            await page.click("button:has-text('Log in')")
            await page.wait_for_timeout(5000)

            if "login" in page.url.lower():
                return []

            tugas = await tb.scrape_kulino_tugas(page, account)
            return tugas
        except Exception as e:
            logger.error(f"Error Kulino: {e}")
            return []


async def do_presensi_siadin(account_key: str) -> tuple[bool, str]:
    """Login MHS & klik presensi."""
    import telegram_bot as tb
    from browser import get_page
    account = MHS_ACCOUNTS[account_key]
    await send_message(f"🤖 Autopilot {account['name']} - presensi dimulai...")

    async with get_page() as page:
        try:
            ok, msg = await tb.scrape_siadin_presensi(page, account)
            return ok, msg
        except Exception as e:
            logger.error(f"Error presensi: {e}")
            return False, str(e)


async def update_schedules_from_mhs() -> tuple[bool, str]:
    """Sinkron jadwal dari MHS untuk kedua akun."""
    import telegram_bot as tb
    from browser import get_page

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


# ============ Proactive Loop ============
_tugas_checked_today: str | None = None
_presensi_done: set = set()

async def proactive_check():
    global _tugas_checked_today, _presensi_done
    while True:
        try:
            now = datetime.now()
            hour, minute = now.hour, now.minute
            today_str = now.strftime("%Y-%m-%d")
            day_name = now.strftime("%A").lower()
            hari_id = {"monday":"senin","tuesday":"selasa","wednesday":"rabu",
                       "thursday":"kamis","friday":"jumat","saturday":"sabtu","sunday":"minggu"}.get(day_name, "")

            # === Web dashboard trigger (single source of truth) ===
            if not DASH_CONTROL.get("autopilot"):
                await asyncio.sleep(10)
                continue
            if DASH_CONTROL.pop("trigger_tugas", False):
                await send_message("🔔 Trigger dari dashboard: cek tugas...")
                for key in KULINO_ACCOUNTS:
                    nama = KULINO_ACCOUNTS[key]["name"]
                    await send_message(f"⏳ Menghubungi Kulino {nama}...")
                    tugas = await login_kulino_and_get_tugas(key)
                    if tugas:
                        # Kirim tabel detail
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

            # === Cek tugas jam 17:00 (sekali sehari) ===
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
                await asyncio.sleep(60)

            # === Cek deadline setiap 30 mnt ===
            if minute % 30 == 0:
                for key in KULINO_ACCOUNTS:
                    await process_and_remind_deadlines([], key, send_message)

            # === Auto backup setiap jam ===
            if minute == 0:
                if backup_tasks_deadlines():
                    logger.info("Backup tasks_deadlines.json OK")
                # Simpan stats supaya tidak hilang saat restart
                try:
                    with open("stats.json", "w") as sf:
                        _json.dump(STATS, sf, indent=2)
                except Exception as e:
                    logger.error(f"Save stats gagal: {e}")

            # === Autopilot Presensi ===
            if AUTOPILOT_ENABLED and hari_id:
                schedules = load_schedules()
                for who in ("saya", "pacar"):
                    if who not in MHS_ACCOUNTS:
                        continue
                    today_sched = schedules.get(who, {}).get(hari_id, [])
                    for jam, mk, ruang in today_sched:
                        jam_mulai = jam.split(" - ")[0].strip().replace(".", ":")
                        try:
                            h, m = map(int, jam_mulai.split(":"))
                        except ValueError:
                            continue
                        sesi_key = f"{who}:{hari_id}:{jam_mulai}"
                        if sesi_key in _presensi_done:
                            continue
                        now_min = hour * 60 + minute
                        start_min = h * 60 + m
                        if start_min <= now_min < start_min + 30:
                            _presensi_done.add(sesi_key)
                            logger.info(f"Presensi: {who} - {mk}")
                            await send_message(f"🤖 Presensi {MHS_ACCOUNTS[who]['name']} - {mk} {jam}")
                            ok, msg = await do_presensi_siadin(who)
                            if ok:
                                await send_message(f"✅ Presensi {MHS_ACCOUNTS[who]['name']} berhasil!"
                                                   f"\n📖 {mk}\n🕐 {jam}\n🏫 {ruang}")
                                await send_photo(SCREENSHOT_PRESENSI)
                            else:
                                await send_message(f"⚠️ Presensi {MHS_ACCOUNTS[who]['name']} gagal: {msg}")
                            await asyncio.sleep(60)

            await asyncio.sleep(30)
        except Exception as e:
            STATS["errors"] += 1
            logger.error(f"Proactive error: {e}")
            await asyncio.sleep(30)


# ============ Command Handlers ============
async def handle_command(text: str, chat_id: int | None = None):
    global AUTOPILOT_ENABLED, _presensi_done
    text = text.strip()
    t = text.lower()

    if t in ("/start", "start", "halo", "hai", "hi"):
        await send_message("Halo! 👋 Saya Asisten Presensi Udinus. Ketik `help` untuk bantuan.")

    elif t in ("/help", "help", "bantuan"):
        await send_message(
            "🆘 *Bantuan*\n\n"
            "📅 `jadwal [hari]` - Jadwal kuliah\n"
            "📅 `jadwal update` - Sinkron dari MHS\n"
            "📝 `cek tugas` - Tugas Kulino\n"
            "📝 `cek tugas pacar` - Tugas Azfa\n"
            "📋 `deadline` - Deadline tersimpan\n"
            "✅ `statustugas <nama>` - Tandai selesai\n"
            "🧹 `cleanup` - Hapus deadline lewat\n"
            "🤖 `autopilot` - Presensi otomatis\n"
            "📷 `presensi` - Presensi manual\n"
            "📊 `/status` - Status bot\n"
            "➕ `/addchid <id>` - Tambah whitelist"
        )

    elif t in ("/status", "status", "stats"):
        uptime = datetime.now() - BOT_START_TIME
        d, r = uptime.days, uptime.seconds
        h, m = r // 3600, (r % 3600) // 60
        cache = load_tasks_deadlines()
        active = sum(1 for k in cache if k != "notified")
        await send_message(
            f"🤖 *Status*\n"
            f"⏱ {d}h {h}j {m}m\n"
            f"📥 {STATS['messages_received']} | 📤 {STATS['messages_sent']}\n"
            f"📝 {STATS['tugas_checks']} | ✅ {STATS['presensi_done']}\n"
            f"⚠ {STATS['errors']} | 📋 {active}\n"
            f"👥 {len(ALLOWED_CHAT_IDS)} user\n"
            f"🤖 {'Aktif' if AUTOPILOT_ENABLED else 'Nonaktif'}"
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
                    found = k; break
            if found:
                name = cache[found]["name"]
                del cache[found]
                for nk in list(cache.get("notified", {}).keys()):
                    if nk.startswith(found): del cache["notified"][nk]
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
        STATS["tugas_checks"] += 1
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
            await send_message(f"📭 Tidak ada tugas aktif.")
        await process_and_remind_deadlines(tugas, target, send_message)

    elif "autopilot" in t:
        if "nonaktif" in t or "off" in t:
            AUTOPILOT_ENABLED = False
            DASH_CONTROL["autopilot"] = False
            await send_message("🤖 Autopilot: NONAKTIF")
        else:
            AUTOPILOT_ENABLED = True
            DASH_CONTROL["autopilot"] = True
            await send_message("🤖 Autopilot: AKTIF")

    elif "presensi" in t or "hadir" in t:
        ok, msg = await do_presensi_siadin("saya")
        if ok:
            STATS["presensi_done"] += 1
            await send_message("✅ Presensi berhasil!")
            await send_photo(SCREENSHOT_PRESENSI)
        else:
            await send_message(f"❌ {msg}")

    else:
        await send_message("Halo! Ketik `help` untuk lihat perintah.")


# ============ Main ============
async def main():
    global ALLOWED_CHAT_ID, ALLOWED_CHAT_IDS

    if not BOT_TOKEN:
        sys.exit(1)

    # Lock
    if not instance_lock.acquire_lock():
        logger.error("Instance lain sedang berjalan")
        sys.exit(1)

    # Load chat IDs
    ALLOWED_CHAT_IDS = load_chat_ids()
    ALLOWED_CHAT_ID = ALLOWED_CHAT_IDS[0] if ALLOWED_CHAT_IDS else None
    set_default_chat_id(ALLOWED_CHAT_ID)
    logger.info(f"Chat IDs: {ALLOWED_CHAT_IDS}" if ALLOWED_CHAT_IDS else "Tunggu chat pertama...")

    # Start proactive
    asyncio.create_task(proactive_check())
    logger.info("Proactive loop started")

    # Start web dashboard (nonaktif jika DASHBOARD_DISABLE=1)
    import os
    if os.environ.get("DASHBOARD_DISABLE", "0") != "1":
        try:
            from web_dashboard import run_in_thread
            run_in_thread(port=8787)
            logger.info("Web dashboard started at http://localhost:8787")
        except Exception as e:
            logger.error(f"Web dashboard gagal: {e}")
    else:
        logger.info("Web dashboard DISABLED (jalankan manual: python web_dashboard.py)")

    # Polling
    offset = load_offset()
    try:
        while True:
            try:
                updates = await get_updates(offset)
                for update in updates:
                    offset = update["update_id"] + 1
                    save_offset(offset)
                    msg = update.get("message", {})
                    chat = msg.get("chat", {})
                    chat_id = chat.get("id")
                    text = msg.get("text", "")

                    if not text:
                        continue
                    if not ALLOWED_CHAT_IDS:
                        save_chat_id(chat_id)
                        ALLOWED_CHAT_IDS = [chat_id]
                        ALLOWED_CHAT_ID = chat_id
                        set_default_chat_id(chat_id)
                    if chat_id not in ALLOWED_CHAT_IDS:
                        continue

                    STATS["messages_received"] += 1
                    logger.info(f"{chat_id}: {text}")
                    await handle_command(text, chat_id)
            except Exception as e:
                STATS["errors"] += 1
                logger.error(f"Polling error: {e}")
                await asyncio.sleep(5)
    finally:
        instance_lock.release_lock()
        from browser import close_browser
        await close_browser()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        instance_lock.release_lock()

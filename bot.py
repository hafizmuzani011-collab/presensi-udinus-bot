"""Presensi Udinus Bot - Main entry point.
Dependencies: config.py, storage.py, utils.py, tg.py, telegram_bot.py, instance_lock.py
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta

import instance_lock
from config import (
    KULINO_ACCOUNTS, MHS_ACCOUNTS, KULINO_URL, MHS_URL,
    LOG_FILE, LOG_DIR, STATS_FILE, SCREENSHOT_PRESENSI, SCREENSHOT_TUGAS,
    SCHEDULES_FILE, BOT_TOKEN, ALLOWED_CHAT_IDS,
)
from storage import (
    load_chat_ids, save_chat_id, load_offset, save_offset,
    load_schedules, load_tasks_deadlines, save_tasks_deadlines,
    backup_tasks_deadlines, write_logbook, cleanup_expired_deadlines,
    load_presensi_done, save_presensi_done,
)
from tg import send_message, send_photo, get_updates, set_default_chat_id, make_inline_keyboard, answer_callback
from utils import get_schedule_for, process_and_remind_deadlines

# Load saved stats
if os.path.exists(STATS_FILE):
    try:
        with open(STATS_FILE, encoding="utf-8") as _f:
            stats_loaded = json.load(_f)
            from config import STATS
            STATS.update(stats_loaded)
    except Exception:
        pass
from config import STATS  # ensure latest after load

# Dashboard control (shared with web dashboard) - circular safe
try:
    from web_dashboard import CONTROL as DASH_CONTROL
except Exception:
    DASH_CONTROL = {"autopilot": True, "trigger_tugas": 0, "trigger_presensi": 0}

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("telegram_bot")

# Bot state
ALLOWED_CHAT_ID = None
_polling_backoff = 1.0  # exponential backoff


def get_autopilot() -> bool:
    """Single source of truth untuk status autopilot presensi."""
    return bool(DASH_CONTROL.get("autopilot", True))


def set_autopilot(enabled: bool) -> None:
    """Set autopilot dari Telegram command atau dashboard."""
    DASH_CONTROL["autopilot"] = bool(enabled)
    logger.info(f"Autopilot -> {enabled}")


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

            # Robust detection: cek kalau form login masih ada = gagal
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
_presensi_done_date: str = ""
_reminder_sent: set = set()  # sesi_key yang sudah dikirim reminder


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

            # Load presensi_done persistent (auto-reset kalau ganti hari)
            _load_presensi_done_for_today(today_str)

            autopilot_on = get_autopilot()

            # === Trigger dari dashboard (counter, tidak ke-miss) ===
            trigger_tugas_count = int(DASH_CONTROL.get("trigger_tugas", 0) or 0)
            if trigger_tugas_count > 0:
                DASH_CONTROL["trigger_tugas"] = 0
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

            # === Trigger presensi dari dashboard ===
            trigger_presensi_who = DASH_CONTROL.get("trigger_presensi", "")
            if trigger_presensi_who in ("saya", "pacar"):
                DASH_CONTROL["trigger_presensi"] = ""
                nama = MHS_ACCOUNTS[trigger_presensi_who]["name"]
                await send_message(f"🔔 Trigger dari dashboard: presensi {nama}...")
                ok, msg = await do_presensi_siadin(trigger_presensi_who)
                if ok:
                    await send_message(f"✅ Presensi {nama} berhasil!")
                    await send_photo(SCREENSHOT_PRESENSI)
                else:
                    await send_message(f"⚠️ Presensi {nama} gagal: {msg}")

            # === Auto-sync jadwal setiap Minggu 22:00 ===
            if day_name == "sunday" and hour == 22 and minute < 2:
                logger.info("Auto-sync jadwal mingguan...")
                ok, msg = await update_schedules_from_mhs()
                logger.info(f"Auto-sync jadwal: {msg}")
                await send_message(f"🔄 Auto-sync jadwal mingguan:\n{msg}")

            # === Cek tugas jam 17:00 (sekali sehari) - INDEPENDENT dari autopilot ===
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
                # Tidak ada sleep 60 - langsung ke iterasi berikutnya

            # === Cek deadline setiap 30 mnt - INDEPENDENT dari autopilot ===
            if minute % 30 == 0:
                for key in KULINO_ACCOUNTS:
                    await process_and_remind_deadlines([], key, send_message)

            # === Auto backup setiap jam - INDEPENDENT dari autopilot ===
            if minute == 0:
                if backup_tasks_deadlines():
                    logger.info("Backup tasks_deadlines.json OK")
                # Simpan stats supaya tidak hilang saat restart
                try:
                    with open(STATS_FILE, "w", encoding="utf-8") as sf:
                        json.dump(STATS, sf, indent=2)
                except Exception as e:
                    logger.error(f"Save stats gagal: {e}")

            # === Reminder 30 menit sebelum kelas (INDEPENDENT dari autopilot) ===
            if hari_id:
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
                        # Window reminder: 30-29 menit sebelum mulai
                        if start_min - 30 == now_min:
                            reminder_key = f"reminder:{who}:{hari_id}:{jam_mulai}"
                            if reminder_key in _reminder_sent:
                                continue
                            _reminder_sent.add(reminder_key)
                            nama = MHS_ACCOUNTS[who]["name"]
                            # Kirim dengan inline keyboard quick action
                            buttons = [[{"text": "✅ Presensi", "callback_data": "presensi:hadir:" + who}]]
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

            # === Autopilot Presensi (HANYA kalau autopilot on) ===
            if autopilot_on and hari_id:
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
                            _presensi_done.add(sesi_key)
                            _save_presensi_done(today_str)
                            logger.info(f"Presensi: {who} - {mk}")
                            await send_message(f"🤖 Presensi {MHS_ACCOUNTS[who]['name']} - {mk} {jam}")
                            ok, msg = await do_presensi_siadin(who)
                            if ok:
                                await send_message(f"✅ Presensi {MHS_ACCOUNTS[who]['name']} berhasil!"
                                                   f"\n📖 {mk}\n🕐 {jam}\n🏫 {ruang}")
                                await send_photo(SCREENSHOT_PRESENSI)
                                # Catat ke logbook harian
                                try:
                                    write_logbook(now.strftime("%Y-%m-%d"), who, jam, mk, ruang, "hadir")
                                except Exception as e:
                                    logger.error(f"Logbook error: {e}")
                            else:
                                await send_message(f"⚠️ Presensi {MHS_ACCOUNTS[who]['name']} gagal: {msg}")

            await asyncio.sleep(30)
        except Exception as e:
            STATS["errors"] += 1
            logger.error(f"Proactive error: {e}")
            await asyncio.sleep(30)


# ============ Command Handlers ============
async def handle_command(text: str, chat_id: int | None = None):
    text = text.strip()
    t = text.lower()

    # Resolve custom alias dulu
    try:
        from aliases import resolve_alias
        alias_cmd = resolve_alias(text)
        if alias_cmd:
            text = alias_cmd
            t = text.lower()
    except Exception as e:
        logger.error(f"Alias error: {e}")

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
        from config import BOT_START_TIME
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
            from aliases import add_alias
            name = parts[1]
            cmd = parts[2]
            add_alias(name, cmd)
            await send_message(f"✅ Alias `/{name}` → `{cmd}`")

    elif t.startswith("delalias") or t.startswith("/delalias"):
        parts = text.split()
        if len(parts) < 2:
            await send_message("Gunakan: `delalias <nama>`")
        else:
            from aliases import remove_alias
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
            set_autopilot(False)
            await send_message("🤖 Autopilot: NONAKTIF")
        else:
            set_autopilot(True)
            await send_message("🤖 Autopilot: AKTIF")

    elif "presensi" in t or "hadir" in t:
        target = "pacar" if "azfa" in t or "pacar" in t else "saya"
        ok, msg = await do_presensi_siadin(target)
        if ok:
            STATS["presensi_done"] += 1
            await send_message(f"✅ Presensi {MHS_ACCOUNTS[target]['name']} berhasil!")
            await send_photo(SCREENSHOT_PRESENSI)
        else:
            await send_message(f"❌ {msg}")

    elif t.startswith("ujian"):
        target = "pacar" if "azfa" in t or "pacar" in t else "saya"
        await send_message(f"⏳ Cek jadwal ujian {MHS_ACCOUNTS[target]['name']}...")
        import telegram_bot as tb
        from browser import get_page
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
                await send_message(f"📭 Belum ada jadwal ujian.")
        except Exception as e:
            logger.error(f"ujian error: {e}")
            await send_message(f"❌ Gagal cek jadwal ujian: {e}")

    else:
        # Natural language fallback: parse pertanyaan
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
async def main():
    global ALLOWED_CHAT_ID, ALLOWED_CHAT_IDS, _polling_backoff

    if not BOT_TOKEN:
        sys.exit(1)

    # Lock (Named Mutex - atomic OS-level, anti race condition)
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
    if os.environ.get("DASHBOARD_DISABLE", "0") != "1":
        try:
            from web_dashboard import run_in_thread
            run_in_thread(port=8787)
            logger.info("Web dashboard started at http://localhost:8787")
        except Exception as e:
            logger.error(f"Web dashboard gagal: {e}")
    else:
        logger.info("Web dashboard DISABLED")

    # Polling dengan exponential backoff
    offset = load_offset()
    try:
        while True:
            try:
                updates = await get_updates(offset)
                _polling_backoff = 1.0  # reset on success
                for update in updates:
                    offset = update["update_id"] + 1
                    save_offset(offset)
                    # Handle callback_query (inline keyboard)
                    if "callback_query" in update:
                        cb = update["callback_query"]
                        cb_id = cb.get("id")
                        cb_data = cb.get("data", "")
                        cb_chat = cb.get("message", {}).get("chat", {}).get("id")
                        cb_msg_id = cb.get("message", {}).get("message_id")
                        cb_chat_id = cb.get("from", {}).get("id")
                        if cb_chat_id and cb_chat_id not in ALLOWED_CHAT_IDS:
                            continue
                        await answer_callback(cb_id, "⏳ Memproses...")
                        # Parse callback_data: "presensi:hadir:saya" atau "presensi:hadir:pacar"
                        if cb_data.startswith("presensi:hadir:"):
                            who = cb_data.split(":")[-1]
                            if who in MHS_ACCOUNTS:
                                await send_message(f"⏳ Presensi {MHS_ACCOUNTS[who]['name']}...")
                                ok, msg = await do_presensi_siadin(who)
                                if ok:
                                    STATS["presensi_done"] += 1
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
                # Exponential backoff: 1, 2, 4, 8, 16, 32, max 60 detik
                await asyncio.sleep(min(_polling_backoff, 60))
                _polling_backoff = min(_polling_backoff * 2, 60)
    finally:
        instance_lock.release_lock()
        from browser import close_browser
        await close_browser()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        instance_lock.release_lock()

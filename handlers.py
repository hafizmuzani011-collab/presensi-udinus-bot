"""Command handlers — Telegram message processing.

Implements all bot commands (/jadwal, /deadline, /presensi, etc.)
and natural language query handling.
"""
import logging
import os
import re
from datetime import datetime, timedelta

from config import (
    KULINO_ACCOUNTS, MHS_ACCOUNTS, SCREENSHOT_JADWAL, SCREENSHOT_PRESENSI,
    SCREENSHOT_TUGAS, ALLOWED_CHAT_IDS, ADMIN_CHAT_ID, BOT_START_TIME,
    get_stats_snapshot, inc_stat, NAMA_PACAR, DASH_TOKEN,
)
from constants import HARI_ID, HARI_INDONESIA
from storage import (
    acleanup_expired_deadlines, aload_nilai_cache, aload_schedules, aload_tasks_deadlines, asave_chat_id, asave_nilai_cache,
    asave_tasks_deadlines,
)
from tg import send_message, send_photo
from utils import get_schedule_for, process_and_remind_deadlines
from aliases import aadd_alias, aremove_alias, aresolve_alias
import scrapers as tb
from scrapers import format_khs_message

logger = logging.getLogger(__name__)

def _resolve_target(chat_id: int | None, text: str) -> str:
    t = text.lower()
    if "azfa" in t or "pacar" in t:
        return "pacar"
    for key, acc in MHS_ACCOUNTS.items():
        if key in ("saya", "pacar"):
            continue
        name = acc.get("name", "").lower()
        if key.lower() in t or name in t:
            return key
    return "saya"

def get_account_info(target: str, chat_id: int | None, platform: str = "mhs") -> dict | None:
    """Resolve account credentials for a given query target.
    If target is 'pacar', return pacar account.
    If chat_id is registered in user_manager, return user credentials.
    Otherwise fallback to 'saya'.
    """
    if target == "pacar":
        return KULINO_ACCOUNTS["pacar"] if platform == "kulino" else MHS_ACCOUNTS["pacar"]

    if chat_id is not None:
        from user_manager import get_info
        info = get_info(chat_id)
        if info and platform in info:
            return info[platform]

    return KULINO_ACCOUNTS["saya"] if platform == "kulino" else MHS_ACCOUNTS["saya"]


# Refer back to bot.py functions or import them locally
# Using local import/dependencies inside handler to avoid circular imports.
_get_autopilot_func = None
_set_autopilot_func = None
_do_presensi_func = None
_login_kulino_func = None
_update_schedules_func = None
_check_materials_func = None
_syncgcal_func = None

def register_callbacks(get_autopilot_fn, set_autopilot_fn, do_presensi_fn, login_kulino_fn, update_schedules_fn, check_materials_fn, syncgcal_fn):
    global _get_autopilot_func, _set_autopilot_func, _do_presensi_func, _login_kulino_func, _update_schedules_func, _check_materials_func, _syncgcal_func
    _get_autopilot_func = get_autopilot_fn
    _set_autopilot_func = set_autopilot_fn
    _do_presensi_func = do_presensi_fn
    _login_kulino_func = login_kulino_fn
    _update_schedules_func = update_schedules_fn
    _check_materials_func = check_materials_fn
    _syncgcal_func = syncgcal_fn

async def handle_command(text: str, chat_id: int | None = None) -> None:
    from bot import get_today_holiday
    text = text.strip()
    t = text.lower()

    alias_cmd = await aresolve_alias(text)
    if alias_cmd:
        text = alias_cmd
        t = text.lower()

    if t in ("/start", "start", "halo", "hai", "hi"):
        from tg import make_inline_keyboard
        kb = make_inline_keyboard([
            [{"text": "📅 Jadwal", "callback_data": "cmd:jadwal"}, {"text": "📝 Tugas", "callback_data": "cmd:tugas"}],
            [{"text": "🤖 Presensi", "callback_data": "cmd:presensi"}, {"text": "📊 Nilai", "callback_data": "cmd:khs"}],
            [{"text": "🔔 Deadline", "callback_data": "cmd:deadline"}, {"text": "❓ Bantuan", "callback_data": "cmd:help"}]
        ])
        await send_message("Halo! 👋 Saya Asisten Akademik Udinus.\n\nPilih menu di bawah atau ketik `help` untuk info lebih lanjut.\n\n📝 Baru? Ketik `/register <NIM> <Password>` untuk daftar.", reply_markup=kb)

    elif t.startswith("/register") or t.startswith("register"):
        from user_manager import register as reg_user
        parts = text.split()
        if len(parts) < 4:
            await send_message("Gunakan: `/register <NIM> <Password_MHS> <Password_Kulino>`\n\n"
                               "Password MHS dan Kulino biasanya berbeda.\n"
                               "Contoh: `/register A11.2024.12345 RahasiaMHS RahasiaKulino`")
            return
        nim = parts[1]
        pwd_mhs = parts[2]
        pwd_kulino = parts[3]
        if chat_id is None:
            await send_message("❌ Error: chat_id tidak dikenal.")
            return
        if not re.match(r'^A\d+\.\d{4}\.\d{5}$', nim):
            await send_message("❌ Format NIM salah. Contoh: `A22.2024.03103`")
            return
        if reg_user(chat_id, nim=nim, password_mhs=pwd_mhs, password_kulino=pwd_kulino, name=nim):
            if chat_id not in ALLOWED_CHAT_IDS:
                ALLOWED_CHAT_IDS.add(chat_id)
                await asave_chat_id(chat_id)
            await send_message(f"✅ Berhasil daftar sebagai *{nim}*! Silakan coba `jadwal hari ini`")
        else:
            # Check if rate limited vs already registered
            from user_manager import _register_attempts, _MAX_REGISTER_ATTEMPTS
            cid = str(chat_id)
            if cid in _register_attempts and _register_attempts[cid][0] >= _MAX_REGISTER_ATTEMPTS:
                await send_message("⏳ Terlalu banyak percobaan. Coba lagi 5 menit lagi.")
            else:
                await send_message("❌ Gagal daftar. Kamu mungkin sudah terdaftar.")

    elif t.startswith("/unregister") or t.startswith("unregister"):
        from user_manager import unregister as unreg
        if chat_id and unreg(chat_id):
            await send_message("✅ Data kamu dihapus dari sistem.")
        else:
            await send_message("❌ Kamu belum terdaftar.")


    elif t in ("/help", "help", "bantuan"):
        await send_message(
            f"🆘 *Bantuan Presensi Udinus Bot*\n\n"
            f"📅 *Jadwal & Ujian*\n"
            f"`jadwal [hari]` — Jadwal kuliah hari ini/besok/senin\n"
            f"`jadwal gambar` — Screenshot jadwal hari ini (PNG)\n"
            f"`jadwal update` — Sinkron jadwal dari MHS\n"
            f"`ujian` / `ujian {NAMA_PACAR.lower()}` — Jadwal UTS/UAS\n"
            f"`libur` — Daftar hari libur nasional 2026\n\n"
            f"📝 *Tugas & Deadline*\n"
            f"`cek tugas` / `cek tugas {NAMA_PACAR.lower()}` — Tugas Kulino\n"
            f"`deadline` — List deadline tersimpan\n"
            f"`statustugas <nama>` — Tandai tugas selesai\n"
            f"`cleanup` — Hapus deadline yang sudah lewat\n\n"
            f"🤖 *Presensi*\n"
            f"`presensi` / `presensi {NAMA_PACAR.lower()}` — Presensi manual sekarang\n"
            f"`autopilot on/off` — Nyalakan/matikan autopilot\n"
            f"*(autopilot akan jalan otomatis 30 menit sebelum kelas)*\n\n"
            f"📊 *Info*\n"
            f"`status` — Status bot & statistik\n"
            f"`quickstats` / `ringkasan` — Ringkasan cepat\n"
            f"`nilai` / `khs` — Nilai & IP terbaru dari SiAdin\n"
            f"`tanggal` — Tanggal & hari ini\n"
            f"`logbook` — Riwayat presensi\n\n"
            f"🔧 *Settings*\n"
            f"`addalias <nama> <perintah>` — Bikin alias\n"
            f"`delalias <nama>` — Hapus alias\n\n"
            f"💡 *Tips:*\n"
            f"• Pagi jam 07:00 bot kirim reminder jadwal + screenshot otomatis\n"
            f"• Nilai baru terdeteksi otomatis — bot akan notify tanpa diminta!\n"
            f"• Tap ⏰ Snooze 10m di reminder kelas untuk tunda 10 menit\n"
            f"• Kirim `presensi {NAMA_PACAR.lower()}` buat absen akun {NAMA_PACAR}\n"
            f"• Kirim `ujian {NAMA_PACAR.lower()}` buat lihat jadwal ujian {NAMA_PACAR}\n"
            f"• Dashboard: `http://localhost:8787`\n"
            f"  (login pakai token dari admin)"
        )

    elif t in ("/status", "status", "stats"):
        uptime = datetime.now() - BOT_START_TIME
        d, r = uptime.days, uptime.seconds
        h, m = r // 3600, (r % 3600) // 60
        cache = await aload_tasks_deadlines()
        active = sum(1 for k in cache if k != "notified")
        snap = get_stats_snapshot()
        autopilot_status = _get_autopilot_func() if _get_autopilot_func else True
        await send_message(
            f"🤖 *Status*\n"
            f"⏱ {d}h {h}j {m}m\n"
            f"📥 {snap['messages_received']} | 📤 {snap['messages_sent']}\n"
            f"📝 {snap['tugas_checks']} | ✅ {snap['presensi_done']}\n"
            f"⚠ {snap['errors']} | 📋 {active}\n"
            f"👥 {len(ALLOWED_CHAT_IDS)} user\n"
            f"🤖 {'Aktif' if autopilot_status else 'Nonaktif'}"
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
        if _update_schedules_func:
            ok, msg = await _update_schedules_func()
            await send_message(f"{'✅' if ok else '❌'} {msg}")
        else:
            await send_message("❌ Fitur sinkron jadwal belum terdaftar.")

    elif t in ("/syncgcal", "sync gcal", "sinkron gcal"):
        await send_message("⏳ Sync jadwal dari Google Calendar...")
        if _syncgcal_func:
            for w in MHS_ACCOUNTS:
                ok, msg = await _syncgcal_func(w)
                await send_message(f"{MHS_ACCOUNTS[w]['name']}: {'✅' if ok else '❌'} {msg}")
        else:
            await send_message("❌ Fitur sync gcal belum terdaftar.")

    elif t.startswith("jadwal") and ("gambar" in t or "foto" in t or "image" in t or "screenshot" in t):
        await send_message("⏳ Render jadwal...")
        from render import render_jadwal_png
        from browser import get_page
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
            schedules = await aload_schedules()
            async with get_page() as page:
                ok = await render_jadwal_png(page, schedules, target_hari, SCREENSHOT_JADWAL)
            if ok and os.path.exists(SCREENSHOT_JADWAL):
                await send_photo(SCREENSHOT_JADWAL)
            else:
                await send_message("❌ Gagal render jadwal.")

    elif t.startswith("jadwal"):
        arg = t.replace("jadwal", "", 1).strip()
        for w in MHS_ACCOUNTS:
            await send_message(get_schedule_for(w, arg or "hari ini"))

    elif t in ("deadline", "tugas deadline", "list deadline"):
        cache = await aload_tasks_deadlines()
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
            cache = await aload_tasks_deadlines()
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
                await asave_tasks_deadlines(cache)
                await send_message(f"✅ *{name}* ditandai selesai")
            else:
                await send_message(f"❌ `{keyword}` tidak ditemukan")

    elif t in ("cleanup", "bersihkan", "hapus deadline"):
        removed = await acleanup_expired_deadlines()
        if removed:
            active = sum(1 for k in await aload_tasks_deadlines() if k != "notified")
            await send_message(f"🧹 {removed} dihapus. {active} tersisa.")
        else:
            await send_message("🧹 Tidak ada yang expired.")

    elif t in ("quickstats", "quick", "ringkasan", "stats cepat"):
        snap = get_stats_snapshot()
        cache = await aload_tasks_deadlines()
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
        schedules = await aload_schedules()
        total_classes = sum(
            len(schedules.get(w, {}).get(hari_id, [])) for w in MHS_ACCOUNTS
        ) if hari_id else 0
        today_holiday = get_today_holiday()
        holiday_text = f"🎉 {today_holiday}" if today_holiday else ""
        autopilot_status = _get_autopilot_func() if _get_autopilot_func else True

        msg = (
            f"📊 *Quick Stats*\n\n"
            f"🤖 Autopilot: {'Aktif' if autopilot_status else 'OFF'}\n"
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
        from config import LOG_DIR as CONFIG_LOG_DIR
        if not os.path.exists(CONFIG_LOG_DIR):
            await send_message("📓 Logbook kosong.")
        else:
            files = sorted([f for f in os.listdir(CONFIG_LOG_DIR) if f.endswith(".md")], reverse=True)[:3]
            if not files:
                await send_message("📓 Logbook kosong.")
            else:
                text = "📓 *Logbook* (3 terakhir):\n\n"
                for f in files:
                    p = os.path.join(CONFIG_LOG_DIR, f)
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
            await aadd_alias(name, cmd)
            await send_message(f"✅ Alias `/{name}` → `{cmd}`")

    elif t.startswith("delalias") or t.startswith("/delalias"):
        parts = text.split()
        if len(parts) < 2:
            await send_message("Gunakan: `delalias <nama>`")
        else:
            if await aremove_alias(parts[1].lower()):
                await send_message(f"✅ Alias `/{parts[1]}` dihapus.")
            else:
                await send_message(f"❌ Alias `{parts[1]}` tidak ditemukan.")

    elif t.startswith("/addchid") or t.startswith("addchid"):
        if ADMIN_CHAT_ID and chat_id != ADMIN_CHAT_ID:
            await send_message("❌ Hanya admin yang bisa menjalankan perintah ini.")
            return
        parts = text.split()
        if len(parts) < 2:
            await send_message("Gunakan: `/addchid <chat_id>`")
        else:
            try:
                new_id = int(parts[1])
                if new_id in ALLOWED_CHAT_IDS:
                    await send_message(f"ℹ️ {new_id} sudah ada.")
                else:
                    ALLOWED_CHAT_IDS.add(new_id)
                    await asave_chat_id(new_id)
                    await send_message(f"✅ {new_id} ditambahkan!")
            except ValueError:
                await send_message("Format salah")

    elif "tugas" in t or "cek tugas" in t:
        target = _resolve_target(chat_id, t)
        inc_stat("tugas_checks")
        await send_message("⏳ Cek tugas...")
        if _login_kulino_func:
            tugas = await _login_kulino_func(target)
            if tugas:
                rows = [f"{'No':<4} {'Tugas':<40} {'Deadline':<20}"]
                rows.append("-" * 70)
                for i, task_item in enumerate(tugas, 1):
                    rows.append(f"{i:<4} {task_item.get('name','?')[:38]:<40} {task_item.get('deadline','?')[:18]:<20}")
                await send_message(f"📝 *Tugas {KULINO_ACCOUNTS[target]['name']}*\n```\n" + "\n".join(rows) + "\n```")
                if os.path.exists(SCREENSHOT_TUGAS):
                    await send_photo(SCREENSHOT_TUGAS)
            else:
                await send_message("📭 Tidak ada tugas aktif.")
                if os.path.exists(SCREENSHOT_TUGAS):
                    await send_photo(SCREENSHOT_TUGAS)
            await process_and_remind_deadlines(tugas, target, send_message)
        else:
            await send_message("❌ Fitur cek tugas belum terdaftar.")

    elif t.startswith("cek") and "materi" in t:
        target = "pacar" if "azfa" in t or "pacar" in t else "saya"
        cmd_text = text.replace("cek materi", "", 1).replace("pacar", "").replace("azfa", "").strip()
        query = cmd_text if cmd_text else ""
        if _check_materials_func:
            await _check_materials_func(target, course_query=query)
        else:
            await send_message("❌ Fitur cek materi belum terdaftar.")

    elif "autopilot" in t:
        if _set_autopilot_func:
            if "nonaktif" in t or "off" in t:
                _set_autopilot_func(False)
                await send_message("🤖 Autopilot: NONAKTIF")
            else:
                _set_autopilot_func(True)
                await send_message("🤖 Autopilot: AKTIF")
        else:
            await send_message("❌ Autopilot settings callback not registered.")

    elif "presensi" in t or "hadir" in t:
        today_h = get_today_holiday()
        if today_h:
            await send_message(f"📢 Hari ini libur: *{today_h}*.\nTidak perlu presensi.")
            return
        target = _resolve_target(chat_id, t)
        if _do_presensi_func:
            ok, msg = await _do_presensi_func(target)
            if ok:
                inc_stat("presensi_done")
                await send_message(f"✅ Presensi {MHS_ACCOUNTS[target]['name']} berhasil!")
                await send_photo(SCREENSHOT_PRESENSI)
            else:
                await send_message(f"❌ {msg}")
        else:
            await send_message("❌ Fitur presensi belum terdaftar.")

    elif t in ("nilai", "khs", "cek nilai", "daftarnilai", "hasil studi"):
        target = _resolve_target(chat_id, t)
        account = MHS_ACCOUNTS[target]
        await send_message(f"⏳ Ambil KHS {account['name']}...")
        try:
            from browser import get_page
            from scrapers.kulino import scrape_kulino_tugas

            async with get_page(".browser_state/kulino_" + target + ".json") as page:
                await page.goto("https://mhs.dinus.ac.id/", wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2000)
                await page.fill("#username", account["nim"])
                await page.fill("#password", account["password"])
                async with page.expect_navigation(timeout=30000, wait_until="networkidle"):
                    await page.click("button:has-text('Masuk ke SiAdin')")
                khs = await tb.scrape_khs(page, account)
            await send_message(format_khs_message(khs, account["name"]))
            # local fallback to internal update method
            from bot import _save_khs_history
            await _save_khs_history(target, khs)
            cache = await aload_nilai_cache()
            cache[target] = {m["kdmk"]: m for m in khs["matkul"]}
            await asave_nilai_cache(cache)
        except Exception as e:
            logger.error(f"KHS error: {e}")
            await send_message(f"❌ Gagal ambil KHS: {e}")

    elif t.startswith("ujian"):
        target = _resolve_target(chat_id, t)
        await send_message(f"⏳ Cek jadwal ujian {MHS_ACCOUNTS[target]['name']}...")
        try:
            from browser import get_page

            async with get_page(".browser_state/mhs_" + target + ".json") as page:
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
        from bot import HOLIDAY_CACHE, get_today_holiday
        if not HOLIDAY_CACHE:
            from bot import load_holidays
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
        from scrapers import format_attendance_message

        target = "pacar" if "azfa" in t or "pacar" in t else "saya"
        now = datetime.now()
        year, month = now.year, now.month
        await send_message(f"⏳ Hitung statistik presensi {MHS_ACCOUNTS[target]['name']}...")
        schedules = await aload_schedules()
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
                schedules = await aload_schedules()
                if intent["intent"] == "jadwal":
                    reply = answer_jadwal(intent, schedules, "saya")
                else:
                    reply = answer_presensi(intent, schedules)
                await send_message(reply)
            elif intent["intent"] == "deadline":
                cache = await aload_tasks_deadlines()
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

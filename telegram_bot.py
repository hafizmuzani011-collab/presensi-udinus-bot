"""Telegram Bot Module - improved Kulino & SiAdin scraper with LLM fallback.

Menggantikan fungsi scraping di bot.py dengan selector CSS yang lebih robust
dan LLM fallback untuk interpretasi halaman yang kompleks.
"""

import json
import logging
import os
import re
from datetime import datetime

logger = logging.getLogger("telegram_bot")

KULINO_URL = "https://kulino.dinus.ac.id/"
MHS_URL = "https://mhs.dinus.ac.id/"
SCREENSHOT_PRESENSI = "bukti_presensi.png"
SCREENSHOT_TUGAS = "bukti_tugas.png"


async def scrape_kulino_tugas(page, kulino_akun: dict) -> list[dict]:
    """Scrape daftar tugas dari halaman Kulino setelah login.

    kulino_akun: dict dengan key 'nim', 'password', 'name'
    """
    logger.info(f"Scrape tugas Kulino untuk {kulino_akun['name']}...")

    tugas_list = []

    # Strategi 1: Cari upcoming events / timeline
    timeline_selectors = [
        "[data-region='timeline-view']",
        ".timeline",
        "#timeline",
        "[data-region='my-timeline']",
    ]
    for sel in timeline_selectors:
        el = await page.query_selector(sel)
        if el:
            logger.info(f"Timeline ditemukan via selector: {sel}")
            break

    logger.info(f"Login Kulino berhasil, navigasi ke Upcoming Events...")
    
    # Navigasi ke Upcoming Events untuk screenshot
    try:
        await page.goto("https://kulino.dinus.ac.id/calendar/view.php?view=upcoming", 
                       wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)
        logger.info(f"URL: {page.url}")
    except Exception as e:
        logger.warning(f"Fallback dashboard: {e}")
        await page.goto(KULINO_URL + "my/", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)

    # Ambil body text aja (simple evaluate, no regex in JS)
    body_text = await page.evaluate("() => document.body?.innerText || ''")
    logger.info(f"Body length: {len(body_text)}")

    # Parse di Python (reliable)
    regex_tasks = extract_tasks_from_text(body_text)
    tugas_list.extend(regex_tasks)
    
    # Screenshot full page
    try:
        await page.screenshot(path=SCREENSHOT_TUGAS, full_page=True)
        logger.info(f"Screenshot: {SCREENSHOT_TUGAS}")
    except Exception as e:
        logger.error(f"Screenshot gagal: {e}")

    # Deduplikasi by name
    seen = set()
    unik = []
    for t in tugas_list:
        key = t["name"].lower().strip()
        if key and key not in seen:
            seen.add(key)
            unik.append(t)

    if unik:
        logger.info(f"Scrape sukses: {len(unik)} tugas ditemukan")
    else:
        logger.info("Tidak ada tugas ditemukan via selector. Mencoba fallback...")

    return unik


async def scrape_siadin_presensi(page, mhs_akun: dict) -> tuple[bool, str]:
    """Login ke MHS (https://mhs.dinus.ac.id/) dan presensi otomatis.

    mhs_akun: dict dengan key 'nim', 'password', 'name'
    Return (success, message).
    """
    logger.info(f"Presensi online untuk {mhs_akun['name']}...")

    # Login ke MHS
    await page.goto(MHS_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)
    await page.fill("#username", mhs_akun["nim"])
    await page.fill("#password", mhs_akun["password"])
    async with page.expect_navigation(timeout=30000, wait_until="networkidle"):
        await page.click("button:has-text('Masuk ke SiAdin')")

    # Navigasi ke Presensi Online
    await page.goto("https://mhs.dinus.ac.id/akademik/presensiOnline",
                   wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(3000)

    logger.info(f"URL: {page.url}")

    # Cari list mata kuliah yang punya tombol presensi / hadiri
    # Biasanya berbentuk card/row dengan tombol "Hadir" / "Absen" / "Presensi"
    info = await page.evaluate("""() => {
        const items = [];
        // Coba selector umum: cari card/list-item dengan tombol
        const cards = document.querySelectorAll(".card, .list-group-item, table tr, .presensi-card, .row, .matkul-item");
        cards.forEach(card => {
            const text = card.innerText?.trim() || "";
            if (text.length < 5) return;
            const buttons = Array.from(card.querySelectorAll("button, a.btn, input[type=submit]")).map(b => ({
                text: b.innerText?.trim() || b.value || "",
                href: b.href || "",
                onclick: b.getAttribute('onclick') || ""
            }));
            if (buttons.length > 0) {
                items.push({ text: text.substring(0, 200), buttons });
            }
        });
        return { items, fullText: document.body?.innerText?.substring(0, 2000) || "" };
    }""")

    logger.info(f"Card presensi: {len(info['items'])}")
    for item in info['items'][:5]:
        logger.info(f"  Card text: {item['text'][:100]}")
        for btn in item['buttons']:
            logger.info(f"    Button: {btn['text'][:30]}")

    # Cari button "Hadir" / "Presensi" / "Absen"
    clicked = False
    for sel in [
        "button:has-text('Hadir')",
        "a:has-text('Hadir')",
        "button:has-text('Presensi')",
        "a:has-text('Presensi')",
        "button:has-text('Absen')",
        "a:has-text('Absen')",
        "button:has-text('Konfirmasi')",
        "a:has-text('Konfirmasi')",
    ]:
        try:
            btn = await page.query_selector(sel)
            if btn:
                logger.info(f"Klik tombol: {sel}")
                await btn.click()
                await page.wait_for_timeout(3000)
                await page.wait_for_load_state("networkidle", timeout=15000)
                clicked = True
                break
        except Exception as e:
            logger.warning(f"Selector {sel} gagal: {e}")

    # Screenshot bukti
    try:
        await page.screenshot(path=SCREENSHOT_PRESENSI, full_page=True)
        logger.info("Screenshot bukti presensi berhasil")
    except Exception as e:
        logger.error(f"Screenshot gagal: {e}")

    if clicked:
        return True, "Berhasil klik tombol presensi"
    elif info['items']:
        return False, f"Tidak ada tombol presensi yang bisa diklik. Items: {len(info['items'])}"
    else:
        return False, "Tidak ada presensi tersedia saat ini"


def extract_deadline_from_text(page_text: str) -> list[dict]:
    """Ekstrak deadline dari text mentah halaman (fallback regex)."""
    tasks = []
    lines = page_text.split("\n")
    current_name = ""
    current_course = ""

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Cari nama tugas
        name_match = re.search(r'(?:Tugas|Assignment|Capstone|Quiz|Project)\s*[\d:]*\s*[:-]?\s*(.+)', line, re.I)
        if name_match:
            current_name = name_match.group(1).strip()
            continue

        # Cari deadline
        deadline_match = re.search(
            r'(\d{1,2}\s+(?:Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember|January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}[\s,]+(?:\d{1,2}[:\.]\d{2}\s*(?:AM|PM)?))',
            line, re.I
        )
        if deadline_match and current_name:
            tasks.append({
                "name": current_name,
                "course": current_course or "",
                "deadline": deadline_match.group(0).strip(),
            })
            current_name = ""
            current_course = ""

        # Cari mata kuliah
        course_match = re.search(r'(?:Mata Kuliah|Course|MK)\s*[:]\s*(.+)', line, re.I)
        if course_match:
            current_course = course_match.group(1).strip()

    return tasks


def extract_tasks_from_text(body_text: str, body_html: str = "") -> list[dict]:
    """Ekstrak tugas dari body text halaman Kulino pakai Python regex.

    Format yang ditemukan dari test:
    "Upload catatan materi Konfigurasi Mikrotik Router OS is due\nTomorrow, 12:00 AM"
    "Capstone 11: Webservice Server is due\nWednesday, 24 June, 12:30 PM"
    """
    results = []
    seen = set()

    # Pattern 1: "X is due\nDATE" (newline between name and deadline)
    matches = re.findall(r"([A-Z][^\n]{5,120})\s+is due\s*\n\s*([^\n]{5,60})", body_text)
    for name, deadline in matches:
        name_clean = name.strip()
        deadline_clean = deadline.strip()
        key = name_clean.lower()
        if name_clean and key not in seen:
            seen.add(key)
            results.append({
                "name": name_clean,
                "course": "",
                "deadline": deadline_clean,
                "link": "",
            })

    # Pattern 2: "X is due" alone (deadline in next line or same line)
    if not results:
        matches = re.findall(r"([A-Za-z][^\\n]{5,120})\s+is due", body_text)
        for name in matches:
            name_clean = name.strip()
            key = name_clean.lower()
            if name_clean and key not in seen:
                seen.add(key)
                results.append({
                    "name": name_clean,
                    "course": "",
                    "deadline": "",
                    "link": "",
                })

    # Pattern 3: Extract course names from HTML (for context)
    if body_html:
        courses = re.findall(r'course-name[^>]*>([^<]+)', body_html)
        for c in courses[:5]:
            c = c.strip()
            if c and len(c) < 60:
                # Add as context
                pass

    return results


async def login_mhs_and_scrape_jadwal(page, mhs_akun: dict, semester: str = "2025-2026 Genap") -> dict:
    """Login ke https://mhs.dinus.ac.id/, ambil jadwal dari halaman akademik.

    Returns dict {hari: [[jam, matkul, ruang], ...]}
    """

    jadwal_pattern = re.compile(
        r'^(SENIN|SELASA|RABU|KAMIS|JUMAT|SABTU|MINGGU)\s+'
        r'(\d+[.:]\d+\s*-\s*\d+[.:]\d+)\s+'
        r'(.+)$', re.I)

    SKIP = {"dashboard", "akademik", "biodata", "keuangan", "dokumen",
            "tugas akhir", "lainnya", "keluar", "krs", "khs", "jadwal ujian",
            "presensi online", "daftar nilai", "matrikulasi", "semester antara",
            "sisa masa studi ideal", "unduh krs", "arrow_back",
            "pengumuman terkini", "pengumuman kelas", "statistik akademik"}

    logger.info(f"Login MHS & scrape jadwal untuk {mhs_akun['name']}...")

    await page.goto(MHS_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)
    await page.fill("#username", mhs_akun["nim"])
    await page.fill("#password", mhs_akun["password"])
    async with page.expect_navigation(timeout=30000, wait_until="networkidle"):
        await page.click("button:has-text('Masuk ke SiAdin')")
    await page.goto(f"{MHS_URL}akademik", wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(3000)

    body = await page.inner_text("body")
    lines = [l.strip() for l in body.split("\n")]

    jadwal = {h: [] for h in
              ["senin", "selasa", "rabu", "kamis", "jumat", "sabtu", "minggu"]}
    sem_mode = False
    course_name = None

    for line in lines:
        if not line:
            continue
        if semester in line:
            sem_mode = True
            course_name = None
            continue
        if not sem_mode:
            continue
        if any(x in line for x in ("2025-2026 Ganjil", "2024-2025")):
            sem_mode = False
            continue
        if any(x in line for x in ("KDMK:", "KLPK:", "SKS", "%", "Unduh")) or line == "-":
            continue

        m = jadwal_pattern.match(line)
        if m:
            if not course_name:
                continue
            hari_en = m.group(1).lower()
            jam = m.group(2).replace(".", ":")
            ruang = m.group(3).strip()
            hari_id = {"senin": "senin", "selasa": "selasa", "rabu": "rabu",
                       "kamis": "kamis", "jumat": "jumat", "sabtu": "sabtu",
                       "minggu": "minggu"}.get(hari_en)
            if hari_id:
                entry = [jam, course_name, ruang]
                if entry not in jadwal[hari_id]:
                    jadwal[hari_id].append(entry)
            continue

        if (not any(c.isdigit() for c in line)
            and line.isupper() and len(line) > 4
            and line.lower() not in SKIP):
            course_name = line

    logger.info(f"Jadwal scraped: {sum(len(v) for v in jadwal.values())} slot")
    return jadwal

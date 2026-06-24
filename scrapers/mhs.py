"""MHS scraper — jadwal kuliah."""
import logging
import re
from browser import login_siadin_portal

logger = logging.getLogger(__name__)

MHS_URL = "https://mhs.dinus.ac.id/"


async def login_mhs_and_scrape_jadwal(page, mhs_akun: dict, semester: str = "2025-2026 Genap") -> dict:
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

    await login_siadin_portal(page, mhs_akun["nim"], mhs_akun["password"])
    await page.goto(f"{MHS_URL}akademik", wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(3000)

    body = await page.inner_text("body")
    lines = [line.strip() for line in body.split("\n")]

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

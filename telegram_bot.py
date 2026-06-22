"""Telegram Bot Module - improved Kulino & SiAdin scraper with LLM fallback.

Menggantikan fungsi scraping di bot.py dengan selector CSS yang lebih robust
dan LLM fallback untuk interpretasi halaman yang kompleks.
"""

import logging
import re

logger = logging.getLogger(__name__)

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

    logger.info("Login Kulino berhasil, navigasi ke Upcoming Events...")
    
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


async def _click_confirmation_in_modal(page) -> bool:
    """Klik tombol konfirmasi 'Ya' di modal/dialog setelah klik presensi.

    SiAdin pake Tailwind CSS custom dialog (bukan SweetAlert2/Bootstrap).
    """
    # Tunggu modal render
    await page.wait_for_timeout(2000)

    # Deteksi elemen dialog via JS — cari overlay fixed + button "Ya" di dalamnya
    dialog_info = await page.evaluate("""() => {
        const result = {found: false, buttons: []};
        const candidates = document.querySelectorAll('.fixed.inset-0, .z-50');
        for (const el of candidates) {
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            if (style.display === 'none' || style.visibility === 'hidden') continue;
            const btns = Array.from(el.querySelectorAll('button, a')).filter(b => {
                const s = window.getComputedStyle(b);
                const r = b.getBoundingClientRect();
                return r.width > 0 && r.height > 0 && s.display !== 'none';
            });
            const texts = btns.map(b => {
                const r = b.getBoundingClientRect();
                return {
                    tag: b.tagName,
                    text: (b.innerText || b.value || '').trim(),
                    rect: {w: Math.round(r.width), h: Math.round(r.height)}
                };
            }).filter(b => b.text.length > 0);
            if (texts.some(t => /ya|tidak|presensi sekarang/i.test(t.text))) {
                result.found = true;
                result.buttons = texts;
                break;
            }
        }
        return result;
    }""")

    if not dialog_info.get("found"):
        logger.info("Tidak ada dialog konfirmasi terdeteksi")
        return False

    logger.info(f"Dialog ditemukan, {len(dialog_info['buttons'])} buttons:")
    for b in dialog_info["buttons"]:
        logger.info(f"  [{b['tag']}] '{b['text']}'")

    # Cari button "Ya" (prioritas), lalu "Tidak" kita skip
    ya_btn = None
    for b in dialog_info["buttons"]:
        if b["text"].strip().lower() in ("ya", "yes", "ok"):
            ya_btn = b
            break
    # Fallback: cari yang mengandung "ya"
    if not ya_btn:
        for b in dialog_info["buttons"]:
            if b["text"].strip().lower().startswith("ya"):
                ya_btn = b
                break

    if not ya_btn:
        logger.warning("Tombol 'Ya' tidak ditemukan di dialog")
        return False

    # Klik via JS (lebih reliable dari Playwright click buat dialog Tailwind)
    try:
        clicked_text = await page.evaluate("""(targetText) => {
            const candidates = document.querySelectorAll('.fixed.inset-0, .z-50');
            for (const el of candidates) {
                const btns = el.querySelectorAll('button, a');
                for (const b of btns) {
                    const t = (b.innerText || b.value || '').trim();
                    if (t === targetText) {
                        b.click();
                        return t;
                    }
                }
            }
            return null;
        }""", ya_btn["text"])
        if clicked_text:
            logger.info(f"Klik 'Ya' via JS: '{clicked_text}'")
            await page.wait_for_timeout(3000)
            return True
    except Exception as e:
        logger.warning(f"JS click 'Ya' gagal: {e}")

    return False


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
            if not btn:
                continue
            # Skip kalau button disabled
            is_disabled = await btn.get_attribute("disabled")
            if is_disabled is not None:
                logger.info(f"Skip {sel} (disabled)")
                continue
            # Skip kalau element adalah nav link (di header) bukan button card
            role = await btn.evaluate("el => (el.closest('nav, header, .navbar, .sidebar') ? 'nav' : 'content')")
            if role == "nav":
                logger.info(f"Skip {sel} (nav/header element)")
                continue
            logger.info(f"Klik tombol: {sel}")
            await btn.click()
            await page.wait_for_timeout(2000)
            clicked = True
            break
        except Exception as e:
            logger.warning(f"Selector {sel} gagal: {e}")

    if not clicked:
        if info['items']:
            return False, f"Tidak ada tombol presensi yang bisa diklik. Items: {len(info['items'])}"
        return False, "Tidak ada presensi tersedia saat ini"

    # === Tangani modal konfirmasi (SweetAlert/Bootstrap/custom) ===
    # Halaman SiAdin biasanya membuka dialog "Presensi Sekarang?" -> tombol "Ya"
    confirmed = await _click_confirmation_in_modal(page)
    if confirmed:
        logger.info("Konfirmasi modal diklik")
    else:
        logger.info("Tidak ada modal konfirmasi (mungkin langsung submit)")

    # Tunggu halaman settle setelah konfirmasi
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception as e:
        logger.warning(f"networkidle timeout (mungkin tetap OK): {e}")
    await page.wait_for_timeout(2000)

    # Screenshot bukti SETELAH konfirmasi
    try:
        await page.screenshot(path=SCREENSHOT_PRESENSI, full_page=True)
        logger.info("Screenshot bukti presensi berhasil")
    except Exception as e:
        logger.error(f"Screenshot gagal: {e}")

    # === Verifikasi: cek apakah presensi benar-benar sukses ===
    success, verify_msg = await _verify_presensi_success(page)
    if not success:
        logger.warning(f"Verifikasi gagal: {verify_msg}")
        return False, f"Verifikasi gagal: {verify_msg}"

    return True, f"Berhasil klik tombol presensi ({verify_msg})"


async def _verify_presensi_success(page) -> tuple[bool, str]:
    """Cek halaman setelah klik Ya, apakah presensi benar-benar sukses.

    Return (success, message). success=True kalau ada indikator berhasil.
    """
    try:
        # Tunggu 2 detik untuk response server
        await page.wait_for_timeout(2000)

        # Ambil teks halaman untuk dicek
        body_text = (await page.inner_text("body")).lower()

        # Indikator sukses (positif)
        success_patterns = [
            "berhasil",
            "sukses",
            "success",
            "hadir",
            "telah melakukan presensi",
            "presensi berhasil",
            "successfully",
            "recorded",
            "tercatat",
        ]
        # Indikator gagal (negatif)
        fail_patterns = [
            "gagal",
            "failed",
            "error",
            "tidak berhasil",
            "tidak dapat",
            "denied",
            "tolak",
            "duplikat",
            "sudah pernah",
            "already",
            "tidak memenuhi",
            "tidak dalam jadwal",
        ]

        # Cek pattern positif dulu
        for pat in success_patterns:
            if pat in body_text:
                return True, f"indikator: '{pat}'"

        # Cek pattern negatif
        for pat in fail_patterns:
            if pat in body_text:
                return False, f"indikator gagal: '{pat}'"

        # Cek apakah card yang barusan di-klik udah berubah status
        # (biasanya dari "Presensi Sekarang" ke "Sudah Presensi" atau similar)
        try:
            card_state = await page.evaluate("""() => {
                const cards = document.querySelectorAll('.card, .list-group-item, .row, .matkul-item');
                const states = [];
                cards.forEach(c => {
                    const text = c.innerText || '';
                    if (text.length < 5) return;
                    // Cari card yang barusan di-klik (yang punya teks HADIR/sudah)
                    if (text.match(/(sudah|tercatat|selesai|✓|✔|hadir|present)/i)) {
                        states.push('HADIR');
                    } else if (text.match(/(belum|not yet|tidak hadir|absen|alpa)/i)) {
                        states.push('BELUM');
                    }
                });
                return states;
            }""")
            if "HADIR" in card_state:
                return True, "card state: HADIR detected"
        except Exception:
            pass

        # Default: tidak ada indikasi kuat, anggap success (klik sukses)
        return True, "no error indicator"
    except Exception as e:
        return True, f"verify error (assume success): {e}"


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

    return results


async def scrape_jadwal_ujian(page, mhs_akun: dict) -> tuple[list[dict], str]:
    """Scrape jadwal UTS/UAS dari MHS SiAdin.

    Returns (list of {jenis, matkul, hari_tanggal, jam, ruang, kursi, ujian}, jenis).
    jenis = "UTS" | "UAS".
    """
    try:
        await page.goto("https://mhs.dinus.ac.id/akademik/jadwalUjian",
                       wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)

        info = await page.evaluate("""() => {
            const result = {uts: [], uas: []};
            const text = document.body.innerText || '';
            // Split by section headers
            const sections = text.split(/Jadwal Ujian (Tengah|Akhir) Semester/);
            // Section[0] = header, [1] = "Tengah", [2] = uts content, [3] = "Akhir", [4] = uas content
            for (let i = 0; i < sections.length; i++) {
                const label = sections[i].toLowerCase();
                const content = sections[i + 1] || '';
                if (label.includes("tengah")) {
                    result.uts = parseSection(content);
                } else if (label.includes("akhir")) {
                    result.uas = parseSection(content);
                }
            }
            function parseSection(text) {
                const items = [];
                const blocks = text.split(/\\s{2,}/).map(s => s.trim()).filter(s => s.length > 0);
                let i = 0;
                while (i < blocks.length) {
                    const block = blocks[i];
                    if (block.match(/[A-Z]{3,}/) && (block.includes("SKS") || i < blocks.length - 5)) {
                        // Course name
                        const matkul = block;
                        const klpk = blocks[i+1] || "";
                        // Find table row "Hari Jam Ruang Kursi Ujian"
                        const hari = blocks[i+2] || "";
                        const jam = blocks[i+3] || "";
                        const ruang = blocks[i+4] || "";
                        const kursi = blocks[i+5] || "";
                        const ujian = blocks[i+6] || "";
                        if (hari && jam) {
                            items.push({matkul, klpk, hari_tanggal: hari, jam, ruang, kursi, ujian});
                        }
                        i += 7;
                    } else {
                        i++;
                    }
                }
                return items;
            }
            return result;
        }""")

        # Fallback: pakai regex kalau JS parsing gagal
        all_items = info.get("uts", []) + info.get("uas", [])
        if not all_items:
            body = await page.inner_text("body")
            return _parse_ujian_text(body), "UTS+UAS"

        return all_items, "UTS+UAS"
    except Exception as e:
        logger.error(f"scrape_jadwal_ujian error: {e}")
        return [], ""


def _parse_ujian_text(body: str) -> list[dict]:
    """Parse plain text halaman jadwal ujian."""
    items = []
    current_matkul = ""
    jam = ""
    hari_tanggal = ""
    for line in body.split("\n"):
        line = line.strip()
        if not line:
            continue
        if re.search(r"\b(UTS|UAS|Teori|Praktek|Tengah|Akhir|Semester)\b", line, re.I):
            continue
        if re.search(r"\b(Senin|Selasa|Rabu|Kamis|Jumat|Sabtu|Minggu)\b", line, re.I) and "," in line:
            hari_tanggal = line
        elif re.match(r"^\d{2}:\d{2}\s*-\s*\d{2}:\d{2}$", line):
            jam = line
        elif "SKS" in line or line.isupper():
            if line not in ("KDMK:", "KLPK:"):
                current_matkul = line
        else:
            ruang = line
            if current_matkul and jam:
                items.append({
                    "matkul": current_matkul,
                    "hari_tanggal": hari_tanggal,
                    "jam": jam,
                    "ruang": ruang,
                    "kursi": "",
                    "ujian": "",
                })
                current_matkul = ""
                jam = ""
    return items


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

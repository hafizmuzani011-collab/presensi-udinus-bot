"""SiAdin scraper — presensi online, jadwal ujian."""
import logging
import re
from constants import PRESENSI_FAIL_PATTERNS, PRESENSI_SUCCESS_PATTERNS, PRESENSI_SUCCESS_SELECTOR
from config import SCREENSHOT_PRESENSI

logger = logging.getLogger(__name__)

MHS_URL = "https://mhs.dinus.ac.id/"


async def _click_confirmation_in_modal(page) -> bool:
    await page.wait_for_timeout(2000)

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

    ya_btn = None
    for b in dialog_info["buttons"]:
        if b["text"].strip().lower() in ("ya", "yes", "ok"):
            ya_btn = b
            break
    if not ya_btn:
        for b in dialog_info["buttons"]:
            if b["text"].strip().lower().startswith("ya"):
                ya_btn = b
                break

    if not ya_btn:
        logger.warning("Tombol 'Ya' tidak ditemukan di dialog")
        return False

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


async def _verify_presensi_success(page) -> tuple[bool, str]:
    try:
        await page.wait_for_timeout(2000)

        try:
            success_el = await page.query_selector(PRESENSI_SUCCESS_SELECTOR)
            if success_el:
                return True, "CSS success indicator found"
        except Exception:
            pass

        try:
            content_el = await page.query_selector("main, #content, .container, .presensi-card, .card")
            if content_el:
                body_text = (await content_el.inner_text()).lower()
            else:
                body_text = (await page.inner_text("body")).lower()
        except Exception:
            body_text = (await page.inner_text("body")).lower()

        for pat in PRESENSI_SUCCESS_PATTERNS:
            if pat in body_text:
                return True, f"indikator: '{pat}'"

        for pat in PRESENSI_FAIL_PATTERNS:
            if pat in body_text:
                logger.warning(f"Presensi verification: fail pattern '{pat}' found")
                return False, f"indikator gagal: '{pat}'"

        try:
            card_state = await page.evaluate("""() => {
                const cards = document.querySelectorAll('.card, .list-group-item, .row, .matkul-item');
                const states = [];
                cards.forEach(c => {
                    const text = c.innerText || '';
                    if (text.length < 5) return;
                    if (text.match(/(sudah|tercatat|selesai|\\u2713|\\u2714|hadir|present)/i)) {
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

        try:
            has_status_btn = await page.evaluate("""() => {
                const btns = document.querySelectorAll('button, a');
                for (const b of btns) {
                    const t = (b.innerText || '').toLowerCase();
                    if (/(sudah|selesai|berhasil|tidak bisa)/i.test(t)) return true;
                }
                return false;
            }""")
            if has_status_btn:
                return True, "status button detected"
        except Exception:
            pass

        logger.info("Presensi verification: no clear indicator, marking uncertain")
        return False, "verifikasi tidak pasti — tidak ada indikator jelas"
    except Exception as e:
        logger.error(f"Presensi verification error: {e}")
        return False, f"verifikasi error: {e}"


async def scrape_siadin_presensi(page, mhs_akun: dict) -> tuple[bool, str]:
    logger.info(f"Presensi online untuk {mhs_akun['name']}...")
    from browser import login_siadin_portal

    await login_siadin_portal(page, mhs_akun["nim"], mhs_akun["password"])

    await page.goto("https://mhs.dinus.ac.id/akademik/presensiOnline",
                   wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(3000)

    logger.info(f"URL: {page.url}")

    info = await page.evaluate("""() => {
        const items = [];
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
            is_disabled = await btn.get_attribute("disabled")
            if is_disabled is not None:
                logger.info(f"Skip {sel} (disabled)")
                continue
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

    confirmed = await _click_confirmation_in_modal(page)
    if confirmed:
        logger.info("Konfirmasi modal diklik")
    else:
        logger.info("Tidak ada modal konfirmasi (mungkin langsung submit)")

    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception as e:
        logger.warning(f"networkidle timeout (mungkin tetap OK): {e}")
    await page.wait_for_timeout(2000)

    try:
        await page.screenshot(path=SCREENSHOT_PRESENSI, full_page=True)
        logger.info("Screenshot bukti presensi berhasil")
    except Exception as e:
        logger.error(f"Screenshot gagal: {e}")

    success, verify_msg = await _verify_presensi_success(page)
    if not success:
        logger.warning(f"Verifikasi gagal: {verify_msg}")
        return False, f"Verifikasi gagal: {verify_msg}"

    return True, f"Berhasil klik tombol presensi ({verify_msg})"


async def scrape_jadwal_ujian(page, mhs_akun: dict) -> tuple[list[dict], str]:
    try:
        await page.goto("https://mhs.dinus.ac.id/akademik/jadwalUjian",
                       wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)

        info = await page.evaluate("""() => {
            const result = {uts: [], uas: []};
            const text = document.body.innerText || '';
            const sections = text.split(/Jadwal Ujian (Tengah|Akhir) Semester/);
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
                        const matkul = block;
                        const klpk = blocks[i+1] || "";
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

        all_items = info.get("uts", []) + info.get("uas", [])
        if not all_items:
            body = await page.inner_text("body")
            return _parse_ujian_text(body), "UTS+UAS"

        return all_items, "UTS+UAS"
    except Exception as e:
        logger.error(f"scrape_jadwal_ujian error: {e}")
        return [], ""


def _parse_ujian_text(body: str) -> list[dict]:
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

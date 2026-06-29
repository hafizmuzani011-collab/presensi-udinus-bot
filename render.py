"""Render jadwal ke PNG via Playwright (reuse existing browser context)."""
import logging
from datetime import datetime

from constants import HARI_INDONESIA, HARI_ORDER

logger = logging.getLogger(__name__)

_VALID_HARI = set(HARI_ORDER)


def _build_html(schedules: dict, hari_id: str, tanggal_str: str) -> str:
    """Build HTML page for jadwal hari tertentu."""
    from config import MHS_ACCOUNTS
    rows_html = ""
    total = 0
    for who in MHS_ACCOUNTS:
        nama = MHS_ACCOUNTS[who]["name"]
        slots = schedules.get(who, {}).get(hari_id, [])
        if not slots:
            rows_html += (
                '<div class="row">'
                f'<div class="name">{esc(nama)}</div>'
                '<div class="empty">Tidak ada kelas &#x1F389;</div>'
                '</div>'
            )
            continue
        for jam, mk, ruang in slots:
            jam_mulai = jam.split("-")[0].strip() if "-" in jam else jam
            rows_html += (
                f'<div class="row">'
                f'<div class="time">{esc(jam_mulai)}</div>'
                f'<div class="name">{esc(nama)}</div>'
                f'<div class="course">{esc(mk)}</div>'
                f'<div class="room">&#x1F3EB; {esc(ruang)}</div>'
                '</div>'
            )
            total += 1

    hari_title = HARI_INDONESIA.get(hari_id, hari_id.title())
    jam_now = datetime.now().strftime("%H:%M WIB")
    return f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
* {{ margin:0; padding:0; box-sizing:border-box; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
body {{ background:linear-gradient(135deg,#667eea 0%,#764ba2 100%); padding:32px; min-height:100vh; }}
.card {{ background:white; border-radius:16px; padding:28px 32px; max-width:720px; margin:0 auto; box-shadow:0 20px 60px rgba(0,0,0,0.3); }}
.header {{ border-bottom:2px solid #f0f0f0; padding-bottom:16px; margin-bottom:20px; }}
.title {{ font-size:22px; font-weight:700; color:#1a202c; }}
.subtitle {{ font-size:14px; color:#718096; margin-top:4px; }}
.count {{ display:inline-block; background:#667eea; color:white; padding:4px 12px; border-radius:12px; font-size:12px; font-weight:600; margin-top:8px; }}
.row {{ display:grid; grid-template-columns:70px 100px 1fr 130px; gap:12px; padding:12px 8px; border-bottom:1px solid #f7fafc; align-items:center; }}
.row:last-child {{ border-bottom:none; }}
.time {{ font-weight:700; color:#667eea; font-size:15px; }}
.name {{ font-weight:600; color:#2d3748; font-size:13px; }}
.course {{ color:#2d3748; font-size:14px; font-weight:500; }}
.room {{ color:#718096; font-size:12px; }}
.empty {{ color:#a0aec0; font-style:italic; padding:8px; grid-column:2/-1; }}
.footer {{ margin-top:20px; padding-top:16px; border-top:1px solid #f0f0f0; font-size:11px; color:#a0aec0; text-align:center; }}
</style></head><body>
<div class="card">
  <div class="header">
    <div class="title">&#x1F4C5; Jadwal Kuliah</div>
    <div class="subtitle">{hari_title} &middot; {tanggal_str}</div>
    <div class="count">{total} kelas hari ini</div>
  </div>
  {rows_html}
  <div class="footer">Presensi Udinus Bot &middot; generated {jam_now}</div>
</div>
</body></html>'''


def esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


async def render_jadwal_png(page, schedules: dict, hari_id: str, output_path: str) -> bool:
    """Render jadwal hari tertentu ke PNG. Return True kalau sukses."""
    tanggal_str = datetime.now().strftime("%d-%m-%Y")
    html = _build_html(schedules, hari_id, tanggal_str)
    try:
        await page.set_content(html, wait_until="domcontentloaded")
        await page.wait_for_timeout(300)
        card = await page.query_selector(".card")
        if card:
            await card.screenshot(path=output_path)
        else:
            await page.screenshot(path=output_path, full_page=True)
        logger.info(f"Jadwal PNG rendered: {output_path}")
        return True
    except Exception as e:
        logger.error(f"Render jadwal PNG gagal: {e}")
        return False


async def get_today_jadwal_png(schedules: dict, output_path: str) -> bool:
    """Convenience: render jadwal hari ini ke PNG (create new page)."""
    from browser import get_page
    from constants import HARI_ID

    day_name = datetime.now().strftime("%A").lower()
    hari_id = HARI_ID.get(day_name, "")
    if not hari_id:
        return False
    async with get_page() as page:
        return await render_jadwal_png(page, schedules, hari_id, output_path)

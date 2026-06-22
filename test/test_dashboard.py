"""Verify dashboard pages via hash URLs."""
import asyncio
from playwright.async_api import async_playwright

PAGES = ["deadline", "jadwal", "log", "calendar", "history", "settings"]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(f"PAGE: {e}"))
        page.on("console", lambda m: errors.append(f"JS:{m.type}:{m.text}") if m.type=="error" else None)

        # Semua halaman via hash (user experience)
        for pg in PAGES:
            await page.goto(f"http://127.0.0.1:8787/?token=presensi123#{pg}", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(3000)
            text = await page.evaluate(f"document.getElementById('page-{pg}')?.innerText || 'NOT FOUND'")
            hidden = await page.evaluate(f"document.getElementById('page-{pg}')?.classList.contains('hidden')")
            ready = not hidden
            print(f"  {pg:12} | {'OK' if ready else 'X'} | {(text or '')[:60]}")

        print(f"\nErrors: {len(errors)}")
        for e in errors[:5]:
            print(f"  {e[:200]}")

        # Verify deadline actually has data
        deadline = await page.evaluate("document.getElementById('dw')?.innerText || ''")
        print(f"\nDashboard deadline widget: {(deadline or 'EMPTY')[:80]}")

        await browser.close()

asyncio.run(main())

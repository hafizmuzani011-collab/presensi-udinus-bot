"""Verify dashboard pages via hash URLs. Integration test — butuh bot running."""
import asyncio
import os
from playwright.async_api import async_playwright

PAGES = ["deadline", "jadwal", "log", "calendar", "history", "settings"]
TOKEN = os.environ.get("DASH_TOKEN", "")

async def main():
    if not TOKEN:
        print("DASH_TOKEN env not set, skipping")
        return
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(f"PAGE: {e}"))
        page.on("console", lambda m: errors.append(f"JS:{m.type}:{m.text}") if m.type=="error" else None)

        for pg in PAGES:
            await page.goto(f"http://127.0.0.1:8787/?token={TOKEN}#{pg}", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(3000)
            text = await page.evaluate(f"document.getElementById('page-{pg}')?.innerText || 'NOT FOUND'")
            hidden = await page.evaluate(f"document.getElementById('page-{pg}')?.classList.contains('hidden')")
            ready = not hidden
            print(f"  {pg:12} | {'OK' if ready else 'X'} | {(text or '')[:60]}")

        print(f"\nErrors: {len(errors)}")
        for e in errors[:5]:
            print(f"  {e[:200]}")

        deadline = await page.evaluate("document.getElementById('dw')?.innerText || ''")
        print(f"\nDashboard deadline widget: {(deadline or 'EMPTY')[:80]}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())

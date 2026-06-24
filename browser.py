"""Shared browser context — persistent context, thread-safe, auto-reconnect."""
import asyncio
import logging
from contextlib import asynccontextmanager

from playwright.async_api import async_playwright, Browser

logger = logging.getLogger(__name__)

# Global state
_playwright = None
_browser: Browser | None = None
_lock = asyncio.Lock()


async def _ensure_browser() -> Browser:
    """Lazy-init browser singleton, thread-safe, with auto-reconnect."""
    global _playwright, _browser

    async with _lock:
        if _browser is not None and _browser.is_connected():
            return _browser

        if _browser is not None:
            try:
                await _browser.close()
            except Exception:
                pass
            _browser = None

        if _playwright is not None:
            try:
                await _playwright.stop()
            except Exception:
                pass
            _playwright = None

        max_retries = 3
        for attempt in range(max_retries):
            try:
                _playwright = await async_playwright().start()
                _browser = await _playwright.chromium.launch(
                    headless=True,
                    args=["--disable-dev-shm-usage", "--no-sandbox"]
                )
                logger.info("Browser launched" + (f" (retry {attempt+1})" if attempt > 0 else ""))
                return _browser
            except Exception as e:
                logger.error(f"Browser launch attempt {attempt+1}/{max_retries}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(5)

        raise RuntimeError("Browser gagal launch setelah retry")


@asynccontextmanager
async def get_page():
    """Context manager: yield new page in a fresh ISOLATED context, auto-close context."""
    context = None
    page = None
    try:
        browser = await _ensure_browser()
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()
        yield page
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass
        if context:
            try:
                await context.close()
            except Exception:
                pass


async def close_browser():
    """Shutdown browser (panggil saat bot exit)."""
    global _playwright, _browser

    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright:
        try:
            await _playwright.stop()
        except Exception:
            pass
        _playwright = None
    logger.info("Browser closed")


async def login_siadin_portal(page, nim: str, password: str, wait_selector: str = "") -> bool:
    """Helper login terpusat untuk portal SiAdin / MHS Dinus."""
    try:
        await page.goto("https://mhs.dinus.ac.id/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)
        await page.fill("#username", nim)
        await page.fill("#password", password)
        async with page.expect_navigation(timeout=30000, wait_until="networkidle"):
            await page.click("button:has-text('Masuk ke SiAdin')")
        if wait_selector:
            await page.wait_for_selector(wait_selector, timeout=10000)
        return True
    except Exception as e:
        logger.error(f"login_siadin_portal failed: {e}")
        return False


"""Shared browser context untuk speed up - reuse antar request."""
import asyncio
import logging
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

# Global state
_playwright = None
_browser: Browser | None = None
_context: BrowserContext | None = None
_lock = asyncio.Lock()


async def get_browser(max_retries=3) -> Browser:
    """Lazy init browser singleton dengan auto-reconnect."""
    global _playwright, _browser

    # Cek apakah perlu reconnect
    if _browser is not None and not _browser.is_connected():
        logger.warning("Browser disconnected, reconnecting...")
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

    async with _lock:
        for attempt in range(max_retries):
            if _browser is not None and _browser.is_connected():
                return _browser
            try:
                _playwright = await async_playwright().start()
                _browser = await _playwright.chromium.launch(
                    headless=True,
                    args=["--disable-dev-shm-usage", "--no-sandbox"]
                )
                logger.info("Browser launched" + (f" (retry {attempt+1})" if attempt > 0 else ""))
                return _browser
            except Exception as e:
                logger.error(f"Browser launch attempt {attempt+1}/{max_retries} gagal: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(5)
        raise RuntimeError("Browser gagal launch setelah retry")


@asynccontextmanager
async def get_page():
    """Context manager: yield page, auto close per request, auto-reconnect."""
    browser = await get_browser()
    ctx = None
    page = None
    try:
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await ctx.new_page()
        yield page
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass
        if ctx:
            try:
                await ctx.close()
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

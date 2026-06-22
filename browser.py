"""Shared browser context untuk speed up - reuse antar request."""
import asyncio
import logging
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger("telegram_bot")

# Global state
_playwright = None
_browser: Browser | None = None
_context: BrowserContext | None = None
_lock = asyncio.Lock()


async def get_browser() -> Browser:
    """Lazy init browser singleton."""
    global _playwright, _browser
    async with _lock:
        if _browser is None or not _browser.is_connected():
            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(headless=True)
            logger.info("Browser launched")
        return _browser


@asynccontextmanager
async def get_page():
    """Context manager: yield page, auto close per request."""
    browser = await get_browser()
    ctx = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    page = await ctx.new_page()
    try:
        yield page
    finally:
        await ctx.close()


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

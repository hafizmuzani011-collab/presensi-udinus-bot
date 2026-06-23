"""Shared browser context — persistent context, thread-safe, auto-reconnect."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext

logger = logging.getLogger(__name__)

# Global state
_playwright = None
_browser: Browser | None = None
_persistent_context: BrowserContext | None = None
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


async def _ensure_context() -> BrowserContext:
    """Persistent context dengan cookies tersimpan di user_data_dir."""
    global _persistent_context

    if _persistent_context is not None:
        try:
            pages = _persistent_context.pages
            if pages is not None:
                return _persistent_context
        except Exception:
            pass
        _persistent_context = None

    browser = await _ensure_browser()
    user_data = os.path.join(Path.home(), ".presensi-bot", "browser-data")
    os.makedirs(user_data, exist_ok=True)

    _persistent_context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        storage_state=os.path.join(user_data, "storage.json") if os.path.exists(os.path.join(user_data, "storage.json")) else None,
    )
    return _persistent_context


@asynccontextmanager
async def get_page():
    """Context manager: reusable persistent context, yield new page, auto-close page."""
    page = None
    try:
        ctx = await _ensure_context()
        page = await ctx.new_page()
        yield page
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


async def close_browser():
    """Shutdown browser (panggil saat bot exit)."""
    global _playwright, _browser, _persistent_context

    if _persistent_context:
        try:
            user_data = os.path.join(Path.home(), ".presensi-bot", "browser-data")
            await _persistent_context.storage_state(path=os.path.join(user_data, "storage.json"))
        except Exception:
            pass
        try:
            await _persistent_context.close()
        except Exception:
            pass
        _persistent_context = None

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

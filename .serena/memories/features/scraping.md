# Scraping and Features architecture

## Recent updates
- Replaced deep integration of web scraping logic inside `telegram_bot.py` with standalone module components in `scrapers/`.
- Kulino Auto-Material Download Feature added: Scrapes moodle for `.pdf`, `.ppt` and sends docs straight to telegram chat. State tracked using `materials_cache.json`.
- Migrated dashboard logic: Removed 500 lines of inline HTML/CSS out of `web_dashboard.py` into a proper template (`templates/dashboard.html`).
- Refactored `instance_lock.py` to be cross-platform, handling both `win32` with Mutex and POSIX (`Linux/macOS`) via `fcntl`.

## Scraping Pointers
- **Kulino** (Moodle): Requires `scrapers.kulino.check_new_materials()`, uses CSS selectors + raw python regex fallback over body.
- **SiAdin** (Academic): Requires finding the *Hadir* button in `akademik/presensiOnline`, tailwind dialog manipulation handled via JS evaluation injected via Playwright.

## Important Note
We moved the project from `C:\` to `D:\Udinus-Academic-Assistant`.
All code execution and refactoring is taking place here.

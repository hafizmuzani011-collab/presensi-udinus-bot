# Codebase Conventions

## Code Style
- **Type hints**: Required for all public function signatures
- **Docstrings**: Indonesian at module level, English for function-level
- **Imports**: stdlib first, third-party second, local last; no blank lines between groups
- **Formatting**: Single quotes, auto-indent 4 spaces. Run `ruff format .`

## Patterns
- **Logging**: `logger = logging.getLogger(__name__)` at module level
- **State management**: `asyncio.Lock` for async shared state, `threading.Lock` for dashboard access
- **File I/O**: atomic writes via `file_utils.atomic_write()` (tmp + fsync + rename)
- **Browser**: Singleton browser with persistent context (`browser.py`), page context manager for request scoping
- **Credential config**: Always `.env` file, never hardcoded

## Code Structure
- `scrapers/` package: one module per venue (kulino, siadin, mhs, khs, formatters)
- `bot.py`: Main logic, commands, and proactive loop
- `storage.py`: Persistence layer  
- `file_utils.py`: Shared atomic write helpers

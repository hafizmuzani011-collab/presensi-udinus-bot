import pytest

# Allow async test functions without explicit @pytest.mark.asyncio decorator
pytest_plugins = []


def pytest_collection_modifyitems(config, items):
    """Auto-mark all async test functions as asyncio."""
    for item in items:
        if isinstance(item, pytest.Function) and item.function.__code__.co_flags & 0x100:
            # co_flags & 0x100 = CO_COROUTINE
            item.add_marker(pytest.mark.asyncio)

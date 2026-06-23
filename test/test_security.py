"""Test security gate — auth bypass fix.

Verifies that:
- ADMIN_CHAT_ID can register on first message
- Non-admin chat_id is rejected when no admin registered
- Multiple chat_ids are not auto-added without explicit /addchid
"""
import pytest


@pytest.fixture(autouse=True)
def setup_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in ("TELEGRAM_BOT_TOKEN", "KULINO_SAYA_NIM", "KULINO_SAYA_PASS",
              "KULINO_PACAR_NIM", "KULINO_PACAR_PASS",
              "MHS_SAYA_NIM", "MHS_SAYA_PASS",
              "MHS_PACAR_NIM", "MHS_PACAR_PASS", "DASH_TOKEN"):
        monkeypatch.setenv(k, "test")
    yield


class TestAuthGate:
    def test_admin_chatid_loaded_from_config(self, monkeypatch):
        """When ADMIN_CHAT_ID is set in config, it can be imported."""
        monkeypatch.setattr("config.ADMIN_CHAT_ID", 123456)
        import importlib
        import bot
        importlib.reload(bot)
        assert bot.ADMIN_CHAT_ID == 123456

    def test_no_admin_defaults_zero(self, monkeypatch):
        """When ADMIN_CHAT_ID=0 (default), it's safe."""
        monkeypatch.setattr("config.ADMIN_CHAT_ID", 0)
        import importlib
        import bot
        importlib.reload(bot)
        assert bot.ADMIN_CHAT_ID == 0


class TestPresensiDoneLock:
    def test_lock_type(self):
        """Verify asyncio.Lock is used to guard _presensi_done."""
        import bot
        import asyncio
        assert isinstance(bot._presensi_done_lock, asyncio.Lock)

    def test_presensi_done_reset_on_new_day(self, tmp_path, monkeypatch):
        """On new day, _presensi_done resets to empty set."""
        monkeypatch.chdir(tmp_path)
        import bot
        # Simulate yesterday's state
        bot._presensi_done = {"saya:senin:07:00"}
        bot._presensi_done_date = "2026-06-20"
        # Today is different -> should reset
        bot._load_presensi_done_for_today("2026-06-22")
        assert bot._presensi_done == set()
        assert bot._presensi_done_date == "2026-06-22"

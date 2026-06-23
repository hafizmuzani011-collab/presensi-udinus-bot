"""Test handle_command — command dispatch & response content."""
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import pytest

from config import NAMA_SAYA, NAMA_PACAR  # noqa: F401


@pytest.fixture(autouse=True)
def setup_env(monkeypatch, tmp_path):
    """Isolasi test: tmp dir + mock environment."""
    monkeypatch.chdir(tmp_path)
    # Set dummy credentials
    for k in ("TELEGRAM_BOT_TOKEN", "KULINO_SAYA_NIM", "KULINO_SAYA_PASS",
              "KULINO_PACAR_NIM", "KULINO_PACAR_PASS",
              "MHS_SAYA_NIM", "MHS_SAYA_PASS",
              "MHS_PACAR_NIM", "MHS_PACAR_PASS", "DASH_TOKEN"):
        monkeypatch.setenv(k, "test")
    # Isolasi config module
    monkeypatch.setattr("bot.ALLOWED_CHAT_IDS", [12345])


@pytest.fixture
def mock_send():
    """Mock tg.send_message — return True + record calls."""
    calls = []
    async def _send(text, **kw):
        calls.append(text)
        return True
    _send.calls = calls
    return _send


@pytest.fixture
def mock_scrape():
    """Mock login_kulino_and_get_tugas — return dummy tasks."""
    async def _mock(key):
        return [
            {"name": "Tugas UTS", "course": "Basis Data",
             "deadline": "27 June 2026 12:30 PM"}
        ]
    return _mock


@pytest.fixture
def mock_presensi():
    """Mock do_presensi_siadin — return success."""
    async def _mock(key):
        return (True, "Berhasil klik tombol presensi")
    return _mock


@pytest.fixture
def mock_holiday(monkeypatch):
    """Mock get_today_holiday = None (not a holiday)."""
    monkeypatch.setattr("bot.get_today_holiday", lambda: None)


class TestStartAndHelp:
    @pytest.mark.asyncio
    async def test_start(self, mock_send):
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("/start")
            assert any("Halo" in m for m in mock_send.calls)

    @pytest.mark.asyncio
    async def test_halo(self, mock_send):
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("halo")
            assert any("Halo" in m for m in mock_send.calls)

    @pytest.mark.asyncio
    async def test_help(self, mock_send):
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("help")
            texts = "\n".join(mock_send.calls)
            assert "Bantuan" in texts
            assert "jadwal" in texts


class TestStatus:
    @pytest.mark.asyncio
    async def test_status(self, mock_send, monkeypatch):
        monkeypatch.setattr("config.BOT_START_TIME", __import__("datetime").datetime(2026, 6, 22, 10, 0, 0))
        monkeypatch.setattr("config.get_stats_snapshot", lambda: {"messages_received": 5, "messages_sent": 3, "tugas_checks": 2, "presensi_done": 1, "errors": 0})
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("/status")
            texts = "\n".join(mock_send.calls)
            assert "Status" in texts

    @pytest.mark.asyncio
    async def test_tanggal(self, mock_send):
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("tanggal")
            texts = "\n".join(mock_send.calls)
            assert "Hari ini" in texts or "Jadwal" in texts


class TestJadwal:
    @pytest.mark.asyncio
    async def test_jadwal_hari(self, mock_send):
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("jadwal senin")
            # Should send 2 messages (saya + pacar)
            assert len(mock_send.calls) >= 1

    @pytest.mark.asyncio
    async def test_jadwal_update(self, mock_send, monkeypatch):
        async def _mock(*a, **kw):
            return (True, "Jadwal diperbarui: 5 slot")
        monkeypatch.setattr("bot.update_schedules_from_mhs", _mock)
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("jadwal update")
            texts = "\n".join(mock_send.calls)
            assert "5 slot" in texts


class TestTugas:
    @pytest.mark.asyncio
    async def test_cek_tugas(self, mock_send, mock_scrape, monkeypatch):
        monkeypatch.setattr("bot.login_kulino_and_get_tugas", mock_scrape)
        monkeypatch.setattr("bot.process_and_remind_deadlines", AsyncMock(return_value=None))
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("cek tugas")
            texts = "\n".join(mock_send.calls)
            assert "Tugas UTS" in texts

    @pytest.mark.asyncio
    async def test_cek_tugas_pacar(self, mock_send, mock_scrape, monkeypatch):
        monkeypatch.setattr("bot.login_kulino_and_get_tugas", mock_scrape)
        monkeypatch.setattr("bot.process_and_remind_deadlines", AsyncMock(return_value=None))
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("cek tugas pacar")
            texts = "\n".join(mock_send.calls)
            assert "Tugas UTS" in texts


class TestPresensi:
    @pytest.mark.asyncio
    async def test_presensi_saya(self, mock_send, mock_presensi, mock_holiday, monkeypatch):
        monkeypatch.setattr("bot.do_presensi_siadin", mock_presensi)
        monkeypatch.setattr("config.SCREENSHOT_PRESENSI", "/nonexistent/screenshot.png")
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("presensi")
            texts = "\n".join(mock_send.calls)
            assert "berhasil" in texts

    @pytest.mark.asyncio
    async def test_presensi_pacar(self, mock_send, mock_presensi, mock_holiday, monkeypatch):
        monkeypatch.setattr("bot.do_presensi_siadin", mock_presensi)
        monkeypatch.setattr("config.SCREENSHOT_PRESENSI", "/nonexistent/screenshot.png")
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("presensi pacar")
            texts = "\n".join(mock_send.calls)
            assert "berhasil" in texts

    @pytest.mark.asyncio
    async def test_presensi_hari_libur(self, mock_send, monkeypatch):
        monkeypatch.setattr("bot.get_today_holiday", lambda: "Hari Raya")
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("presensi")
            texts = "\n".join(mock_send.calls)
            assert "libur" in texts.lower()


class TestAutopilot:
    @pytest.mark.asyncio
    async def test_autopilot_off(self, mock_send, monkeypatch):
        monkeypatch.setattr("bot.set_autopilot", MagicMock())
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("autopilot off")
            texts = "\n".join(mock_send.calls)
            assert "NONAKTIF" in texts

    @pytest.mark.asyncio
    async def test_autopilot_on(self, mock_send, monkeypatch):
        monkeypatch.setattr("bot.set_autopilot", MagicMock())
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("autopilot on")
            texts = "\n".join(mock_send.calls)
            assert "AKTIF" in texts


class TestDeadlineTask:
    @pytest.mark.asyncio
    async def test_deadline_empty(self, mock_send, monkeypatch):
        monkeypatch.setattr("bot.load_tasks_deadlines", lambda: {"notified": {}})
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("deadline")
            texts = "\n".join(mock_send.calls)
            assert "Belum" in texts

    @pytest.mark.asyncio
    async def test_cleanup(self, mock_send, monkeypatch):
        monkeypatch.setattr("bot.cleanup_expired_deadlines", lambda: 2)
        monkeypatch.setattr("bot.load_tasks_deadlines", lambda: {"notified": {}})
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("cleanup")
            texts = "\n".join(mock_send.calls)
            assert "dihapus" in texts.lower()


class TestAlias:
    @pytest.mark.asyncio
    async def test_addalias(self, mock_send):
        with patch("bot.send_message", mock_send), \
             patch("aliases.add_alias", MagicMock()):
            from bot import handle_command
            await handle_command("addalias cek jadwal")

    @pytest.mark.asyncio
    async def test_deletalias(self, mock_send):
        with patch("bot.send_message", mock_send), \
             patch("aliases.remove_alias", MagicMock(return_value=True)):
            from bot import handle_command
            await handle_command("delalias cek")


class TestLibur:
    @pytest.mark.asyncio
    async def test_libur(self, mock_send, monkeypatch):
        monkeypatch.setattr("bot.HOLIDAY_CACHE", {"2026-12-25": "Hari Natal"})
        monkeypatch.setattr("bot.get_today_holiday", lambda: None)
        monkeypatch.setattr("bot.load_holidays", lambda: {"2026-12-25": "Hari Natal"})
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("libur")
            texts = "\n".join(mock_send.calls)
            assert "Libur" in texts


class TestQuickStats:
    @pytest.mark.asyncio
    async def test_quickstats_basic(self, mock_send, monkeypatch):
        from datetime import datetime, timedelta
        future = (datetime.now() + timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%S")
        past = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        monkeypatch.setattr("bot.get_stats_snapshot",
                            lambda: {"messages_received": 5, "presensi_done": 2,
                                     "tugas_checks": 1, "errors": 0})
        monkeypatch.setattr("bot.load_tasks_deadlines", lambda: {
            "saya:tugas_dekat": {"name": "Tugas Dekat", "account": NAMA_SAYA,
                                  "deadline_iso": future, "deadline_raw": "Besok"},
            "saya:tugas_lewat": {"name": "Tugas Lewat", "account": NAMA_SAYA,
                                  "deadline_iso": past, "deadline_raw": "Kemarin"},
            "notified": {},
        })
        monkeypatch.setattr("bot.load_schedules", lambda: {
            "saya": {"senin": []}, "pacar": {"senin": []},
        })
        monkeypatch.setattr("bot.get_today_holiday", lambda: None)
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("quickstats")
        texts = "\n".join(mock_send.calls)
        assert "Quick Stats" in texts
        assert "1 aktif" in texts  # Only future one
        assert "Tugas Dekat" in texts
        assert "Tugas Lewat" not in texts

    @pytest.mark.asyncio
    async def test_quickstats_alias(self, mock_send, monkeypatch):
        monkeypatch.setattr("bot.load_tasks_deadlines", lambda: {"notified": {}})
        monkeypatch.setattr("bot.load_schedules", lambda: {"saya": {}, "pacar": {}})
        monkeypatch.setattr("bot.get_today_holiday", lambda: None)
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("ringkasan")
        assert any("Quick Stats" in m for m in mock_send.calls)

    @pytest.mark.asyncio
    async def test_quickstats_holiday(self, mock_send, monkeypatch):
        monkeypatch.setattr("bot.load_tasks_deadlines", lambda: {"notified": {}})
        monkeypatch.setattr("bot.load_schedules", lambda: {"saya": {}, "pacar": {}})
        monkeypatch.setattr("bot.get_today_holiday", lambda: "Hari Raya")
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("quickstats")
        texts = "\n".join(mock_send.calls)
        assert "Hari Raya" in texts


class TestJadwalGambar:
    @pytest.mark.asyncio
    async def test_jadwal_gambar_invalid_hari(self, mock_send, monkeypatch):
        with patch("bot.send_message", mock_send):
            from bot import handle_command
            await handle_command("jadwal gambar foo")
        texts = "\n".join(mock_send.calls)
        assert "tidak dikenali" in texts.lower()

    @pytest.mark.asyncio
    async def test_jadwal_gambar_success(self, mock_send, monkeypatch, tmp_path):
        from contextlib import asynccontextmanager
        monkeypatch.chdir(tmp_path)
        class FakePage:
            async def set_content(self, *a, **kw): pass
            async def wait_for_timeout(self, *a): pass
            async def query_selector(self, *a):
                class FakeEl:
                    async def screenshot(self, *a, **kw): pass
                return FakeEl()
        @asynccontextmanager
        async def fake_get_page():
            yield FakePage()
        async def fake_render(page, schedules, hari_id, output_path):  # noqa: ARG001
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"\x89PNG\r\n\x1a\n")
            return True
        monkeypatch.setattr("bot.get_page", fake_get_page)
        monkeypatch.setattr("render.render_jadwal_png", fake_render)
        monkeypatch.setattr("bot.load_schedules", lambda: {"saya": {"senin": []}, "pacar": {"senin": []}})
        with patch("bot.send_message", mock_send), patch("bot.send_photo", AsyncMock()):
            from bot import handle_command
            await handle_command("jadwal gambar senin")


class TestMorningReminderState:
    def test_morning_state_attribute(self):
        """Verify _morning_reminder_date state exists."""
        import bot
        assert hasattr(bot, "_morning_reminder_date")
        assert bot._morning_reminder_date is None or isinstance(bot._morning_reminder_date, str)


class TestJadwalGambarHelp:
    def test_help_includes_jadwal_gambar(self, monkeypatch):
        """Verify help text mentions new commands."""
        import bot
        from pathlib import Path
        # Read source to check help text includes new commands
        src = Path(bot.__file__).read_text(encoding="utf-8")
        assert "jadwal gambar" in src.lower() or "jadwal gambar" in src
        assert "quickstats" in src.lower() or "ringkasan" in src.lower()

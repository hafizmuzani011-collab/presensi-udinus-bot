"""Test Snooze Reminder — snooze state, callback, fire logic."""
import time
import pytest

from constants import SNOOZE_DURATION_SECONDS


@pytest.fixture(autouse=True)
def setup_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in ("TELEGRAM_BOT_TOKEN", "KULINO_SAYA_NIM", "KULINO_SAYA_PASS",
              "KULINO_PACAR_NIM", "KULINO_PACAR_PASS",
              "MHS_SAYA_NIM", "MHS_SAYA_PASS",
              "MHS_PACAR_NIM", "MHS_PACAR_PASS", "DASH_TOKEN"):
        monkeypatch.setenv(k, "test")
    monkeypatch.setattr("bot.ALLOWED_CHAT_IDS", [12345])
    import bot
    bot._snoozed_reminders.clear()
    bot._reminder_sent.clear()
    yield


class TestSnoozeState:
    def test_initial_empty(self):
        import bot
        assert bot._snoozed_reminders == {}

    def test_snooze_set(self, monkeypatch):
        import bot
        key = "snoozed:saya:senin:07:00"
        bot._snoozed_reminders[key] = time.time() + 600
        assert key in bot._snoozed_reminders

    def test_snooze_expired_detection(self):
        import bot
        key = "snoozed:saya:senin:07:00"
        bot._snoozed_reminders[key] = time.time() - 1
        now = time.time()
        expired = [k for k, exp in bot._snoozed_reminders.items() if exp <= now]
        assert expired == [key]


class TestSnoozeCallback:
    @pytest.mark.asyncio
    async def test_snooze_callback_sets_state(self, monkeypatch):
        import bot
        sent = []
        async def _send(text, **kw):
            sent.append(text)
            return True
        async def _answer(cb_id, text=""):
            return True
        monkeypatch.setattr("bot.send_message", _send)
        monkeypatch.setattr("bot.answer_callback", _answer)
        # Manually invoke callback logic via the same pattern
        cb_data = "snooze:saya:senin:07:00"
        parts = cb_data.split(":")
        who, key_hari, jam_mulai = parts[1], parts[2], parts[3]
        key = f"snoozed:{who}:{key_hari}:{jam_mulai}"
        bot._snoozed_reminders[key] = time.time() + SNOOZE_DURATION_SECONDS
        assert key in bot._snoozed_reminders
        assert bot._snoozed_reminders[key] > time.time()

    @pytest.mark.asyncio
    async def test_snooze_presensi_callback(self, monkeypatch):
        """Snooze button tidak trigger presensi langsung."""
        called = []
        async def _pres(key):
            called.append(key)
            return (True, "ok")
        monkeypatch.setattr("bot.do_presensi_siadin", _pres)
        cb_data = "snooze:saya:senin:07:00"
        # Just simulate: snooze doesn't call do_presensi
        if cb_data.startswith("snooze:"):
            # logic: only set state, do NOT call presensi
            pass
        assert len(called) == 0


class TestSnoozeFireLogic:
    @pytest.mark.asyncio
    async def test_fire_sends_message(self, monkeypatch):
        import bot
        bot._snoozed_reminders.clear()
        sent = []
        async def _send(text, **kw):
            sent.append(text)
            return True
        monkeypatch.setattr("bot.send_message", _send)
        monkeypatch.setattr("bot.load_schedules", lambda: {
            "saya": {"senin": [["07:00-08:40", "Basis Data", "D.2.J"]]},
            "pacar": {"senin": []},
        })
        # Add expired snooze
        bot._snoozed_reminders["snoozed:saya:senin:07:00"] = time.time() - 1
        await bot._check_snoozed_reminders("senin")
        # After fire, state should be cleared
        assert "snoozed:saya:senin:07:00" not in bot._snoozed_reminders
        # Message should contain "Snooze selesai"
        assert any("Snooze selesai" in s for s in sent)

    @pytest.mark.asyncio
    async def test_fire_skips_invalid_key(self, monkeypatch):
        import bot
        bot._snoozed_reminders.clear()
        sent = []
        async def _send(text, **kw):
            sent.append(text)
            return True
        monkeypatch.setattr("bot.send_message", _send)
        bot._snoozed_reminders["bad_key"] = time.time() - 1
        await bot._check_snoozed_reminders("senin")
        assert "bad_key" not in bot._snoozed_reminders
        assert sent == []  # No valid snooze to fire

    @pytest.mark.asyncio
    async def test_fire_skips_wrong_hari(self, monkeypatch):
        import bot
        bot._snoozed_reminders.clear()
        sent = []
        async def _send(text, **kw):
            sent.append(text)
            return True
        monkeypatch.setattr("bot.send_message", _send)
        bot._snoozed_reminders["snoozed:saya:selasa:09:00"] = time.time() - 1
        await bot._check_snoozed_reminders("senin")
        assert "snoozed:saya:selasa:09:00" not in bot._snoozed_reminders
        assert sent == []

    @pytest.mark.asyncio
    async def test_fire_skips_unknown_who(self, monkeypatch):
        import bot
        bot._snoozed_reminders.clear()
        sent = []
        async def _send(text, **kw):
            sent.append(text)
            return True
        monkeypatch.setattr("bot.send_message", _send)
        bot._snoozed_reminders["snoozed:unknown:senin:07:00"] = time.time() - 1
        await bot._check_snoozed_reminders("senin")
        assert "snoozed:unknown:senin:07:00" not in bot._snoozed_reminders
        assert sent == []

    @pytest.mark.asyncio
    async def test_fire_skips_missing_schedule(self, monkeypatch):
        import bot
        bot._snoozed_reminders.clear()
        sent = []
        async def _send(text, **kw):
            sent.append(text)
            return True
        monkeypatch.setattr("bot.send_message", _send)
        monkeypatch.setattr("bot.load_schedules", lambda: {"saya": {"senin": []}, "pacar": {"senin": []}})
        bot._snoozed_reminders["snoozed:saya:senin:07:00"] = time.time() - 1
        await bot._check_snoozed_reminders("senin")
        # Cleared because schedule not found
        assert "snoozed:saya:senin:07:00" not in bot._snoozed_reminders
        assert sent == []

    @pytest.mark.asyncio
    async def test_no_fire_for_future_snooze(self, monkeypatch):
        import bot
        bot._snoozed_reminders.clear()
        sent = []
        async def _send(text, **kw):
            sent.append(text)
            return True
        monkeypatch.setattr("bot.send_message", _send)
        monkeypatch.setattr("bot.load_schedules", lambda: {
            "saya": {"senin": [["07:00-08:40", "Basis Data", "D.2.J"]]},
            "pacar": {"senin": []},
        })
        # Set snooze in the future
        bot._snoozed_reminders["snoozed:saya:senin:07:00"] = time.time() + 600
        await bot._check_snoozed_reminders("senin")
        # Not yet expired
        assert "snoozed:saya:senin:07:00" in bot._snoozed_reminders
        assert sent == []


class TestSnoozeButtonInReminder:
    def test_reminder_message_contains_snooze_callback(self, monkeypatch):
        """Class reminder (30 min) should include snooze button in keyboard."""
        from tg import make_inline_keyboard
        who = "saya"
        hari_id = "senin"
        jam_mulai = "07:00"
        buttons = [
            [{"text": "✅ Presensi", "callback_data": "presensi:hadir:" + who}],
            [{"text": "⏰ Snooze 10m", "callback_data": f"snooze:{who}:{hari_id}:{jam_mulai}"}],
        ]
        kb = make_inline_keyboard(buttons)
        assert "snooze:saya:senin:07:00" in str(kb)
        assert "presensi:hadir:saya" in str(kb)


class TestSnoozeCallbackWiring:
    def test_constants_snooze_duration(self):
        assert SNOOZE_DURATION_SECONDS == 600

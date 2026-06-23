"""Test process_and_remind_deadlines - reminder logic."""
from datetime import datetime, timedelta
from pathlib import Path
import json
import pytest
from config import TASKS_DEADLINE_FILE
from utils import process_and_remind_deadlines


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from config import TASKS_DEADLINE_FILE
    Path(TASKS_DEADLINE_FILE).parent.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def fixed_now(monkeypatch):
    """Freeze datetime.now() in utils module."""
    fake_now = datetime(2026, 6, 22, 10, 0, 0)
    import utils as utils_mod
    monkeypatch.setattr(utils_mod, "datetime", _FrozenDateTime(fake_now))
    return fake_now


class _FrozenDateTime:
    def __init__(self, frozen):
        self._frozen = frozen

    def now(self):
        return self._frozen

    def fromisoformat(self, s):
        return datetime.fromisoformat(s)

    def __call__(self, *args, **kwargs):
        return datetime(*args, **kwargs)


def make_send_recorder():
    """Mock send_message that records all calls."""
    calls = []

    async def _send(text, **kwargs):
        calls.append(text)
        return True

    _send.calls = calls
    return _send


def in_5_hours():
    return (datetime(2026, 6, 22, 10, 0, 0) + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S")


def in_10_hours():
    return (datetime(2026, 6, 22, 10, 0, 0) + timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%S")


def in_24_hours():
    return (datetime(2026, 6, 22, 10, 0, 0) + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")


class TestParseAndStore:
    def test_new_task_stored(self, fixed_now):
        send = make_send_recorder()
        tasks = [{"name": "Tugas Basis Data", "course": "BD", "deadline": "27 June 2026 12:30 PM"}]
        asyncio_run(process_and_remind_deadlines(tasks, "saya", send))
        cache = json.loads(Path(TASKS_DEADLINE_FILE).read_text())
        assert "saya:Tugas Basis Data" in cache
        assert cache["saya:Tugas Basis Data"]["course"] == "BD"

    def test_empty_task_skipped(self, fixed_now):
        send = make_send_recorder()
        tasks = [{"name": "", "deadline": "27 June 2026 12:30 PM"}]
        asyncio_run(process_and_remind_deadlines(tasks, "saya", send))
        assert not Path(TASKS_DEADLINE_FILE).exists()

    def test_unchanged_task_not_resaved(self, fixed_now):
        send = make_send_recorder()
        tasks = [{"name": "Tugas A", "course": "MK", "deadline": "27 June 2026 12:30 PM"}]
        asyncio_run(process_and_remind_deadlines(tasks, "saya", send))
        mtime1 = Path(TASKS_DEADLINE_FILE).stat().st_mtime

        asyncio_run(process_and_remind_deadlines(tasks, "saya", send))
        mtime2 = Path(TASKS_DEADLINE_FILE).stat().st_mtime
        # No dirty = no rewrite
        assert mtime1 == mtime2


class TestReminderH6:
    def test_h6_sends_reminder(self, fixed_now):
        # Pre-populate cache with task in 5h
        Path(TASKS_DEADLINE_FILE).write_text(json.dumps({
            "saya:urgent": {
                "name": "urgent", "course": "MK", "account": "Hafizh",
                "deadline_raw": "27 June 2026 12:30 PM",
                "deadline_iso": in_5_hours(),
            },
            "notified": {},
        }))
        send = make_send_recorder()
        asyncio_run(process_and_remind_deadlines([], "saya", send))
        assert any("Mendekat" in m for m in send.calls)
        # notified flag set
        cache = json.loads(Path(TASKS_DEADLINE_FILE).read_text())
        assert cache["notified"].get("saya:urgent:h6") is True

    def test_h6_not_sent_twice(self, fixed_now):
        Path(TASKS_DEADLINE_FILE).write_text(json.dumps({
            "saya:urgent": {
                "name": "urgent", "course": "MK", "account": "Hafizh",
                "deadline_raw": "27 June 2026 12:30 PM",
                "deadline_iso": in_5_hours(),
            },
            "notified": {"saya:urgent:h6": True},
        }))
        send = make_send_recorder()
        asyncio_run(process_and_remind_deadlines([], "saya", send))
        assert len(send.calls) == 0


class TestReminderH12:
    def test_h12_sends(self, fixed_now):
        Path(TASKS_DEADLINE_FILE).write_text(json.dumps({
            "saya:soon": {
                "name": "soon", "course": "MK", "account": "Hafizh",
                "deadline_raw": "27 June 2026 12:30 PM",
                "deadline_iso": in_10_hours(),
            },
            "notified": {},
        }))
        send = make_send_recorder()
        asyncio_run(process_and_remind_deadlines([], "saya", send))
        assert any("H-12" in m for m in send.calls)
        cache = json.loads(Path(TASKS_DEADLINE_FILE).read_text())
        assert cache["notified"].get("saya:soon:h12") is True

    def test_h6_priority_over_h12(self, fixed_now):
        # Task 5h away, h12 not yet sent — but h6 should fire, not h12
        Path(TASKS_DEADLINE_FILE).write_text(json.dumps({
            "saya:urgent": {
                "name": "urgent", "course": "MK", "account": "Hafizh",
                "deadline_raw": "27 June 2026 12:30 PM",
                "deadline_iso": in_5_hours(),
            },
            "notified": {},
        }))
        send = make_send_recorder()
        asyncio_run(process_and_remind_deadlines([], "saya", send))
        h6_sent = any("Mendekat" in m for m in send.calls)
        h12_sent = any("H-12" in m for m in send.calls)
        assert h6_sent
        assert not h12_sent


class TestNoReminder:
    def test_distant_task_no_reminder(self, fixed_now):
        Path(TASKS_DEADLINE_FILE).write_text(json.dumps({
            "saya:far": {
                "name": "far", "course": "MK", "account": "Hafizh",
                "deadline_raw": "27 June 2026 12:30 PM",
                "deadline_iso": in_24_hours(),
            },
            "notified": {},
        }))
        send = make_send_recorder()
        asyncio_run(process_and_remind_deadlines([], "saya", send))
        assert len(send.calls) == 0

    def test_past_deadline_skipped(self, fixed_now):
        Path(TASKS_DEADLINE_FILE).write_text(json.dumps({
            "saya:overdue": {
                "name": "overdue", "course": "MK", "account": "Hafizh",
                "deadline_raw": "1 June 2026 12:30 PM",
                "deadline_iso": "2026-06-01T12:30:00",
            },
            "notified": {},
        }))
        send = make_send_recorder()
        asyncio_run(process_and_remind_deadlines([], "saya", send))
        assert len(send.calls) == 0


class TestDedup:
    def test_task_same_name_different_course(self, fixed_now):
        """Dua tugas beda course tapi nama sama → harus deduplikasi."""
        # This is actually a known limitation: task_key = "account:name"
        # without course prefix. Test documents the behavior.
        tasks = [
            {"name": "Tugas UTS", "course": "Basis Data",
             "deadline": "27 June 2026 12:30 PM"},
            {"name": "Tugas UTS", "course": "Jaringan",
             "deadline": "30 June 2026 11:59 PM"},
        ]
        send = make_send_recorder()
        asyncio_run(process_and_remind_deadlines(tasks, "saya", send))
        cache = json.loads(Path(TASKS_DEADLINE_FILE).read_text())
        # Key is "saya:Tugas UTS" — second task overwrites first
        assert "saya:Tugas UTS" in cache


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)

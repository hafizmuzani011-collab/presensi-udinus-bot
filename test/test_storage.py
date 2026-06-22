"""Test storage layer - file I/O functions."""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
import pytest
from storage import (
    load_chat_ids, save_chat_id,
    load_presensi_done, save_presensi_done,
    load_offset, save_offset,
    cleanup_expired_deadlines,
    load_tasks_deadlines, save_tasks_deadlines,
    backup_tasks_deadlines,
)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Setiap test pakai folder tmp sendiri."""
    monkeypatch.chdir(tmp_path)


class TestChatIds:
    def test_load_empty(self):
        assert load_chat_ids() == []

    def test_save_and_load(self):
        save_chat_id(12345)
        assert load_chat_ids() == [12345]

    def test_multiple(self):
        save_chat_id(1)
        save_chat_id(2)
        save_chat_id(3)
        assert load_chat_ids() == [1, 2, 3]

    def test_no_duplicate(self):
        save_chat_id(1)
        save_chat_id(1)
        assert load_chat_ids() == [1]

    def test_negative_id(self):
        save_chat_id(-100)
        assert load_chat_ids() == [-100]

    def test_invalid_line_skipped(self):
        Path("chat_id.txt").write_text("123,not_a_number,456")
        assert load_chat_ids() == [123, 456]

    def test_empty_file(self):
        Path("chat_id.txt").write_text("")
        assert load_chat_ids() == []


class TestPresensiDone:
    def test_load_empty(self):
        assert load_presensi_done() == {"date": "", "keys": []}

    def test_save_and_load(self):
        save_presensi_done("2026-06-22", {"senin:matkul1", "senin:matkul2"})
        data = load_presensi_done()
        assert data["date"] == "2026-06-22"
        assert "senin:matkul1" in data["keys"]
        assert "senin:matkul2" in data["keys"]

    def test_overwrite(self):
        save_presensi_done("2026-06-22", {"a"})
        save_presensi_done("2026-06-23", {"b"})
        data = load_presensi_done()
        assert data["date"] == "2026-06-23"
        assert data["keys"] == ["b"]

    def test_corrupt_json(self):
        Path("presensi_done.json").write_text("not json")
        data = load_presensi_done()
        assert data == {"date": "", "keys": []}


class TestOffset:
    def test_default_none(self):
        assert load_offset() is None

    def test_save_and_load(self):
        save_offset(42)
        assert load_offset() == 42

    def test_overwrite(self):
        save_offset(1)
        save_offset(99)
        assert load_offset() == 99


class TestTasksDeadlines:
    TODAY = "2026-06-22T10:00:00"
    PAST = "2026-06-01T10:00:00"
    FUTURE = "2026-07-01T10:00:00"

    def test_load_empty(self):
        assert load_tasks_deadlines() == {"notified": {}}

    def test_save_and_load(self):
        save_tasks_deadlines({
            "saya:tugas1": {
                "name": "tugas1", "deadline_raw": "Besok",
                "deadline_iso": self.TODAY, "course": "MK",
                "account": "Hafizh",
            },
            "notified": {},
        })
        data = load_tasks_deadlines()
        assert "saya:tugas1" in data
        assert data["saya:tugas1"]["name"] == "tugas1"

    def test_cleanup_removes_past(self, monkeypatch):
        cache = {
            "saya:lama": {"deadline_iso": self.PAST, "name": "lama", "account": "H"},
            "saya:baru": {"deadline_iso": self.FUTURE, "name": "baru", "account": "H"},
            "notified": {"saya:lama:h6": True},
        }
        save_tasks_deadlines(cache)
        removed = cleanup_expired_deadlines()
        assert removed == 1
        data = load_tasks_deadlines()
        assert "saya:baru" in data
        assert "saya:lama" not in data
        # notified cleanup
        assert "saya:lama:h6" not in data.get("notified", {})

    def test_cleanup_no_expired(self):
        cache = {"saya:tugas": {"deadline_iso": self.FUTURE, "name": "t", "account": "H"}, "notified": {}}
        save_tasks_deadlines(cache)
        removed = cleanup_expired_deadlines()
        assert removed == 0

    def test_backup(self):
        cache = {"saya:t": {"deadline_iso": self.FUTURE, "name": "t", "account": "H"}}
        save_tasks_deadlines(cache)
        assert backup_tasks_deadlines()
        bak = Path("tasks_deadlines.json.bak")
        assert bak.exists()
        data = json.loads(bak.read_text())
        assert "saya:t" in data

    def test_corrupt_file(self):
        Path("tasks_deadlines.json").write_text("not json")
        assert load_tasks_deadlines() == {"notified": {}}

"""Test storage layer - file I/O functions."""
import json
from pathlib import Path
import pytest
from storage import (
    load_chat_ids, save_chat_id,
    load_presensi_done, save_presensi_done,
    load_offset, save_offset,
    cleanup_expired_deadlines,
    load_tasks_deadlines, save_tasks_deadlines,
    backup_tasks_deadlines, run_backup, backup_file,
    load_schedules,
)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Setiap test pakai folder tmp sendiri."""
    monkeypatch.chdir(tmp_path)
    # Create subdirs matching config paths
    from config import RUNTIME_DIR
    Path(RUNTIME_DIR).mkdir(parents=True, exist_ok=True)
    yield


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
        from config import CHAT_ID_FILE
        Path(CHAT_ID_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(CHAT_ID_FILE).write_text("123,not_a_number,456")
        assert load_chat_ids() == [123, 456]

    def test_empty_file(self):
        from config import CHAT_ID_FILE
        Path(CHAT_ID_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(CHAT_ID_FILE).write_text("")
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
        from config import PRESENSI_DONE_FILE
        Path(PRESENSI_DONE_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(PRESENSI_DONE_FILE).write_text("not json")
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
        from config import TASKS_DEADLINE_FILE
        cache = {"saya:t": {"deadline_iso": self.FUTURE, "name": "t", "account": "H"}}
        save_tasks_deadlines(cache)
        assert backup_tasks_deadlines()
        bak = Path(TASKS_DEADLINE_FILE + ".bak")
        assert bak.exists()
        data = json.loads(bak.read_text())
        assert "saya:t" in data

    def test_corrupt_file(self):
        from config import TASKS_DEADLINE_FILE
        Path(TASKS_DEADLINE_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(TASKS_DEADLINE_FILE).write_text("not json")
        assert load_tasks_deadlines() == {"notified": {}}


class TestSchedulesValidation:
    def test_valid(self):
        from config import SCHEDULES_FILE
        data = {"saya": {"senin": [["07:00", "Basis Data", "D.2.J"]]}}
        Path(SCHEDULES_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(SCHEDULES_FILE).write_text(json.dumps(data))
        assert load_schedules() == data

    def test_corrupt_json(self):
        from config import SCHEDULES_FILE
        Path(SCHEDULES_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(SCHEDULES_FILE).write_text("not json")
        assert load_schedules() == {}

    def test_invalid_type(self):
        from config import SCHEDULES_FILE
        Path(SCHEDULES_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(SCHEDULES_FILE).write_text("[]")  # not dict
        assert load_schedules() == {}

    def test_invalid_slot(self):
        from config import SCHEDULES_FILE
        Path(SCHEDULES_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(SCHEDULES_FILE).write_text('{"saya": {"senin": ["bad"]}}')
        assert load_schedules() == {}


class TestRunBackup:
    def test_run_backup_all(self):
        from config import TASKS_DEADLINE_FILE, SCHEDULES_FILE, PRESENSI_HISTORY_FILE
        for p, data in [
            (TASKS_DEADLINE_FILE, json.dumps({"notified": {}})),
            (SCHEDULES_FILE, json.dumps({"saya": {}})),
            (PRESENSI_HISTORY_FILE, json.dumps([])),
        ]:
            Path(p).parent.mkdir(parents=True, exist_ok=True)
            Path(p).write_text(data)
        count = run_backup()
        assert count == 3
        assert Path(TASKS_DEADLINE_FILE + ".bak").exists()
        assert Path(SCHEDULES_FILE + ".bak").exists()
        assert Path(PRESENSI_HISTORY_FILE + ".bak").exists()

    def test_run_backup_missing_files(self):
        count = run_backup()
        assert count == 0

    def test_backup_file_missing(self):
        assert backup_file("/nonexistent/file.json") is False

"""Test attendance tracker — compute_attendance, parsing, alerts."""
from pathlib import Path

from storage import (
    attendance_alert, compute_attendance, load_logbook_entries,
    parse_logbook_line, write_logbook,
)
from config import LOG_DIR


SAMPLE_SCHEDULES = {
    "saya": {
        "senin": [["07:00-08:40", "Basis Data", "D.2.J"]],
        "selasa": [["09:00-10:40", "Jaringan", "Lab A"]],
        "rabu": [["10:00-11:40", "Basis Data", "D.2.K"]],
    },
    "pacar": {},
}


class TestParseLogbookLine:
    def test_valid_hadir(self):
        r = parse_logbook_line("- 07:00-08:40 - BASIS DATA \u2705 (saya, Ruang D.2.J)")
        assert r is not None
        assert r["jam"] == "07:00-08:40"
        assert r["matkul"] == "BASIS DATA"
        assert r["hadir"] is True
        assert r["account"] == "saya"

    def test_valid_absen(self):
        r = parse_logbook_line("- 09:00-10:40 - MATEMATIKA \u274C (pacar, Ruang Lab C)")
        assert r is not None
        assert r["hadir"] is False

    def test_invalid_line(self):
        assert parse_logbook_line("## Monday, 1 June 2026") is None
        assert parse_logbook_line("") is None


class TestLoadLogbookEntries:
    def test_empty_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert load_logbook_entries(2026, 6) == []

    def test_load_filtered(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        logdir = Path(LOG_DIR)
        logdir.mkdir(parents=True)
        for d, entries in [
            ("2026-06-01", ["- 07:00-08:40 - BD \u2705 (saya, Ruang A)"]),
            ("2026-06-02", ["- 09:00-10:40 - JR \u2705 (pacar, Ruang B)"]),
            ("2026-05-31", ["- 10:00-11:40 - LM \u2705 (saya, Ruang C)"]),
        ]:
            with open(logdir / f"{d}.md", "w", encoding="utf-8") as f:
                for e in entries:
                    f.write(e + "\n")
        assert len(load_logbook_entries(2026, 6)) == 2
        assert len(load_logbook_entries(2026, 6, "saya")) == 1


class TestComputeAttendance:
    def test_with_logbook(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        Path(LOG_DIR).mkdir(parents=True)
        for d in ["2026-06-01", "2026-06-08", "2026-06-15"]:
            write_logbook(d, "saya", "07:00-08:40", "Basis Data", "D.2.J", "hadir")
        write_logbook("2026-06-03", "saya", "10:00-11:40", "Basis Data", "D.2.K", "hadir")
        results = compute_attendance(SAMPLE_SCHEDULES, "saya", 2026, 6)
        bd = [r for r in results if r["matkul"] == "Basis Data"]
        assert len(bd) == 1
        assert bd[0]["hadir"] == 4

    def test_zero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        results = compute_attendance(SAMPLE_SCHEDULES, "saya", 2026, 6)
        for r in results:
            assert r["hadir"] == 0

    def test_unknown_account(self):
        assert compute_attendance(SAMPLE_SCHEDULES, "nonexistent", 2026, 6) == []


class TestAttendanceAlert:
    def test_no_warnings(self):
        assert attendance_alert([{"matkul": "A", "total": 10, "hadir": 10, "pct": 100.0}]) == []

    def test_warning(self):
        r = [{"matkul": "BD", "total": 10, "hadir": 5, "pct": 50.0}]
        w = attendance_alert(r)
        assert len(w) == 1
        assert "BD" in w[0]

    def test_minimal_classes_no_warning(self):
        r = [{"matkul": "MK", "total": 2, "hadir": 1, "pct": 50.0}]
        assert attendance_alert(r) == []

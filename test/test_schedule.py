"""Test get_schedule_for - jadwal formatter."""
import json
from pathlib import Path
import pytest
from utils import get_schedule_for


@pytest.fixture(autouse=True)
def populate_schedules(tmp_path, monkeypatch):
    """Chdir ke tmp, lalu tulis schedules.json di sana."""
    monkeypatch.chdir(tmp_path)
    from config import SCHEDULES_FILE
    schedules = {
        "saya": {
            "senin": [["07:00-08:40", "Basis Data", "D.2.J"]],
            "selasa": [["09:00-11:30", "Jaringan Komputer", "Lab A"]],
            "rabu": [],
        },
        "pacar": {
            "senin": [["10:00-11:40", "Matematika", "D.2.K"]],
        },
    }
    Path(SCHEDULES_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(SCHEDULES_FILE).write_text(json.dumps(schedules))


class TestGetScheduleFor:
    def test_hari_spesifik(self):
        result = get_schedule_for("saya", "senin")
        assert "Basis Data" in result
        assert "07:00-08:40" in result
        assert "D.2.J" in result

    def test_hari_english(self):
        result = get_schedule_for("saya", "Monday")
        assert "Basis Data" in result

    def test_besok(self):
        result = get_schedule_for("saya", "besok")
        assert "Libur" in result or "Jadwal" in result or "🎉" in result

    def test_today_alias(self):
        for alias in ("hari ini", "today", "skrg", "sekarang", "now"):
            result = get_schedule_for("saya", alias)
            assert "Jadwal" in result or "Libur" in result or "🎉" in result, f"alias {alias!r} failed"

    def test_hari_tidak_ada(self):
        result = get_schedule_for("saya", "ahad")
        assert "tidak dikenali" in result

    def test_user_tidak_ada(self):
        result = get_schedule_for("tidak_ada", "senin")
        assert "tidak ditemukan" in result

    def test_hari_tanpa_jadwal(self):
        result = get_schedule_for("saya", "rabu")
        assert ("tidak ada jadwal" in result) or ("Libur" in result) or ("🎉" in result)

    def test_user_pacar(self):
        result = get_schedule_for("pacar", "senin")
        assert "Matematika" in result

    def test_pacar_hari_tidak_ada(self):
        result = get_schedule_for("pacar", "selasa")
        assert ("tidak ada jadwal" in result) or ("Libur" in result)

    def test_hari_case_insensitive(self):
        result = get_schedule_for("saya", "SENIN")
        assert "Basis Data" in result

    def test_name_case_sensitive(self):
        # Nama user harus match persis (lowercase)
        result = get_schedule_for("Saya", "senin")
        assert "tidak ditemukan" in result

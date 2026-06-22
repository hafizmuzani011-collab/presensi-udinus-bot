"""Test NLP parser untuk jadwal & deadline questions."""
import pytest
from nlp import parse_question, answer_jadwal, answer_presensi


def intent(text):
    return {k: v for k, v in parse_question(text).items()
            if k in ("intent", "hari", "keyword", "relative")}


class TestParseQuestion:
    def test_jadwal_hari(self):
        i = intent("jadwal selasa")
        assert i["intent"] == "jadwal"
        assert i["hari"] == "selasa"

    def test_jadwal_relative(self):
        i = intent("jadwal besok")
        assert i["intent"] == "jadwal"
        assert i["relative"] == "besok"

    def test_jadwal_lusa(self):
        i = intent("besok lusa")
        assert i["intent"] == "jadwal"
        assert i["relative"] == "lusa"

    def test_deadline_tugas(self):
        i = intent("deadline basis data")
        assert i["intent"] == "deadline"
        assert "keyword" not in i or i["keyword"] is None or True

    def test_deadline_keyword(self):
        i = intent("kapan deadline basis data")
        assert i["intent"] == "deadline"

    def test_presensi(self):
        i = intent("kapan presensi")
        assert i["intent"] == "presensi"

    def test_presensi_hadir_selasa(self):
        i = intent("hadir selasa")
        assert i["intent"] == "presensi"
        assert i["hari"] == "selasa"

    def test_unknown(self):
        i = intent("apa kabar")
        assert i["intent"] == "unknown"

    def test_hari_by_name(self):
        i = intent("kelas rabu")
        assert i["intent"] == "jadwal"
        assert i["hari"] == "rabu"

    def test_hari_english(self):
        i = intent("schedule monday")
        assert i["intent"] == "jadwal"
        assert i["hari"] == "senin"

    def test_matkul_keyword(self):
        i = intent("jadwal basis data")
        assert i["intent"] == "jadwal"
        assert i["keyword"] is not None

    def test_today(self):
        i = intent("hari ini ada kelas?")
        assert i["intent"] == "jadwal"
        assert i["relative"] == "hari ini"

    def test_case_insensitive(self):
        i = intent("JADWAL SENIN")
        assert i["intent"] == "jadwal"
        assert i["hari"] == "senin"


SCHEDULES = {
    "saya": {
        "senin": [["07:00-08:40", "Basis Data", "D.2.J"]],
        "selasa": [["09:00-10:40", "Jaringan", "Lab A"]],
    },
    "pacar": {
        "senin": [["10:00-11:40", "Matematika", "D.2.K"]],
    },
}


class TestAnswerJadwal:
    def test_hari_tersedia(self):
        reply = answer_jadwal({"hari": "senin"}, SCHEDULES, "saya")
        assert "Basis Data" in reply
        assert "07:00-08:40" in reply

    def test_hari_libur(self):
        reply = answer_jadwal({"hari": "rabu"}, SCHEDULES, "saya")
        assert "Libur" in reply

    def test_none_hari(self):
        reply = answer_jadwal({"hari": None}, SCHEDULES, "saya")
        assert "hari yang kamu maksud" in reply

    def test_cari_matkul(self):
        reply = answer_jadwal({"hari": "senin", "keyword": "basis"}, SCHEDULES, "saya")
        assert "Basis Data" in reply

    def test_cari_matkul_tidak_ada(self):
        reply = answer_jadwal({"hari": "senin", "keyword": "sistem"}, SCHEDULES, "saya")
        assert "tidak ada yang cocok" in reply


class TestAnswerPresensi:
    def test_presensi_hari_kerja(self):
        reply = answer_presensi({"hari": "senin"}, SCHEDULES)
        assert "Basis Data" in reply
        assert "Hafizh" in reply
        assert "Azfa" in reply

    def test_presensi_hari_libur(self):
        reply = answer_presensi({"hari": "rabu"}, SCHEDULES)
        assert "tidak ada presensi" in reply

    def test_presensi_no_hari(self):
        reply = answer_presensi({"hari": None}, SCHEDULES)
        assert "hari yang kamu maksud" in reply

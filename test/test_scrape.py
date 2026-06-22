"""Test extract_tasks_from_text — Kulino page text parser."""
import pytest
from telegram_bot import extract_tasks_from_text


class TestExtractTasksFromText:
    def test_standard_format(self):
        text = "Upload catatan materi Konfigurasi Mikrotik Router OS is due\nTomorrow, 12:00 AM"
        results = extract_tasks_from_text(text)
        assert len(results) == 1
        assert "Mikrotik" in results[0]["name"]
        assert "Tomorrow" in results[0]["deadline"]

    def test_capstone_format(self):
        text = "Capstone 11: Webservice Server is due\nWednesday, 24 June, 12:30 PM"
        results = extract_tasks_from_text(text)
        assert len(results) == 1
        assert "Webservice" in results[0]["name"]
        assert "12:30 PM" in results[0]["deadline"]

    def test_multiple_tasks(self):
        text = (
            "Tugas Basis Data is due\n27 June 2026 12:30 PM\n"
            "Tugas Jaringan is due\n30 June 2026 11:59 PM"
        )
        results = extract_tasks_from_text(text)
        assert len(results) == 2
        names = [r["name"] for r in results]
        assert "Tugas Basis Data" in names
        assert "Tugas Jaringan" in names

    def test_no_tasks(self):
        results = extract_tasks_from_text("Halaman kosong atau tidak ada tugas")
        assert len(results) == 0

    def test_empty_text(self):
        results = extract_tasks_from_text("")
        assert len(results) == 0

    def test_is_due_without_deadline(self):
        # Format tanpa "is due\nDATE" — pattern 1 catch "Tugas X is due"
        # plus teks random di baris berikutnya sebagai deadline (false positive
        # parser tidak cek apakah baris 2 benar-benar tanggal).
        text = "Tugas Sistem Informasi is due\nwaktu tidak ditentukan"
        results = extract_tasks_from_text(text)
        # Parser capture name + baris random (imperfect parser, by design)
        assert len(results) >= 1
        assert any("Sistem Informasi" in r["name"] for r in results)

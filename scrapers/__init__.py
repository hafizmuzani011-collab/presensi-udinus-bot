"""Scrapers package — kulino, siadin, mhs, khs."""
from .kulino import scrape_kulino_tugas, extract_tasks_from_text
from .siadin import (
    scrape_siadin_presensi,
    scrape_jadwal_ujian,
    _verify_presensi_success,
)
from .mhs import login_mhs_and_scrape_jadwal
from .khs import scrape_khs, scrape_khs_komponen
from .formatters import format_khs_message, format_attendance_message

__all__ = [
    "scrape_kulino_tugas",
    "extract_tasks_from_text",
    "scrape_siadin_presensi",
    "scrape_jadwal_ujian",
    "_verify_presensi_success",
    "login_mhs_and_scrape_jadwal",
    "scrape_khs",
    "scrape_khs_komponen",
    "format_khs_message",
    "format_attendance_message",
]

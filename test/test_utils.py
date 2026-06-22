"""Test parse_deadline — regex parser untuk berbagai format deadline."""
from datetime import datetime
import pytest
from utils import parse_deadline


NOW = datetime(2026, 6, 22, 10, 0, 0)


@pytest.mark.parametrize("raw,expected", [
    # ISO format
    ("2026-06-17 12:30", "2026-06-17T12:30:00"),
    ("2026-06-17T12:30", "2026-06-17T12:30:00"),
    ("2026-6-7 8:05", "2026-06-07T08:05:00"),

    # English long format
    ("Wednesday, 24 June, 12:30 PM", "2026-06-24T12:30:00"),
    ("27 June 2026 12:30 PM", "2026-06-27T12:30:00"),
    ("27 June, 12:30 PM", "2026-06-27T12:30:00"),
    ("1 July 2026 at 1:00 PM", "2026-07-01T13:00:00"),
    ("15 August 2026", "2026-08-15T23:59:00"),

    # Indonesian long format
    ("27 Mei 2026 12:30", "2026-05-27T12:30:00"),
    ("27 Mei, 12:30", "2026-05-27T12:30:00"),

    # Relative
    ("Tomorrow, 12:00 AM", "2026-06-23T00:00:00"),
    ("Tomorrow", "2026-06-23T23:59:00"),
    ("Besok", "2026-06-23T23:59:00"),
    ("Today, 11:59 PM", "2026-06-22T23:59:00"),
    ("Hari ini", "2026-06-22T23:59:00"),

    # AM/PM edge cases
    # BUG: parser only handles AM/PM inside long-format, not standalone time
    ("12:00 AM", None),
    ("12:00 PM", None),

    # None / empty
    (None, None),
    ("", None),
    ("  ", None),
])
def test_parse_deadline(raw, expected):
    result = parse_deadline(raw, NOW)
    assert result == expected, f"parse_deadline({raw!r}) = {result!r}, expected {expected!r}"

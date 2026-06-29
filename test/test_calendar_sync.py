"""Test for calendar_sync module."""
import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from icalendar import Calendar, Event

from storage import load_schedules
import calendar_sync
from config import SCHEDULES_FILE


def test_get_ical_url(monkeypatch):
    monkeypatch.setattr(calendar_sync, "GCAL_SAYA_ICAL_URL", "https://saya.ics")
    monkeypatch.setattr(calendar_sync, "GCAL_PACAR_ICAL_URL", "https://pacar.ics")

    assert calendar_sync.get_ical_url("saya") == "https://saya.ics"
    assert calendar_sync.get_ical_url("pacar") == "https://pacar.ics"
    assert calendar_sync.get_ical_url("other") == ""


@pytest.mark.asyncio
async def test_sync_gcal_schedule_no_url(monkeypatch):
    monkeypatch.setattr(calendar_sync, "get_ical_url", lambda _: "")
    ok, msg = await calendar_sync.sync_gcal_schedule("saya")
    assert ok is False
    assert "belum diset" in msg


class DummyResponse:
    def __init__(self, content, status_code):
        self.content = content
        self.status_code = status_code


class DummyClient:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def get(self, url):
        return self.response


@pytest.mark.asyncio
async def test_sync_gcal_schedule_http_error(monkeypatch):
    monkeypatch.setattr(calendar_sync, "get_ical_url", lambda _: "http://valid.url")

    async def mock_get(*args, **kwargs):
        return DummyResponse(b"", 404)

    monkeypatch.setattr("httpx.AsyncClient.get", mock_get)
    ok, msg = await calendar_sync.sync_gcal_schedule("saya")
    assert ok is False
    assert "HTTP 404" in msg


@pytest.mark.asyncio
async def test_sync_gcal_schedule_success(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(calendar_sync, "get_ical_url", lambda _: "http://valid.url")

    # Create dummy schedules.json
    schedules = {"saya": {}, "pacar": {}}
    Path(SCHEDULES_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(SCHEDULES_FILE).write_text(json.dumps(schedules))

    now = datetime.now()
    # Create event for tomorrow
    tmr = now + timedelta(days=1)
    tmr_start = tmr.replace(hour=10, minute=0, second=0, microsecond=0)
    tmr_end = tmr.replace(hour=11, minute=40, second=0, microsecond=0)

    cal = Calendar()
    cal.add('prodid', '-//Test//EN')
    cal.add('version', '2.0')

    event = Event()
    event.add('summary', 'Jaringan Komputer')
    event.add('location', 'Lab C.2')
    event.add('dtstart', tmr_start)
    event.add('dtend', tmr_end)
    cal.add_component(event)

    # Event far in future (should be ignored)
    far = now + timedelta(days=10)
    event2 = Event()
    event2.add('summary', 'Matematika')
    event2.add('dtstart', far)
    event2.add('dtend', far + timedelta(hours=2))
    cal.add_component(event2)

    ics_data = cal.to_ical()

    async def mock_get(*args, **kwargs):
        return DummyResponse(ics_data, 200)

    monkeypatch.setattr("httpx.AsyncClient.get", mock_get)

    ok, msg = await calendar_sync.sync_gcal_schedule("saya")
    assert ok is True
    assert "Berhasil" in msg
    assert "1" in msg

    # Verify data
    saved = load_schedules()
    tmr_day_name = tmr.strftime("%A").lower()
    from constants import HARI_ID
    hari_id = HARI_ID[tmr_day_name]

    assert len(saved["saya"][hari_id]) == 1
    slot = saved["saya"][hari_id][0]
    assert slot[0] == "10:00-11:40"
    assert slot[1] == "Jaringan Komputer"
    assert slot[2] == "Lab C.2"

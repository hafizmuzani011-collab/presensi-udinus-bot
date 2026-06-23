"""Test dashboard rate limiting — 429 responses."""
import json
import os
import pytest

DASH_TOKEN = "presensi123"


@pytest.fixture(autouse=True)
def setup_control():
    """Reset CONTROL + set DASH_TOKEN sebelum tiap test."""
    os.environ["DASH_TOKEN"] = DASH_TOKEN
    import web_dashboard
    web_dashboard.DASH_TOKEN = DASH_TOKEN
    with web_dashboard.CONTROL_LOCK:
        web_dashboard.CONTROL.clear()
        web_dashboard.CONTROL.update({"autopilot": True, "trigger_tugas": 0, "last_msg": ""})
    yield


@pytest.fixture
def client():
    """Flask test client for web_dashboard."""
    import web_dashboard
    web_dashboard.app.config["TESTING"] = True
    with web_dashboard.app.test_client() as c:
        yield c


class TestRateLimit:
    def test_trigger_tugas_rate_limit(self, client):
        resp1 = client.post("/control/trigger-tugas?token=presensi123")
        assert resp1.status_code == 200
        j1 = json.loads(resp1.data)
        assert j1.get("triggered") is True

        resp2 = client.post("/control/trigger-tugas?token=presensi123")
        # Should be 429 because < 60s since first call
        assert resp2.status_code == 429
        j2 = json.loads(resp2.data)
        assert "Rate limit" in j2.get("error", "")

    def test_trigger_presensi_rate_limit(self, client):
        resp1 = client.post("/control/trigger-presensi?token=presensi123&who=saya")
        assert resp1.status_code == 200

        resp2 = client.post("/control/trigger-presensi?token=presensi123&who=saya")
        assert resp2.status_code == 429
        j2 = json.loads(resp2.data)
        assert "Rate limit" in j2.get("error", "")

    def test_presensi_different_akun(self, client):
        # Beda akun harus independent rate limit
        resp1 = client.post("/control/trigger-presensi?token=presensi123&who=saya")
        assert resp1.status_code == 200

        resp2 = client.post("/control/trigger-presensi?token=presensi123&who=pacar")
        assert resp2.status_code == 200  # beda akun, no rate limit

    def test_invalid_who(self, client):
        resp = client.post("/control/trigger-presensi?token=presensi123&who=invalid")
        assert resp.status_code == 400

    def test_no_token(self, client):
        resp = client.post("/control/trigger-tugas")
        assert resp.status_code == 401


class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "ok"
        assert "uptime" in data
        assert "autopilot" in data

    def test_health_no_auth_required(self, client):
        # Health endpoint should be public (no token needed)
        resp = client.get("/health?token=wrong")
        assert resp.status_code == 200

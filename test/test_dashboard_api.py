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
        hdrs = {"Authorization": f"Bearer {DASH_TOKEN}"}
        resp1 = client.post("/control/trigger-tugas", headers=hdrs)
        assert resp1.status_code == 200
        j1 = json.loads(resp1.data)
        assert j1.get("triggered") is True

        resp2 = client.post("/control/trigger-tugas", headers=hdrs)
        # Should be 429 because < 60s since first call
        assert resp2.status_code == 429
        j2 = json.loads(resp2.data)
        assert "Rate limit" in j2.get("error", "")

    def test_trigger_presensi_rate_limit(self, client):
        hdrs = {"Authorization": f"Bearer {DASH_TOKEN}"}
        resp1 = client.post("/control/trigger-presensi?who=saya", headers=hdrs)
        assert resp1.status_code == 200

        resp2 = client.post("/control/trigger-presensi?who=saya", headers=hdrs)
        assert resp2.status_code == 429
        j2 = json.loads(resp2.data)
        assert "Rate limit" in j2.get("error", "")

    def test_presensi_different_akun(self, client):
        hdrs = {"Authorization": f"Bearer {DASH_TOKEN}"}
        # Beda akun harus independent rate limit
        resp1 = client.post("/control/trigger-presensi?who=saya", headers=hdrs)
        assert resp1.status_code == 200

        resp2 = client.post("/control/trigger-presensi?who=pacar", headers=hdrs)
        assert resp2.status_code == 200  # beda akun, no rate limit

    def test_invalid_who(self, client):
        hdrs = {"Authorization": f"Bearer {DASH_TOKEN}"}
        resp = client.post("/control/trigger-presensi?who=invalid", headers=hdrs)
        assert resp.status_code == 400


class TestHealth:
    def test_health_requires_auth_by_default(self, client):
        resp = client.get("/health")
        assert resp.status_code == 401

    def test_health_with_token(self, client):
        hdrs = {"Authorization": f"Bearer {DASH_TOKEN}"}
        resp = client.get("/health", headers=hdrs)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "ok"
        assert "uptime" in data
        assert "autopilot" in data

    def test_health_public_mode(self, client):
        """When HEALTH_PUBLIC=1, /health should be accessible without auth."""
        import config
        old = config.HEALTH_PUBLIC
        config.HEALTH_PUBLIC = True
        try:
            resp = client.get("/health")
            assert resp.status_code == 200
            data = json.loads(resp.data)
            assert data["status"] == "ok"
        finally:
            config.HEALTH_PUBLIC = old

class TestAccounts:
    def test_get_accounts(self, client):
        hdrs = {"Authorization": f"Bearer {DASH_TOKEN}"}
        resp = client.get("/api/accounts", headers=hdrs)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "kulino" in data
        assert "mhs" in data

    def test_save_and_delete_account(self, client):
        hdrs = {"Authorization": f"Bearer {DASH_TOKEN}", "Content-Type": "application/json"}
        payload = {
            "kulino": {
                "tester": {"nim": "123", "name": "Tester", "password": "pass"}
            }
        }
        resp = client.post("/api/accounts", headers=hdrs, data=json.dumps(payload))
        assert resp.status_code == 200
        assert json.loads(resp.data)["success"] is True

        # Verify it was added
        resp_get = client.get("/api/accounts", headers=hdrs)
        data = json.loads(resp_get.data)
        assert "tester" in data["kulino"]
        assert data["kulino"]["tester"]["nim"] == "123"
        # Password should not be leaked in GET response!
        assert "password" not in data["kulino"]["tester"]

        # Delete it
        resp_del = client.delete("/api/accounts/kulino/tester", headers=hdrs)
        assert resp_del.status_code == 200
        assert json.loads(resp_del.data)["success"] is True

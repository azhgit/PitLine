from fastapi.testclient import TestClient

from main import app

DASH = {"X-API-Key": "test-dashboard-key"}


def test_dashboard_key_reads_but_cannot_write():
    anon = TestClient(app)
    assert anon.get("/technicians", headers=DASH).status_code == 200
    assert anon.get("/technicians", params={"api_key": "test-dashboard-key"}).status_code == 200  # EventSource path
    # The dashboard's key is public - none of these may succeed with it.
    assert anon.post("/appointments", headers=DASH, json={}).status_code == 401
    assert anon.patch("/appointments/1", headers=DASH, params={"status": "cancelled"}).status_code == 401
    assert anon.post("/jobs/claim", headers=DASH).status_code == 401
    assert anon.post("/calls", headers=DASH, json={}).status_code == 401


def test_full_key_is_header_only():
    anon = TestClient(app)
    assert anon.get("/technicians", params={"api_key": "test-api-key"}).status_code == 401
    assert anon.get("/technicians", headers={"X-API-Key": "test-api-key"}).status_code == 200

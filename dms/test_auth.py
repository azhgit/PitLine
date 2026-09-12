from fastapi.testclient import TestClient

from main import app


def test_missing_api_key_rejected():
    resp = TestClient(app).get("/appointments")
    assert resp.status_code == 401


def test_wrong_api_key_rejected():
    resp = TestClient(app, headers={"X-API-Key": "wrong-key"}).get("/appointments")
    assert resp.status_code == 401


def test_correct_api_key_allowed(client):
    resp = client.get("/appointments")
    assert resp.status_code == 200


def test_health_check_unauthenticated():
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200

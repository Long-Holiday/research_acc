import os
import json
import pytest
from fastapi.testclient import TestClient
from server import app
import server
import server_modules.processor as processor

client = TestClient(app)
TEST_DB_PATH = "data/test_advisor_api.db"

def _cleanup():
    for ext in ["", "-wal", "-shm"]:
        p = TEST_DB_PATH + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

@pytest.fixture(autouse=True)
def setup_api_env(monkeypatch):
    server.ACCESS_PASSWORD = "testpassword"
    os.makedirs("data", exist_ok=True)
    _cleanup()
        
    monkeypatch.setattr(server, "DB_PATH", TEST_DB_PATH)
    monkeypatch.setattr(processor, "DB_PATH", TEST_DB_PATH)
    
    # Run schema creation
    processor.scan_and_process_files()

    yield
    _cleanup()

def get_auth_header():
    resp = client.post("/api/auth/login", json={"password": "testpassword"})
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}

def test_advisor_dates_empty():
    headers = get_auth_header()
    resp = client.get("/api/advisor/dates", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["dates"] == []

def test_advisor_report_not_found():
    headers = get_auth_header()
    resp = client.get("/api/advisor/report?date=2026-08-08", headers=headers)
    assert resp.status_code == 404

def test_advisor_settings_get_and_post():
    headers = get_auth_header()
    resp = client.get("/api/advisor/settings", headers=headers)
    assert resp.status_code == 200
    assert "topic" in resp.json()

    # Update topic
    new_topic = "高光谱遥感弱监督分类"
    resp = client.post("/api/advisor/settings", json={"topic": new_topic}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["topic"] == new_topic

    # Re-fetch
    resp = client.get("/api/advisor/settings", headers=headers)
    assert resp.json()["topic"] == new_topic

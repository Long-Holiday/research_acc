import pytest
from fastapi.testclient import TestClient
from server import app
import server
import server_modules.processor as processor
from server_modules.database import connect_db

client = TestClient(app)
@pytest.fixture(autouse=True)
def setup_api_env(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "ACCESS_PASSWORD", "testpassword")
    monkeypatch.setattr(server, "DB_PATH", str(tmp_path / "advisor.db"))
    monkeypatch.setattr(processor, "DB_PATH", server.DB_PATH)
    conn = connect_db(server.DB_PATH)
    processor._init_tables(conn.cursor())
    conn.commit()
    conn.close()


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


def test_advisor_report_repairs_malformed_ideas_json():
    conn = connect_db(server.DB_PATH)
    conn.execute(
        """
        INSERT INTO advisor_reports
            (report_date, topic, summary_takeaway, report_markdown, ideas_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("2026-08-08", "Remote Sensing", "摘要", "# 报告", '[{"title":"历史方案"}')
    )
    conn.commit()
    conn.close()

    resp = client.get("/api/advisor/report?date=2026-08-08", headers=get_auth_header())
    assert resp.status_code == 200
    assert resp.json()["ideas_json"][0]["title"] == "历史方案"

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


@pytest.mark.parametrize("method,path", [
    ("post", "/api/advisor/generate"),
    ("post", "/api/advisor/backfill"),
    ("get", "/api/advisor/generate/status?date=2026-08-08"),
])
def test_advisor_maintenance_endpoints_removed(method, path):
    response = getattr(client, method)(path, headers=get_auth_header())
    assert response.status_code == 404

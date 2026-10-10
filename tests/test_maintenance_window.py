from datetime import datetime
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.mark.parametrize("hour,minute,maintenance", [
    (3, 59, False), (4, 0, True), (4, 59, True), (5, 0, False),
])
def test_fixed_maintenance_window(monkeypatch, hour, minute, maintenance):
    monkeypatch.setattr(main, "local_now", lambda: datetime(2026, 10, 12, hour, minute))
    client = TestClient(main.app)
    page = client.get("/")
    api = client.post("/api/auth/login", json={"password": "wrong"})
    if maintenance:
        assert page.status_code == 503
        assert "论文正在更新" in page.text
        assert "04:00–05:00" in page.text
        assert "cdn" not in page.text
        assert api.status_code == 503
        assert "论文正在更新" in api.json()["detail"]
        assert api.headers["cache-control"] == "no-store"
        assert int(api.headers["retry-after"]) == (5 - hour) * 3600 - minute * 60
    else:
        assert page.status_code == 200
        assert api.status_code != 503


def test_maintenance_does_not_reach_business_routes(monkeypatch):
    monkeypatch.setattr(main, "local_now", lambda: datetime(2026, 10, 12, 4, 30))
    client = TestClient(main.app)
    with patch("server_modules.papers.query_papers", side_effect=AssertionError("must not query")), patch(
        "server_modules.advisor.connect_db", side_effect=AssertionError("must not query")
    ):
        for url in (
            "/api/papers?date=2026-10-12&lang=Chinese&page=1",
            "/api/advisor/report?date=2026-10-12",
            "/api/stats/categories?start_date=2026-10-01&end_date=2026-10-12",
        ):
            assert client.get(url).status_code == 503
        assert "论文正在更新" in client.get("/statistic.html").text
        assert "论文正在更新" in client.get("/advisor.html").text


def test_web_imports_no_processing_or_model_dependencies():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([
        sys.executable, "-c",
        "import server, sys; "
        "assert not any(m in sys.modules for m in "
        "('spacy', 'server_modules.processor', 'ai.advisor', 'langchain_openai', 'langchain')); "
        "assert not hasattr(__import__('app.main', fromlist=['']), 'periodic_file_scan')",
    ], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_startup_does_not_process_files(tmp_path, monkeypatch):
    import server
    import server_modules.processor as processor

    monkeypatch.setattr(server, "DB_PATH", str(tmp_path / "statistics.db"), raising=False)
    with patch.object(processor, "scan_and_process_files", side_effect=AssertionError("must not scan")):
        with TestClient(main.app) as client:
            assert client.get("/").status_code == 200

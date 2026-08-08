import pytest
from unittest.mock import MagicMock, patch
from ai.keyword_filter import heuristic_filter, filter_meaningless_keywords
from fastapi.testclient import TestClient
from server import app
import server

client = TestClient(app)

def get_auth_header():
    server.ACCESS_PASSWORD = "testpassword"
    resp = client.post("/api/auth/login", json={"password": "testpassword"})
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}

def test_heuristic_filter():
    keywords = [
        "proposed method",
        "novel approach",
        "YOLOv8",
        "Vision Transformer",
        "experimental results",
        "state-of-the-art",
        "accuracy",
        "Change Detection",
        "SAR",
        "good performance",
        "ablation study"
    ]
    excluded = heuristic_filter(keywords)
    assert "proposed method" in excluded
    assert "novel approach" in excluded
    assert "experimental results" in excluded
    assert "state-of-the-art" in excluded
    assert "accuracy" in excluded
    assert "ablation study" in excluded
    
    # Must preserve valuable keywords
    assert "YOLOv8" not in excluded
    assert "Vision Transformer" not in excluded
    assert "Change Detection" not in excluded
    assert "SAR" not in excluded

def test_filter_meaningless_keywords_fallback(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    
    keywords = ["novel method", "Remote Sensing", "U-Net", "performance analysis"]
    excluded = filter_meaningless_keywords(keywords, category="Remote Sensing")
    assert "novel method" in excluded
    assert "performance analysis" in excluded
    assert "Remote Sensing" not in excluded
    assert "U-Net" not in excluded

def test_filter_meaningless_keywords_llm_mock(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake_key")
    
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = '{"excluded_keywords": ["general framework", "novel technique"]}'
    mock_llm.invoke.return_value = mock_response
    
    with patch("ai.keyword_filter.init_llm", return_value=mock_llm):
        keywords = ["general framework", "novel technique", "Diffusion Model", "Sentinel-2"]
        excluded = filter_meaningless_keywords(keywords, category="CV")
        assert "general framework" in excluded
        assert "novel technique" in excluded
        assert "Diffusion Model" not in excluded
        assert "Sentinel-2" not in excluded

def test_api_stats_keywords_ai_filter():
    headers = get_auth_header()
    payload = {
        "keywords": ["proposed approach", "comparative study", "CLIP", "Deep Learning"],
        "category": "All"
    }
    resp = client.post("/api/stats/keywords/ai-filter", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "excluded_keywords" in data
    assert "proposed approach" in data["excluded_keywords"]
    assert "comparative study" in data["excluded_keywords"]
    assert "CLIP" not in data["excluded_keywords"]
    assert data["total_checked"] == 4

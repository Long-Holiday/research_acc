import os
import json
from fastapi.testclient import TestClient
from server import app

client = TestClient(app)

def test_index_page():
    # Test root route
    response = client.get("/")
    assert response.status_code == 200
    assert "Daily arXiv AI Enhanced" in response.text

    # Test /index.html route
    response = client.get("/index.html")
    assert response.status_code == 200
    assert "Daily arXiv AI Enhanced" in response.text

def test_login_page():
    response = client.get("/login.html")
    assert response.status_code == 200
    assert "Access Verification" in response.text or "daily-arXiv-ai-enhanced" in response.text

def test_settings_page():
    response = client.get("/settings.html")
    assert response.status_code == 200
    assert "Settings" in response.text

def test_statistic_page():
    response = client.get("/statistic.html")
    assert response.status_code == 200
    assert "Statistics" in response.text

def test_static_files():
    # Test mounting and serving of static files (e.g. css/styles.css)
    response = client.get("/css/styles.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")

def test_auth_and_data_apis():
    # Configure fake password
    import server
    server.ACCESS_PASSWORD = "testpassword"
    
    # Unauthenticated access should fail
    response = client.get("/api/dates")
    assert response.status_code == 401

    # Login with bad password
    response = client.post("/api/auth/login", json={"password": "bad"})
    assert response.status_code == 401

    # Login with good password
    response = client.post("/api/auth/login", json={"password": "testpassword"})
    assert response.status_code == 200
    token = response.json()["token"]
    assert token is not None

    headers = {"Authorization": f"Bearer {token}"}
    
    # Check auth
    response = client.post("/api/auth/check", headers=headers)
    assert response.status_code == 200
    assert response.json()["authenticated"] is True

    # Setup temp data folder for testing
    os.makedirs("data", exist_ok=True)
    test_file = "data/2026-07-09_AI_enhanced_Chinese.jsonl"
    with open(test_file, "w") as f:
        f.write(json.dumps({"title": "Test Paper", "authors": ["Author 1"], "categories": ["cs.CV"], "AI": {"tldr": "Tldr"}}) + "\n")
    
    try:
        # Get dates
        response = client.get("/api/dates", headers=headers)
        assert response.status_code == 200
        assert "2026-07-09" in response.json()["dates"]

        # Get papers
        response = client.get("/api/papers?date=2026-07-09&lang=Chinese", headers=headers)
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["title"] == "Test Paper"
    finally:
        if os.path.exists(test_file):
            os.remove(test_file)
        # Restore ACCESS_PASSWORD
        server.ACCESS_PASSWORD = ""

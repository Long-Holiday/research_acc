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

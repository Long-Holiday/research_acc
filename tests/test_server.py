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
    old_password = server.ACCESS_PASSWORD
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
        f.write(json.dumps({
            "id": "123",
            "title": "Test Paper", 
            "authors": ["Author 1"], 
            "categories": ["cs.CV"], 
            "AI": {
                "translated_title": "测试论文标题",
                "tldr": "Tldr"
            }
        }) + "\n")
    
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
        assert response.json()[0]["AI"]["translated_title"] == "测试论文标题"

        # Invalid date format should return 400
        response = client.get("/api/papers?date=2026/07/09&lang=Chinese", headers=headers)
        assert response.status_code == 400

        # Invalid lang format should return 400
        response = client.get("/api/papers?date=2026-07-09&lang=../Chinese", headers=headers)
        assert response.status_code == 400

        # Test keyword stats API
        response = client.get("/api/stats/keywords?start_date=2026-07-09&end_date=2026-07-09&lang=Chinese&category=All", headers=headers)
        assert response.status_code == 200
        res_data = response.json()
        assert "keywords" in res_data
        assert "daily_trends" in res_data
        kws = [k["keyword"] for k in res_data["keywords"]]
        assert "test" in kws

        # Test network stats API
        response = client.get("/api/stats/network?start_date=2026-07-09&end_date=2026-07-09&lang=Chinese&category=All", headers=headers)
        assert response.status_code == 200
        res_net = response.json()
        assert "nodes" in res_net
        assert "links" in res_net
    finally:
        if os.path.exists(test_file):
            os.remove(test_file)
        # Restore ACCESS_PASSWORD
        server.ACCESS_PASSWORD = old_password


def test_stats_apis():
    # Configure fake password
    import server
    old_password = server.ACCESS_PASSWORD
    server.ACCESS_PASSWORD = "testpassword"
    
    # Login
    response = client.post("/api/auth/login", json={"password": "testpassword"})
    assert response.status_code == 200
    token = response.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # Setup temp data folder for testing
    os.makedirs("data", exist_ok=True)
    test_file = "data/2026-07-09_AI_enhanced_Chinese.jsonl"
    with open(test_file, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "id": "paper_1",
            "title": "Proactive Context Graphs for Enterprise Agents",
            "summary": "Retrieval-Augmented Generation RAG systems should be proactive enterprise agents.",
            "categories": ["cs.AI", "cs.LG"]
        }) + "\n")
        f.write(json.dumps({
            "id": "paper_2",
            "title": "Active Graphs and Enterprise Networks",
            "summary": "We study active context graphs in enterprise networks.",
            "categories": ["cs.AI"]
        }) + "\n")
        
    try:
        # Clear stats database to ensure a clean slate
        db_path = "data/statistics.db"
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception:
                pass

        # Trigger API keywords
        response = client.get("/api/stats/keywords?start_date=2026-07-09&end_date=2026-07-09&lang=Chinese&category=All", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert "keywords" in data
        assert "daily_trends" in data
        
        keywords = [k["keyword"] for k in data["keywords"]]
        assert "graphs" in keywords or "enterprise" in keywords
        assert len(data["daily_trends"]) > 0
        
        # Trigger API network
        response = client.get("/api/stats/network?start_date=2026-07-09&end_date=2026-07-09&lang=Chinese&category=All", headers=headers)
        assert response.status_code == 200
        net_data = response.json()
        assert "nodes" in net_data
        assert "links" in net_data
        
        node_ids = [n["id"] for n in net_data["nodes"]]
        assert "graphs" in node_ids or "enterprise" in node_ids
        
    finally:
        if os.path.exists(test_file):
            os.remove(test_file)
        db_path = "data/statistics.db"
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception:
                pass
        server.ACCESS_PASSWORD = old_password


def test_papers_range_api():
    # Configure fake password
    import server
    old_password = server.ACCESS_PASSWORD
    server.ACCESS_PASSWORD = "testpassword"
    
    # Login
    response = client.post("/api/auth/login", json={"password": "testpassword"})
    assert response.status_code == 200
    token = response.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # Setup temp data folder for testing
    os.makedirs("data", exist_ok=True)
    test_file_1 = "data/2020-07-08_AI_enhanced_Chinese.jsonl"
    test_file_2 = "data/2020-07-09_AI_enhanced_Chinese.jsonl"
    
    with open(test_file_1, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "id": "paper_old",
            "title": "Old Paper title",
            "summary": "Old summary",
            "categories": ["cs.AI"]
        }) + "\n")
        
    with open(test_file_2, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "id": "paper_new",
            "title": "New Paper title",
            "summary": "New summary",
            "categories": ["cs.LG"]
        }) + "\n")
        
    try:
        # Clear stats database to ensure a clean slate
        db_path = "data/statistics.db"
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception:
                pass

        # Trigger API papers range for date range containing both
        response = client.get("/api/papers/range?start_date=2020-07-08&end_date=2020-07-09&lang=Chinese", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        
        ids = [p["id"] for p in data]
        assert "paper_old" in ids
        assert "paper_new" in ids
        
        # Test out of range
        response = client.get("/api/papers/range?start_date=2020-07-10&end_date=2020-07-11&lang=Chinese", headers=headers)
        assert response.status_code == 200
        assert len(response.json()) == 0
        
        # Test invalid parameters
        response = client.get("/api/papers/range?start_date=2020/07/08&end_date=2020-07-09&lang=Chinese", headers=headers)
        assert response.status_code == 400
        
    finally:
        if os.path.exists(test_file_1):
            os.remove(test_file_1)
        if os.path.exists(test_file_2):
            os.remove(test_file_2)
        db_path = "data/statistics.db"
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception:
                pass
        server.ACCESS_PASSWORD = old_password


def test_hot_papers_apis():
    # Setup test password and login
    import server
    old_password = server.ACCESS_PASSWORD
    server.ACCESS_PASSWORD = "testpassword"
    
    response = client.post("/api/auth/login", json={"password": "testpassword"})
    assert response.status_code == 200
    token = response.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # 1. 测试获取期刊接口
    response = client.get("/api/stats/journals", headers=headers)
    assert response.status_code == 200
    journals = response.json()
    assert len(journals) > 0
    assert "TGRS" in [j["name"] for j in journals]
    
    # 2. 测试获取热门论文（模拟 OpenAlex 查询或确保缓存及接口能正常响应）
    response = client.get("/api/stats/hot-papers?journal=TGRS&period=30", headers=headers)
    assert response.status_code in [200, 500] # 如果没配置 API key 或访问不通可能报 500
    
    # 测试非法 period
    response = client.get("/api/stats/hot-papers?journal=TGRS&period=5", headers=headers)
    assert response.status_code == 400
    
    # 测试非法 journal
    response = client.get("/api/stats/hot-papers?journal=INVALID&period=30", headers=headers)
    assert response.status_code == 404
    
    server.ACCESS_PASSWORD = old_password


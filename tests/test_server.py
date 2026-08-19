import os
import json
from fastapi.testclient import TestClient
from server import app

client = TestClient(app)

def test_index_page():
    # Test root route
    response = client.get("/")
    assert response.status_code == 200
    assert "Make RS Great Again" in response.text

    # Test /index.html route
    response = client.get("/index.html")
    assert response.status_code == 200
    assert "Make RS Great Again" in response.text

def test_login_page():
    response = client.get("/login.html")
    assert response.status_code == 200
    assert "Access Verification" in response.text or "Make RS Great Again" in response.text

def test_settings_page():
    response = client.get("/settings.html")
    assert response.status_code == 200
    assert "Settings" in response.text

def test_statistic_page():
    response = client.get("/statistic.html")
    assert response.status_code == 200
    assert "Statistics" in response.text

def test_advisor_page():
    response = client.get("/advisor.html")
    assert response.status_code == 200
    assert "学术导师" in response.text or "Academic Advisor" in response.text

def test_static_files():
    # Test mounting and serving of static files (e.g. css/styles.css)
    response = client.get("/css/styles.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")

def test_auth_and_data_apis():
    # Configure fake password
    import server
    import server_modules.processor as processor
    
    old_password = server.ACCESS_PASSWORD
    server.ACCESS_PASSWORD = "testpassword"
    
    # Redirect DB paths to temp test database
    old_server_db_path = server.DB_PATH
    old_processor_db_path = processor.DB_PATH
    server.DB_PATH = "data/test_statistics.db"
    processor.DB_PATH = "data/test_statistics.db"
    if os.path.exists("data/test_statistics.db"):
        try:
            os.remove("data/test_statistics.db")
        except Exception:
            pass
    
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
            "summary": "This test paper presents a novel computer vision method.",
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

        # Test categories stats API
        response = client.get("/api/stats/categories?start_date=2026-07-09&end_date=2026-07-09&lang=Chinese", headers=headers)
        assert response.status_code == 200
        res_cat = response.json()
        assert "categories" in res_cat
        assert "category_counts" in res_cat
        assert "total_all" in res_cat
        assert "cs.CV" in res_cat["categories"]
        assert res_cat["category_counts"]["cs.CV"] >= 1
        assert res_cat["total_all"] >= 1

        # Test network stats API
        response = client.get("/api/stats/network?start_date=2026-07-09&end_date=2026-07-09&lang=Chinese&category=All", headers=headers)
        assert response.status_code == 200
        res_net = response.json()
        assert "nodes" in res_net
        assert "links" in res_net
    finally:
        if os.path.exists(test_file):
            os.remove(test_file)
        # Clean up test database
        if os.path.exists("data/test_statistics.db"):
            try:
                os.remove("data/test_statistics.db")
            except Exception:
                pass
        # Restore DB paths
        server.DB_PATH = old_server_db_path
        processor.DB_PATH = old_processor_db_path
        # Restore ACCESS_PASSWORD
        server.ACCESS_PASSWORD = old_password


def test_stats_apis():
    # Configure fake password
    import server
    import server_modules.processor as processor
    old_password = server.ACCESS_PASSWORD
    server.ACCESS_PASSWORD = "testpassword"
    
    # Redirect DB paths to temp test database
    old_server_db_path = server.DB_PATH
    old_processor_db_path = processor.DB_PATH
    server.DB_PATH = "data/test_statistics.db"
    processor.DB_PATH = "data/test_statistics.db"
    
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
        test_db_path = "data/test_statistics.db"
        if os.path.exists(test_db_path):
            try:
                os.remove(test_db_path)
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
        
        # Test that exclude parameter correctly filters out nodes in network
        response = client.get("/api/stats/network?start_date=2026-07-09&end_date=2026-07-09&lang=Chinese&category=All&exclude=graphs,enterprise", headers=headers)
        assert response.status_code == 200
        net_data_exclude = response.json()
        node_ids_exclude = [n["id"] for n in net_data_exclude["nodes"]]
        assert "graphs" not in node_ids_exclude
        assert "enterprise" not in node_ids_exclude
        
    finally:
        if os.path.exists(test_file):
            os.remove(test_file)
        test_db_path = "data/test_statistics.db"
        if os.path.exists(test_db_path):
            try:
                os.remove(test_db_path)
            except Exception:
                pass
        # Restore DB paths
        server.DB_PATH = old_server_db_path
        processor.DB_PATH = old_processor_db_path
        server.ACCESS_PASSWORD = old_password


def test_papers_range_api():
    # Configure fake password
    import server
    import server_modules.processor as processor
    old_password = server.ACCESS_PASSWORD
    server.ACCESS_PASSWORD = "testpassword"
    
    # Redirect DB paths to temp test database
    old_server_db_path = server.DB_PATH
    old_processor_db_path = processor.DB_PATH
    server.DB_PATH = "data/test_statistics.db"
    processor.DB_PATH = "data/test_statistics.db"
    
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
        test_db_path = "data/test_statistics.db"
        if os.path.exists(test_db_path):
            try:
                os.remove(test_db_path)
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
        test_db_path = "data/test_statistics.db"
        if os.path.exists(test_db_path):
            try:
                os.remove(test_db_path)
            except Exception:
                pass
        # Restore DB paths
        server.DB_PATH = old_server_db_path
        processor.DB_PATH = old_processor_db_path
        server.ACCESS_PASSWORD = old_password


def test_hot_papers_apis():
    # Setup test password and login
    import server
    import server_modules.processor as processor
    old_password = server.ACCESS_PASSWORD
    server.ACCESS_PASSWORD = "testpassword"
    
    # Redirect DB paths to temp test database
    old_server_db_path = server.DB_PATH
    old_processor_db_path = processor.DB_PATH
    server.DB_PATH = "data/test_statistics.db"
    processor.DB_PATH = "data/test_statistics.db"
    
    try:
        # Clear stats database to ensure a clean slate
        test_db_path = "data/test_statistics.db"
        if os.path.exists(test_db_path):
            try:
                os.remove(test_db_path)
            except Exception:
                pass
        
        # Create database and hot_papers_cache table for test environment
        os.makedirs(os.path.dirname(test_db_path), exist_ok=True)
        conn = server.connect_db(test_db_path)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS hot_papers_cache (
            journal TEXT,
            period INTEGER,
            query_date TEXT,
            papers_json TEXT,
            PRIMARY KEY (journal, period, query_date)
        )
        """)
        conn.commit()
        conn.close()

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
        from unittest.mock import patch, MagicMock
        
        mock_response_data = {
            "results": [
                {
                    "id": "https://openalex.org/W12345",
                    "title": "Mocked Hot Paper",
                    "authorships": [
                        {
                            "author": {
                                "display_name": "John Doe"
                            }
                        }
                    ],
                    "cited_by_count": 42,
                    "doi": "https://doi.org/10.1000/xyz123",
                    "primary_location": {
                        "landing_page_url": "https://example.com/mocked"
                    },
                    "publication_date": "2026-07-01"
                }
            ]
        }
        
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_response_data
            mock_get.return_value = mock_resp
            
            response = client.get("/api/stats/hot-papers?journal=TGRS&period=30", headers=headers)
            assert response.status_code == 200
            papers = response.json()
            assert len(papers) == 1
            assert papers[0]["title"] == "Mocked Hot Paper"
            assert papers[0]["authors"] == "John Doe"
            assert papers[0]["cited_by_count"] == 42
            assert papers[0]["url"] == "https://doi.org/10.1000/xyz123"
            assert papers[0]["publication_date"] == "2026-07-01"
            
            # Calculate expected citations per day
            from datetime import datetime
            expected_days = (datetime.now() - datetime.strptime("2026-07-01", "%Y-%m-%d")).days
            expected_days = max(expected_days, 1)
            expected_rate = round(42 / expected_days, 2)
            assert papers[0]["citations_per_day"] == expected_rate
            
            mock_get.assert_called_once()
            
            # Verify Cache Hit: Second request must use cached result without calling requests.get again
            response2 = client.get("/api/stats/hot-papers?journal=TGRS&period=30", headers=headers)
            assert response2.status_code == 200
            papers2 = response2.json()
            assert len(papers2) == 1
            assert papers2[0]["title"] == "Mocked Hot Paper"
            assert papers2[0]["citations_per_day"] == expected_rate
            assert mock_get.call_count == 1
        
        # 测试非法 period
        response = client.get("/api/stats/hot-papers?journal=TGRS&period=5", headers=headers)
        assert response.status_code == 400
        
        # 测试非法 journal
        response = client.get("/api/stats/hot-papers?journal=INVALID&period=30", headers=headers)
        assert response.status_code == 404
    finally:
        test_db_path = "data/test_statistics.db"
        if os.path.exists(test_db_path):
            try:
                os.remove(test_db_path)
            except Exception:
                pass
        # Restore DB paths
        server.DB_PATH = old_server_db_path
        processor.DB_PATH = old_processor_db_path
        server.ACCESS_PASSWORD = old_password


def test_keyword_growth_rate_is_confidence_adjusted():
    # Configure fake password
    import server
    import server_modules.processor as processor
    old_password = server.ACCESS_PASSWORD
    server.ACCESS_PASSWORD = "testpassword"
    
    # Redirect DB paths to temp test database
    old_server_db_path = server.DB_PATH
    old_processor_db_path = processor.DB_PATH
    server.DB_PATH = "data/test_statistics.db"
    processor.DB_PATH = "data/test_statistics.db"
    
    # Login
    response = client.post("/api/auth/login", json={"password": "testpassword"})
    assert response.status_code == 200
    token = response.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # Setup temp data folder for testing
    os.makedirs("data", exist_ok=True)
    
    test_file_0 = "data/2026-07-07_AI_enhanced_Chinese.jsonl"
    test_file_1 = "data/2026-07-08_AI_enhanced_Chinese.jsonl"
    test_file_2 = "data/2026-07-09_AI_enhanced_Chinese.jsonl"
    
    # We want "noisy" keyword to appear < 5 times total (e.g. 3 times)
    # We want "frequent" keyword to appear >= 5 times total (e.g. 18 times)
    
    with open(test_file_0, "w", encoding="utf-8") as f:
        # Frequent appears 1 time on Day 0 (count 3). Noisy does not appear (count 0).
        f.write(json.dumps({"id": "p0_1", "title": "frequent", "summary": "", "categories": ["cs.AI"]}) + "\n")
        
    with open(test_file_1, "w", encoding="utf-8") as f:
        # Frequent appears 2 times on Day 1 (count 6). Noisy appears 1 time in summary (count 1).
        f.write(json.dumps({"id": "p1_1", "title": "frequent", "summary": "", "categories": ["cs.AI"]}) + "\n")
        f.write(json.dumps({"id": "p1_2", "title": "frequent", "summary": "noisy", "categories": ["cs.AI"]}) + "\n")
        
    with open(test_file_2, "w", encoding="utf-8") as f:
        # Frequent appears 3 times on Day 2 (count 9). Noisy appears 2 times in summaries (count 2).
        f.write(json.dumps({"id": "p2_1", "title": "frequent", "summary": "", "categories": ["cs.AI"]}) + "\n")
        f.write(json.dumps({"id": "p2_2", "title": "frequent", "summary": "noisy", "categories": ["cs.AI"]}) + "\n")
        f.write(json.dumps({"id": "p2_3", "title": "frequent", "summary": "noisy", "categories": ["cs.AI"]}) + "\n")
        
    try:
        # Clear stats database to ensure a clean slate
        test_db_path = "data/test_statistics.db"
        if os.path.exists(test_db_path):
            try:
                os.remove(test_db_path)
            except Exception:
                pass

        # Trigger API keywords
        response = client.get(
            "/api/stats/keywords?start_date=2026-07-07&end_date=2026-07-09&lang=Chinese&category=All",
            headers=headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "keywords" in data
        
        # Check keyword stats
        kw_map = {k["keyword"]: k for k in data["keywords"]}
        
        assert "frequent" in kw_map
        assert kw_map["frequent"]["count"] == 6
        assert kw_map["frequent"]["growth_rate"] == 0.0
        
        # Now check "noisy".
        assert "noisy" in kw_map
        assert kw_map["noisy"]["count"] == 3
        # A three-hit keyword may have a positive raw window change, but it is
        # not strong enough to be presented as a reliable emerging trend.
        assert kw_map["noisy"]["growth_rate"] > 0
        assert kw_map["noisy"]["trend_confidence"] < 0.2
        assert kw_map["noisy"]["trend_score"] == 0.0
        
    finally:
        for tf in [test_file_0, test_file_1, test_file_2]:
            if os.path.exists(tf):
                try:
                    os.remove(tf)
                except Exception:
                    pass
        test_db_path = "data/test_statistics.db"
        if os.path.exists(test_db_path):
            try:
                os.remove(test_db_path)
            except Exception:
                pass
        # Restore DB paths
        server.DB_PATH = old_server_db_path
        processor.DB_PATH = old_processor_db_path
        server.ACCESS_PASSWORD = old_password


def test_papers_pagination():
    import server
    old_password = server.ACCESS_PASSWORD
    server.ACCESS_PASSWORD = "testpassword"
    
    # 登录获取 token
    login_resp = client.post("/api/auth/login", json={"password": "testpassword"})
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    test_file = "data/2026-07-20_AI_enhanced_Chinese.jsonl"
    os.makedirs("data", exist_ok=True)
    
    # 写入 5 篇测试论文
    with open(test_file, "w", encoding="utf-8") as f:
        for i in range(1, 6):
            cat = "cs.CV" if i <= 3 else "cs.AI"
            author = "Alice" if i % 2 == 1 else "Bob"
            f.write(json.dumps({
                "id": f"paper_{i}",
                "title": f"Paper Title {i}",
                "authors": [author],
                "categories": [cat],
                "summary": f"Summary of paper {i} with machine learning content.",
                "AI": {
                    "translated_title": f"论文标题 {i}",
                    "tldr": f"TLDR {i}"
                }
            }) + "\n")
            
    try:
        # 1. 不传 page 参数：应该返回全量列表 5 条（向下兼容）
        resp_full = client.get("/api/papers?date=2026-07-20&lang=Chinese", headers=headers)
        assert resp_full.status_code == 200
        data_full = resp_full.json()
        assert isinstance(data_full, list)
        assert len(data_full) == 5

        # 2. 传 page=1, page_size=2：返回第 1 页 2 条，total=5, total_pages=3
        resp_p1 = client.get("/api/papers?date=2026-07-20&lang=Chinese&page=1&page_size=2", headers=headers)
        assert resp_p1.status_code == 200
        data_p1 = resp_p1.json()
        assert isinstance(data_p1, dict)
        assert data_p1["total"] == 5
        assert data_p1["page"] == 1
        assert data_p1["page_size"] == 2
        assert data_p1["total_pages"] == 3
        assert len(data_p1["items"]) == 2
        assert data_p1["items"][0]["id"] == "paper_1"
        assert data_p1["items"][1]["id"] == "paper_2"
        assert "cs.CV" in data_p1["category_counts"]
        assert data_p1["category_counts"]["cs.CV"] == 3
        assert data_p1["category_counts"]["cs.AI"] == 2

        # 3. 传 page=3, page_size=2：返回最后一页 1 条
        resp_p3 = client.get("/api/papers?date=2026-07-20&lang=Chinese&page=3&page_size=2", headers=headers)
        assert resp_p3.status_code == 200
        data_p3 = resp_p3.json()
        assert len(data_p3["items"]) == 1
        assert data_p3["items"][0]["id"] == "paper_5"

        # 4. 传 category="cs.AI" 进行分页过滤
        resp_cat = client.get("/api/papers?date=2026-07-20&lang=Chinese&page=1&page_size=10&category=cs.AI", headers=headers)
        assert resp_cat.status_code == 200
        data_cat = resp_cat.json()
        assert data_cat["total"] == 2
        assert len(data_cat["items"]) == 2
        assert all("cs.AI" in p["categories"] for p in data_cat["items"])

        # 5. 传 keyword 搜索过滤
        resp_kw = client.get("/api/papers?date=2026-07-20&lang=Chinese&page=1&page_size=10&keyword=paper+1", headers=headers)
        assert resp_kw.status_code == 200
        data_kw = resp_kw.json()
        assert data_kw["total"] == 1
        assert data_kw["items"][0]["id"] == "paper_1"

        # 6. 传 author 过滤
        resp_author = client.get("/api/papers?date=2026-07-20&lang=Chinese&page=1&page_size=10&author=Alice", headers=headers)
        assert resp_author.status_code == 200
        data_author = resp_author.json()
        assert data_author["total"] == 3
    finally:
        if os.path.exists(test_file):
            try:
                os.remove(test_file)
            except Exception:
                pass
        server.ACCESS_PASSWORD = old_password


def test_papers_range_pagination():
    import server
    old_password = server.ACCESS_PASSWORD
    server.ACCESS_PASSWORD = "testpassword"
    
    # 登录获取 token
    login_resp = client.post("/api/auth/login", json={"password": "testpassword"})
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    test_file = "data/2026-07-21_AI_enhanced_Chinese.jsonl"
    os.makedirs("data", exist_ok=True)
    
    with open(test_file, "w", encoding="utf-8") as f:
        for i in range(1, 4):
            f.write(json.dumps({
                "id": f"range_paper_{i}",
                "title": f"Range Paper Title {i}",
                "authors": ["Carol"],
                "categories": ["cs.CV"],
                "summary": f"Summary {i}",
                "AI": {"translated_title": f"范围论文 {i}"}
            }) + "\n")
            
    try:
        # 分页查询 range
        resp = client.get("/api/papers/range?start_date=2026-07-21&end_date=2026-07-21&lang=Chinese&page=1&page_size=2", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert data["total"] == 3
        assert data["page"] == 1
        assert data["page_size"] == 2
        assert data["total_pages"] == 2
        assert len(data["items"]) == 2

        # 兼容模式：未传 page 返回 list
        resp_full = client.get("/api/papers/range?start_date=2026-07-21&end_date=2026-07-21&lang=Chinese", headers=headers)
        assert resp_full.status_code == 200
        assert isinstance(resp_full.json(), list)
        assert len(resp_full.json()) == 3
    finally:
        if os.path.exists(test_file):
            try:
                os.remove(test_file)
            except Exception:
                pass
        server.ACCESS_PASSWORD = old_password


def test_reextract_keywords_api():
    import server
    import server_modules.processor as processor
    import time

    old_password = server.ACCESS_PASSWORD
    server.ACCESS_PASSWORD = "testpassword"

    old_server_db_path = server.DB_PATH
    old_processor_db_path = processor.DB_PATH
    server.DB_PATH = "data/test_statistics.db"
    processor.DB_PATH = "data/test_statistics.db"

    # 未认证测试
    unauth_post = client.post("/api/stats/reextract-keywords")
    assert unauth_post.status_code == 401
    unauth_get = client.get("/api/stats/reextract-status")
    assert unauth_get.status_code == 401

    # 登录
    login_resp = client.post("/api/auth/login", json={"password": "testpassword"})
    assert login_resp.status_code == 200
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 初始状态获取
    status_resp = client.get("/api/stats/reextract-status", headers=headers)
    assert status_resp.status_code == 200
    assert "status" in status_resp.json()

    # 构造测试数据
    os.makedirs("data", exist_ok=True)
    test_file = "data/2026-07-22_AI_enhanced_Chinese.jsonl"
    with open(test_file, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "id": "reextract_p1",
            "title": "Quantum Reinforcement Learning (QRL) Agents",
            "summary": "We study Quantum Reinforcement Learning (QRL) for autonomous systems.",
            "categories": ["cs.AI"]
        }) + "\n")

    try:
        # 直接执行重新提取函数并验证
        success = processor.reextract_all_keywords()
        assert success is True

        status = processor.get_reextract_status()
        assert status["status"] == "completed"
        assert status["progress"] == 100

        # API 触发测试
        trigger_resp = client.post("/api/stats/reextract-keywords", headers=headers)
        assert trigger_resp.status_code == 200
        assert trigger_resp.json()["status"] in ("started", "running")

        # 检查重提取后关键词查询接口是否正常返回最新提取的词
        time.sleep(0.5)
        kw_resp = client.get(
            "/api/stats/keywords?start_date=2026-07-22&end_date=2026-07-22&lang=Chinese&category=All",
            headers=headers
        )
        assert kw_resp.status_code == 200
        kw_data = kw_resp.json()
        assert "keywords" in kw_data
        kw_names = [k["keyword"] for k in kw_data["keywords"]]
        # 应该识别出 "quantum reinforcement learning" 或 "qrl" 或 "reinforcement learning"
        assert any("reinforcement" in name or "quantum" in name or "qrl" in name for name in kw_names)

    finally:
        if os.path.exists(test_file):
            try:
                os.remove(test_file)
            except Exception:
                pass
        server.ACCESS_PASSWORD = old_password
        server.DB_PATH = old_server_db_path
        processor.DB_PATH = old_processor_db_path





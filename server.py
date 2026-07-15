import os
import uuid
import time
import json
import re
import sqlite3
import threading
import asyncio
from datetime import datetime, timedelta
from fastapi import FastAPI, Depends, HTTPException, Header, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pydantic import BaseModel

# Import modular components
from server_modules.database import connect_db
from server_modules.processor import scan_and_process_files
from server_modules.analytics import community_detection

load_dotenv()

# 清理环境变量中的空白符和换行符，防止因 \r 导致解析报错
for k in os.environ:
    os.environ[k] = os.environ[k].strip()

ACCESS_PASSWORD = os.getenv("ACCESS_PASSWORD", "")
active_sessions = {}  # token -> expiry_timestamp

DB_PATH = "data/statistics.db"

class LoginRequest(BaseModel):
    password: str

def verify_token(authorization: str = Header(None)):
    if not ACCESS_PASSWORD:
        return "anonymous"
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid token")
    token = authorization.split(" ")[1]
    expiry = active_sessions.get(token)
    if not expiry or time.time() > expiry:
        active_sessions.pop(token, None)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired or invalid")
    return token

app = FastAPI(title="Daily arXiv AI Enhanced Server")

@app.post("/api/auth/login")
def login(req: LoginRequest):
    if not ACCESS_PASSWORD:
        return {"status": "success", "token": "anonymous_token", "expire": 0}
    if req.password == ACCESS_PASSWORD:
        token = str(uuid.uuid4())
        expire_at = time.time() + 7 * 24 * 3600
        active_sessions[token] = expire_at
        return {"status": "success", "token": token, "expire": int(expire_at * 1000)}
    raise HTTPException(status_code=401, detail="Invalid password")

@app.post("/api/auth/check")
def check_auth(token: str = Depends(verify_token)):
    return {"authenticated": True}

@app.get("/api/dates")
def get_dates(token: str = Depends(verify_token)):
    data_dir = "data"
    if not os.path.exists(data_dir):
        return {"dates": [], "languages": {}}
    
    files = os.listdir(data_dir)
    dates_set = set()
    languages_map = {} # date -> list of languages
    
    # Parse YYYY-MM-DD_AI_enhanced_{lang}.jsonl
    for f in files:
        if f.endswith(".jsonl") and "_AI_enhanced_" in f:
            parts = f.replace(".jsonl", "").split("_AI_enhanced_")
            if len(parts) == 2:
                date_str, lang = parts[0], parts[1]
                dates_set.add(date_str)
                languages_map.setdefault(date_str, []).append(lang)
                
    sorted_dates = sorted(list(dates_set), reverse=True)
    return {"dates": sorted_dates, "languages": languages_map}

@app.get("/api/papers")
def get_papers(date: str, lang: str, token: str = Depends(verify_token)):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date) or not re.match(r"^[a-zA-Z]+$", lang):
        raise HTTPException(status_code=400, detail="Invalid date or language format")
        
    filepath = f"data/{date}_AI_enhanced_{lang}.jsonl"
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Papers not found for this date and language")
    
    papers = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    papers.append(json.loads(line.strip()))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read data: {str(e)}")
    return papers

@app.get("/api/papers/range")
def get_papers_range(
    start_date: str,
    end_date: str,
    lang: str = "en",
    token: str = Depends(verify_token)
):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", start_date) or not re.match(r"^\d{4}-\d{2}-\d{2}$", end_date) or not re.match(r"^[a-zA-Z]+$", lang):
        raise HTTPException(status_code=400, detail="Invalid date or language format")
        
    try:
        scan_and_process_files()
    except Exception as e:
        print(f"Error scanning files dynamically: {e}")
        
    if not os.path.exists(DB_PATH):
        return []
        
    conn = connect_db(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT paper_json, paper_date 
        FROM papers 
        WHERE paper_date BETWEEN ? AND ? 
          AND language = ?
        """, (start_date, end_date, lang))
        rows = cursor.fetchall()
        
        papers = []
        for paper_json, paper_date in rows:
            try:
                p = json.loads(paper_json)
                p['date'] = paper_date
                papers.append(p)
            except Exception:
                continue
        return papers
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    finally:
        conn.close()

async def clean_expired_sessions_loop():
    while True:
        try:
            now = time.time()
            expired = [t for t, exp in active_sessions.items() if now > exp]
            for t in expired:
                active_sessions.pop(t, None)
        except Exception as e:
            print(f"Error cleaning sessions: {e}")
        await asyncio.sleep(3600)  # Clean every hour

@app.on_event("startup")
def startup_event():
    try:
        scan_and_process_files()
    except Exception as e:
        print(f"Error during startup scanning: {e}")
        
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = connect_db(DB_PATH)
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
    except Exception as e:
        print(f"Error creating hot_papers_cache table on startup: {e}")
        
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(clean_expired_sessions_loop())
        else:
            asyncio.create_task(clean_expired_sessions_loop())
    except Exception as e:
        print(f"Failed to start background session cleaner: {e}")

@app.get("/api/stats/keywords")
def get_keyword_stats(
    start_date: str, 
    end_date: str, 
    lang: str = "en", 
    category: str = "All", 
    token: str = Depends(verify_token)
):
    try:
        scan_and_process_files()
    except Exception as e:
        print(f"Error scanning files dynamically: {e}")
        
    if not os.path.exists(DB_PATH):
        return {"keywords": [], "daily_trends": []}
        
    conn = connect_db(DB_PATH)
    try:
        cursor = conn.cursor()
        
        if category == 'All':
            query = """
            SELECT keyword, category, paper_date, SUM(frequency) as count
            FROM keyword_stats
            WHERE paper_date BETWEEN ? AND ?
              AND language = ?
            GROUP BY keyword, category, paper_date
            """
            params = [start_date, end_date, lang]
        else:
            categories = category.split(',')
            placeholders = ','.join(['?'] * len(categories))
            query = f"""
            SELECT keyword, category, paper_date, SUM(frequency) as count
            FROM keyword_stats
            WHERE paper_date BETWEEN ? AND ?
              AND language = ?
              AND category IN ({placeholders})
            GROUP BY keyword, category, paper_date
            """
            params = [start_date, end_date, lang] + categories
            
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        keyword_data = {}
        for keyword, cat, p_date, count in rows:
            if keyword not in keyword_data:
                keyword_data[keyword] = {
                    "keyword": keyword,
                    "count": 0,
                    "category_distribution": {},
                    "date_distribution": {}
                }
            
            entry = keyword_data[keyword]
            entry["count"] += count
            entry["category_distribution"][cat] = entry["category_distribution"].get(cat, 0) + count
            entry["date_distribution"][p_date] = entry["date_distribution"].get(p_date, 0) + count
            
        # Calculate growth rate if the time range has a span
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            delta_days = (end_dt - start_dt).days
            if delta_days >= 1:
                midpoint_dt = start_dt + timedelta(days=delta_days // 2)
                midpoint_str = midpoint_dt.strftime("%Y-%m-%d")
                
                for kw, entry in keyword_data.items():
                    first_half_sum = 0
                    second_half_sum = 0
                    for p_date, count in entry["date_distribution"].items():
                        if p_date <= midpoint_str:
                            first_half_sum += count
                        else:
                            second_half_sum += count
                    
                    entry["growth_rate"] = (second_half_sum - first_half_sum) / max(first_half_sum, 1)
            else:
                for kw, entry in keyword_data.items():
                    entry["growth_rate"] = 0.0
        except Exception as e:
            print(f"Error calculating growth rates: {e}")
            for kw, entry in keyword_data.items():
                entry["growth_rate"] = 0.0

        # Convert to list and sort by count descending
        keywords_list = sorted(keyword_data.values(), key=lambda x: x["count"], reverse=True)
        # Limit to 100
        keywords_list = keywords_list[:100]
        
        daily_trends = []
        top_10_keywords = [item["keyword"] for item in keywords_list[:10]]
        for kw in top_10_keywords:
            if kw in keyword_data:
                for p_date, count in keyword_data[kw]["date_distribution"].items():
                    daily_trends.append({
                        "keyword": kw,
                        "date": p_date,
                        "count": count
                    })
        daily_trends.sort(key=lambda x: x["date"])
        
        return {
            "keywords": keywords_list,
            "daily_trends": daily_trends
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    finally:
        conn.close()

@app.get("/api/stats/network")
def get_network_stats(
    start_date: str, 
    end_date: str, 
    lang: str = "en", 
    category: str = "All", 
    token: str = Depends(verify_token)
):
    try:
        scan_and_process_files()
    except Exception as e:
        print(f"Error scanning files dynamically: {e}")
        
    if not os.path.exists(DB_PATH):
        return {"nodes": [], "links": []}
        
    conn = connect_db(DB_PATH)
    try:
        cursor = conn.cursor()
        
        if category == 'All':
            query = """
            SELECT keyword, SUM(frequency) as total
            FROM keyword_stats
            WHERE paper_date BETWEEN ? AND ?
              AND language = ?
            GROUP BY keyword
            ORDER BY total DESC
            LIMIT 35
            """
            params = [start_date, end_date, lang]
        else:
            categories = category.split(',')
            placeholders = ','.join(['?'] * len(categories))
            query = f"""
            SELECT keyword, SUM(frequency) as total
            FROM keyword_stats
            WHERE paper_date BETWEEN ? AND ?
              AND language = ?
              AND category IN ({placeholders})
            GROUP BY keyword
            ORDER BY total DESC
            LIMIT 35
            """
            params = [start_date, end_date, lang] + categories
            
        cursor.execute(query, params)
        nodes_rows = cursor.fetchall()
        nodes = [{"id": row[0], "value": row[1]} for row in nodes_rows]
        top_35_keywords = [row[0] for row in nodes_rows]
        
        links = []
        if top_35_keywords:
            kw_placeholders = ",".join(["?"] * len(top_35_keywords))
            
            if category == 'All':
                sql = f"""
                SELECT pk1.keyword AS source, pk2.keyword AS target, COUNT(*) AS value
                FROM paper_keywords pk1
                JOIN paper_keywords pk2 ON pk1.paper_id = pk2.paper_id AND pk1.keyword < pk2.keyword
                WHERE pk1.paper_date BETWEEN ? AND ?
                  AND pk1.language = ?
                  AND pk1.keyword IN ({kw_placeholders})
                  AND pk2.keyword IN ({kw_placeholders})
                GROUP BY pk1.keyword, pk2.keyword
                """
                links_params = [start_date, end_date, lang] + top_35_keywords + top_35_keywords
            else:
                sql = f"""
                SELECT pk1.keyword AS source, pk2.keyword AS target, COUNT(*) AS value
                FROM paper_keywords pk1
                JOIN paper_keywords pk2 ON pk1.paper_id = pk2.paper_id AND pk1.keyword < pk2.keyword
                WHERE pk1.paper_date BETWEEN ? AND ?
                  AND pk1.language = ?
                  AND pk1.category IN ({placeholders})
                  AND pk1.keyword IN ({kw_placeholders})
                  AND pk2.keyword IN ({kw_placeholders})
                GROUP BY pk1.keyword, pk2.keyword
                """
                links_params = [start_date, end_date, lang] + categories + top_35_keywords + top_35_keywords
                
            cursor.execute(sql, links_params)
            links_rows = cursor.fetchall()
            links = [{"source": row[0], "target": row[1], "value": row[2]} for row in links_rows]
            
        # Perform community detection on nodes and links
        try:
            community_detection(nodes, links)
        except Exception as e:
            print(f"Error doing community detection: {e}")
            
        return {
            "nodes": nodes,
            "links": links
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    finally:
        conn.close()


# Helper to fetch journals Safely
try:
    from daily_paper.daily_journals.constants import JOURNALS
except ModuleNotFoundError:
    import sys
    sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'daily_paper'))
    from daily_journals.constants import JOURNALS

def fetch_top_papers_from_openalex(issn_list, from_date):
    import requests
    issn_str = "|".join(issn_list)
    headers = {
        "User-Agent": "daily-arXiv-ai-enhanced/1.0 (mailto:dw-dengwei@users.noreply.github.com)"
    }
    api_key = os.environ.get("OPENALEX_API_KEY", "")
    
    url = "https://api.openalex.org/works"
    params = {
        "filter": f"primary_location.source.issn:{issn_str},from_publication_date:{from_date}",
        "sort": "cited_by_count:desc",
        "per_page": 10,
        "page": 1
    }
    if api_key:
        params["api_key"] = api_key
        
    resp = requests.get(url, params=params, headers=headers, timeout=20)
    if resp.status_code != 200:
        raise Exception(f"OpenAlex API error {resp.status_code}: {resp.text}")
        
    data = resp.json()
    results = data.get("results", [])
    
    formatted_papers = []
    for paper in results:
        title = paper.get("title") or "Untitled"
        
        authors_list = []
        for authorship in (paper.get("authorships") or []):
            if isinstance(authorship, dict):
                author_name = (authorship.get("author") or {}).get("display_name")
                if author_name:
                    authors_list.append(author_name)
        authors_str = ", ".join(authors_list[:5])
        if len(authors_list) > 5:
            authors_str += " et al."
            
        cited_by = paper.get("cited_by_count") or 0
        primary_loc = paper.get("primary_location") or {}
        paper_url = paper.get("doi") or (primary_loc.get("landing_page_url") if isinstance(primary_loc, dict) else "") or ""
        pub_date = paper.get("publication_date") or ""
        
        formatted_papers.append({
            "id": paper.get("id") or "",
            "title": title,
            "authors": authors_str,
            "cited_by_count": cited_by,
            "url": paper_url,
            "publication_date": pub_date
        })
        
    return formatted_papers

@app.get("/api/stats/journals")
def get_journals(token: str = Depends(verify_token)):
    return [{"name": j["name"], "category": j["category"]} for j in JOURNALS]

@app.get("/api/stats/hot-papers")
def get_hot_papers(journal: str, period: int, token: str = Depends(verify_token)):
    if period not in [30, 180, 365]:
        raise HTTPException(status_code=400, detail="Invalid period. Must be 30, 180, or 365.")
    
    selected_journal = None
    for j in JOURNALS:
        if j["name"] == journal or j["category"] == journal:
            selected_journal = j
            break
            
    if not selected_journal:
        raise HTTPException(status_code=404, detail=f"Journal '{journal}' not found in configuration.")
    
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = connect_db(DB_PATH)
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT papers_json FROM hot_papers_cache
        WHERE journal = ? AND period = ? AND query_date = ?
        """, (selected_journal["name"], period, today_str))
        
        row = cursor.fetchone()
        if row:
            return json.loads(row[0])
            
        # Cache miss, fetch from OpenAlex
        from_date = (datetime.now() - timedelta(days=period)).strftime("%Y-%m-%d")
        papers = fetch_top_papers_from_openalex(selected_journal["issns"], from_date)
        
        # Store in cache
        cursor.execute("""
        INSERT OR REPLACE INTO hot_papers_cache (journal, period, query_date, papers_json)
        VALUES (?, ?, ?, ?)
        """, (selected_journal["name"], period, today_str, json.dumps(papers)))
        conn.commit()
        
        return papers
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to fetch hot papers: {str(e)}")
    finally:
        conn.close()


# Serve HTML pages directly
@app.get("/")
@app.get("/index.html")
def read_index():
    return FileResponse("index.html")

@app.get("/login.html")
def read_login():
    return FileResponse("login.html")

@app.get("/settings.html")
def read_settings():
    return FileResponse("settings.html")

@app.get("/statistic.html")
def read_statistic():
    return FileResponse("statistic.html")

# Mount static folders if they exist
for folder in ["js", "css", "assets", "images"]:
    if os.path.exists(folder):
        app.mount(f"/{folder}", StaticFiles(directory=folder), name=folder)

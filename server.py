import os
import uuid
import time
import json
import re
import sqlite3
import threading
from fastapi import FastAPI, Depends, HTTPException, Header, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

# 清理环境变量中的空白符和换行符，防止因 \r 导致解析报错
for k in os.environ:
    os.environ[k] = os.environ[k].strip()

ACCESS_PASSWORD = os.getenv("ACCESS_PASSWORD", "")
active_sessions = {}  # token -> expiry_timestamp

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


# ==========================================
# Keyword Extraction & SQLite Storage
# ==========================================

STOPWORDS = {
    # 学术/特定高频词
    'method', 'based', 'towards', 'via', 'multi', 'text', 'using', 'aware', 'data', 'from', 'paper', 'propose', 
    'proposed', 'approach', 'model', 'system', 'framework', 'results', 'show', 'demonstrates', 'experimental', 
    'experiments', 'evaluation', 'performance', 'state', 'art', 'sota', 'dataset', 'datasets', 'task', 'tasks', 
    'learning', 'neural', 'network', 'networks', 'deep', 'machine', 'artificial', 'intelligence', 'ai', 'ml', 'dl',
    'efficient', 'novel', 'modality', 'generative', 'large', 'pretrained', 'unsupervised', 'supervised', 'semi', 
    'self', 'methods',
    # 通用英文停用词
    'a', 'an', 'the', 'and', 'or', 'but', 'if', 'then', 'else', 'when', 'at', 'by', 'from', 'for', 'with', 'in', 'on', 
    'to', 'of', 'up', 'down', 'out', 'over', 'under', 'again', 'further', 'then', 'once', 'here', 'there', 'why', 
    'how', 'all', 'any', 'both', 'each', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not', 'only', 
    'own', 'same', 'so', 'than', 'too', 'very', 's', 't', 'can', 'will', 'just', 'don', 'should', 'now', 'i', 'me', 
    'my', 'myself', 'we', 'our', 'ours', 'ourselves', 'you', 'your', 'yours', 'yourself', 'yourselves', 'he', 'him', 
    'his', 'himself', 'she', 'her', 'hers', 'herself', 'it', 'its', 'itself', 'they', 'them', 'their', 'theirs', 
    'themselves', 'what', 'which', 'who', 'whom', 'this', 'that', 'these', 'those', 'am', 'is', 'are', 'was', 'were', 
    'be', 'been', 'being', 'have', 'has', 'had', 'having', 'do', 'does', 'did', 'doing', 'would', 'should', 'could', 
    'ought', 'im', 'youre', 'hes', 'shes', 'its', 'were', 'theyre', 'ive', 'youve', 'weve', 'theyve', 'id', 'youd', 
    'hed', 'shed', 'wed', 'theyd', 'ill', 'youll', 'hell', 'shell', 'well', 'theyll', 'isnt', 'arent', 'wasnt', 
    'werent', 'hasnt', 'havent', 'hadnt', 'doesnt', 'dont', 'didnt', 'wont', 'wouldnt', 'shant', 'shouldnt', 'cant', 
    'cannot', 'couldnt', 'mustnt', 'lets', 'thats', 'whos', 'whats', 'heres', 'theres', 'whens', 'wheres', 'whys', 
    'hows', 'd', 'll', 'm', 'o', 're', 've', 'y', 'about', 'above', 'after', 'against', 'again', 'all', 'am', 'an', 
    'any', 'are', 'arent', 'as', 'at', 'be', 'because', 'been', 'before', 'being', 'below', 'between', 'both', 
    'but', 'by', 'cant', 'cannot', 'could', 'couldnt', 'did', 'didnt', 'do', 'does', 'doesnt', 'doing', 'dont', 
    'down', 'during', 'each', 'few', 'for', 'from', 'further', 'had', 'hadnt', 'has', 'hasnt', 'have', 'havent', 
    'having', 'he', 'hed', 'hell', 'hes', 'her', 'here', 'heres', 'hers', 'herself', 'him', 'himself', 'his', 
    'how', 'hows', 'i', 'id', 'ill', 'im', 'ive', 'if', 'in', 'into', 'is', 'isnt', 'it', 'its', 'itself', 'lets', 
    'me', 'more', 'most', 'mustnt', 'my', 'myself', 'no', 'nor', 'not', 'of', 'off', 'on', 'once', 'only', 'or', 
    'other', 'ought', 'our', 'ours', 'ourselves', 'out', 'over', 'own', 'same', 'shant', 'she', 'shed', 'shell', 
    'shes', 'should', 'shouldnt', 'so', 'some', 'such', 'than', 'that', 'thats', 'the', 'their', 'theirs', 
    'them', 'themselves', 'then', 'there', 'theres', 'these', 'they', 'theyd', 'theyll', 'theyre', 'theyve', 
    'this', 'those', 'through', 'to', 'too', 'under', 'until', 'up', 'very', 'was', 'wasnt', 'we', 'wed', 
    'well', 'were', 'weve', 'werent', 'what', 'whats', 'when', 'whens', 'where', 'wheres', 'which', 'while', 
    'who', 'whos', 'whom', 'why', 'whys', 'with', 'wont', 'would', 'wouldnt', 'you', 'youd', 'youll', 'youre', 
    'youve', 'your', 'yours', 'yourself', 'yourselves'
}

def extract_keywords(title: str, summary: str = "") -> list:
    candidates = {}
    for text in [title, summary]:
        if not text:
            continue
        text = text.lower()
        cleaned = re.sub(r"[^\w\s-]", " ", text)
        raw_words = cleaned.split()
        
        words = []
        for w in raw_words:
            w_clean = w.strip("-_")
            if w_clean and not w_clean.isdigit() and len(w_clean) > 1:
                words.append(w_clean)
                
        segments = []
        current_segment = []
        for w in words:
            if w in STOPWORDS:
                if current_segment:
                    segments.append(current_segment)
                    current_segment = []
            else:
                current_segment.append(w)
        if current_segment:
            segments.append(current_segment)
            
        for seg in segments:
            n = len(seg)
            for i in range(n):
                for l in range(1, min(4, n - i + 1)):
                    phrase = " ".join(seg[i:i+l])
                    candidates[phrase] = candidates.get(phrase, 0) + 1
                    
    sorted_candidates = sorted(
        candidates.items(),
        key=lambda x: (x[1], len(x[0])),
        reverse=True
    )
    return sorted_candidates[:10]

db_lock = threading.Lock()

def scan_and_process_files():
    db_dir = "data"
    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, "statistics.db")
    
    with db_lock:
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.cursor()
            
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_files (
                filename TEXT PRIMARY KEY,
                processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS keyword_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_date TEXT,
                language TEXT,
                category TEXT,
                keyword TEXT,
                frequency INTEGER
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS paper_keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id TEXT,
                paper_date TEXT,
                language TEXT,
                category TEXT,
                keyword TEXT
            )
            """)
            
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_ks_date_lang_cat ON keyword_stats (paper_date, language, category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_ks_keyword ON keyword_stats (keyword)")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ks_unique ON keyword_stats (paper_date, language, category, keyword)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pk_paper_id ON paper_keywords (paper_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pk_date_lang_cat ON paper_keywords (paper_date, language, category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pk_keyword ON paper_keywords (keyword)")
            conn.commit()
            
            files = os.listdir(db_dir)
            target_files = []
            for f in files:
                if f.endswith(".jsonl") and "_AI_enhanced_" in f:
                    parts = f.replace(".jsonl", "").split("_AI_enhanced_")
                    if len(parts) == 2:
                        target_files.append((f, parts[0], parts[1]))
                        
            for filename, paper_date, lang in target_files:
                cursor.execute("SELECT 1 FROM processed_files WHERE filename = ?", (filename,))
                if cursor.fetchone():
                    continue
                    
                filepath = os.path.join(db_dir, filename)
                if not os.path.exists(filepath):
                    continue
                    
                stats_map = {}
                paper_keywords_list = []
                
                with open(filepath, "r", encoding="utf-8") as f_in:
                    for line in f_in:
                        line_str = line.strip()
                        if not line_str:
                            continue
                        try:
                            paper = json.loads(line_str)
                        except Exception:
                            continue
                            
                        paper_id = paper.get("id")
                        if not paper_id:
                            continue
                            
                        cats = paper.get("categories", [])
                        category = "unknown"
                        if isinstance(cats, list) and len(cats) > 0:
                            category = cats[0]
                        elif isinstance(cats, str):
                            cats_split = re.split(r"[,\s]+", cats.strip())
                            if cats_split and cats_split[0]:
                                category = cats_split[0]
                                
                        title = paper.get("title", "")
                        summary = paper.get("summary", "")
                        
                        keywords_with_freq = extract_keywords(title, summary)
                        
                        for kw, freq in keywords_with_freq:
                            paper_keywords_list.append((paper_id, paper_date, lang, category, kw))
                            key = (paper_date, lang, category, kw)
                            stats_map[key] = stats_map.get(key, 0) + freq
                            
                if paper_keywords_list:
                    cursor.executemany(
                        "INSERT INTO paper_keywords (paper_id, paper_date, language, category, keyword) VALUES (?, ?, ?, ?, ?)",
                        paper_keywords_list
                    )
                    
                stats_insert_data = []
                for (p_date, p_lang, p_cat, p_kw), freq in stats_map.items():
                    stats_insert_data.append((p_date, p_lang, p_cat, p_kw, freq))
                    
                if stats_insert_data:
                    cursor.executemany("""
                    INSERT INTO keyword_stats (paper_date, language, category, keyword, frequency)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(paper_date, language, category, keyword)
                    DO UPDATE SET frequency = frequency + excluded.frequency
                    """, stats_insert_data)
                    
                cursor.execute("INSERT OR REPLACE INTO processed_files (filename) VALUES (?)", (filename,))
                conn.commit()
        finally:
            conn.close()

@app.on_event("startup")
def startup_event():
    try:
        scan_and_process_files()
    except Exception as e:
        print(f"Error during startup scanning: {e}")

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
        
    db_path = "data/statistics.db"
    if not os.path.exists(db_path):
        return {"keywords": [], "daily_trends": []}
        
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        
        cursor.execute("""
        SELECT keyword, category, paper_date, SUM(frequency) as count
        FROM keyword_stats
        WHERE paper_date BETWEEN ? AND ?
          AND language = ?
          AND (? = 'All' OR category = ?)
        GROUP BY keyword, category, paper_date
        """, (start_date, end_date, lang, category, category))
        
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
            
        # 转换为列表并按 count 降序排序
        keywords_list = sorted(keyword_data.values(), key=lambda x: x["count"], reverse=True)
        # 限制返回 100 个
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
        # 按照 date 排序，保证前端折线图渲染时日期有序
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
        
    db_path = "data/statistics.db"
    if not os.path.exists(db_path):
        return {"nodes": [], "links": []}
        
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        
        cursor.execute("""
        SELECT keyword, SUM(frequency) as total
        FROM keyword_stats
        WHERE paper_date BETWEEN ? AND ?
          AND language = ?
          AND (? = 'All' OR category = ?)
        GROUP BY keyword
        ORDER BY total DESC
        LIMIT 35
        """, (start_date, end_date, lang, category, category))
        
        nodes_rows = cursor.fetchall()
        nodes = [{"id": row[0], "value": row[1]} for row in nodes_rows]
        top_35_keywords = [row[0] for row in nodes_rows]
        
        links = []
        if top_35_keywords:
            placeholders = ",".join(["?"] * len(top_35_keywords))
            sql = f"""
            SELECT pk1.keyword AS source, pk2.keyword AS target, COUNT(*) AS value
            FROM paper_keywords pk1
            JOIN paper_keywords pk2 ON pk1.paper_id = pk2.paper_id AND pk1.keyword < pk2.keyword
            WHERE pk1.paper_date BETWEEN ? AND ?
              AND pk1.language = ?
              AND (? = 'All' OR pk1.category = ?)
              AND pk1.keyword IN ({placeholders})
              AND pk2.keyword IN ({placeholders})
            GROUP BY pk1.keyword, pk2.keyword
            """
            params = [start_date, end_date, lang, category, category] + top_35_keywords + top_35_keywords
            cursor.execute(sql, params)
            
            links_rows = cursor.fetchall()
            links = [{"source": row[0], "target": row[1], "value": row[2]} for row in links_rows]
            
        return {
            "nodes": nodes,
            "links": links
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
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

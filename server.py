import os
import uuid
import time
import json
import re
import sqlite3
import threading
import asyncio
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
        
    db_path = "data/statistics.db"
    if not os.path.exists(db_path):
        return []
        
    conn = connect_db(db_path)
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


# ==========================================
# Keyword Extraction & SQLite Storage
# ==========================================

STOPWORDS = {
    # 学术通用无实质意义的词/短语元素
    'method', 'methods', 'based', 'towards', 'via', 'using', 'paper', 'propose', 'proposes',
    'proposed', 'approach', 'approaches', 'system', 'systems', 'framework', 'frameworks',
    'results', 'result', 'show', 'shows', 'demonstrated', 'demonstrates', 'demonstrate',
    'experimental', 'experiments', 'experiment', 'evaluation', 'evaluations', 'performance',
    'performances', 'state', 'art', 'sota', 'dataset', 'datasets', 'task', 'tasks',
    'efficient', 'novel', 'modality', 'modalities', 'large', 'unsupervised', 'supervised',
    'semi', 'self', 'new', 'study', 'studies', 'analysis', 'analyses', 'application',
    'applications', 'development', 'developments', 'design', 'designs', 'process', 'processes',
    # 通用英文停用词 (已去重)
    'a', 'about', 'above', 'after', 'against', 'all', 'am', 'an', 'and', 'any', 'are', 'aren',
    'arent', 'as', 'at', 'be', 'because', 'been', 'before', 'being', 'below', 'between', 'both',
    'but', 'by', 'can', 'cannot', 'cant', 'could', 'couldn', 'couldnt', 'd', 'did', 'didn',
    'didnt', 'do', 'does', 'doesn', 'doesnt', 'doing', 'don', 'dont', 'down', 'during', 'each',
    'else', 'few', 'for', 'from', 'further', 'had', 'hadn', 'hadnt', 'has', 'hasn', 'hasnt',
    'have', 'haven', 'havent', 'having', 'he', 'hed', 'hell', 'hes', 'her', 'here', 'heres',
    'hers', 'herself', 'him', 'himself', 'his', 'how', 'hows', 'i', 'id', 'if', 'ill', 'im',
    'in', 'into', 'is', 'isn', 'isnt', 'it', 'its', 'itself', 'just', 'lets', 'll', 'm', 'me',
    'more', 'most', 'mustn', 'mustnt', 'my', 'myself', 'no', 'nor', 'not', 'now', 'o', 'of',
    'off', 'on', 'once', 'only', 'or', 'other', 'ought', 'our', 'ours', 'ourselves', 'out',
    'over', 'own', 're', 'same', 'shan', 'shant', 'she', 'shed', 'shell', 'shes', 'should',
    'shouldn', 'shouldnt', 'so', 'some', 'such', 't', 'than', 'that', 'thats', 'the', 'their',
    'theirs', 'them', 'themselves', 'then', 'there', 'theres', 'these', 'they', 'theyd',
    'theyll', 'theyre', 'theyve', 'this', 'those', 'through', 'to', 'too', 'under', 'until',
    'up', 've', 'very', 'was', 'wasn', 'wasnt', 'we', 'wed', 'well', 'were', 'weren', 'werent',
    'weve', 'what', 'whats', 'when', 'whens', 'where', 'wheres', 'which', 'while', 'who',
    'whos', 'whom', 'why', 'whys', 'will', 'with', 'won', 'wont', 'would', 'wouldn', 'wouldnt',
    'y', 'you', 'youd', 'youll', 'youre', 'youve', 'your', 'yours', 'yourself', 'yourselves'
}

def connect_db(db_path):
    conn = sqlite3.connect(db_path, timeout=10.0, check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception as e:
        print(f"Error setting WAL mode: {e}")
    return conn

processed_files_cache = set()
cache_initialized = False

def extract_keywords(title: str, summary: str = "") -> list:
    title = title or ""
    summary = summary or ""
    
    def stem_phrase(phrase: str) -> str:
        words = phrase.split()
        stemmed = []
        for w in words:
            w_stem = w.lower()
            if len(w_stem) > 4:
                if w_stem.endswith("sses"):
                    w_stem = w_stem[:-2]
                elif w_stem.endswith("ies"):
                    w_stem = w_stem[:-3] + "y"
                elif w_stem.endswith("s") and not w_stem.endswith("ss"):
                    w_stem = w_stem[:-1]
                
                if w_stem.endswith("ing"):
                    w_stem = w_stem[:-3]
                elif w_stem.endswith("ed"):
                    w_stem = w_stem[:-2]
            stemmed.append(w_stem)
        return " ".join(stemmed)

    raw_candidates = {}  # stemmed_phrase -> {raw_phrase: count}
    stemmed_freq = {}    # stemmed_phrase -> total_weighted_count
    
    # Process title (weight = 3) and summary (weight = 1)
    for text, weight in [(title, 3), (summary, 1)]:
        if not text:
            continue
        text_lower = text.lower()
        cleaned = re.sub(r"[^\w\s-]", " ", text_lower)
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
                    stemmed = stem_phrase(phrase)
                    
                    stemmed_freq[stemmed] = stemmed_freq.get(stemmed, 0) + weight
                    if stemmed not in raw_candidates:
                        raw_candidates[stemmed] = {}
                    raw_candidates[stemmed][phrase] = raw_candidates[stemmed].get(phrase, 0) + 1

    sorted_stems = sorted(stemmed_freq.keys(), key=len, reverse=True)
    pruned_stems = set()
    
    for i, long_stem in enumerate(sorted_stems):
        if long_stem in pruned_stems:
            continue
        for short_stem in sorted_stems[i+1:]:
            if short_stem in pruned_stems:
                continue
            long_words = long_stem.split()
            short_words = short_stem.split()
            is_sub = False
            for idx in range(len(long_words) - len(short_words) + 1):
                if long_words[idx:idx+len(short_words)] == short_words:
                    is_sub = True
                    break
            
            if is_sub:
                if len(short_words) >= 2:
                    if stemmed_freq[short_stem] <= stemmed_freq[long_stem] * 1.2:
                        pruned_stems.add(short_stem)
                    else:
                        stemmed_freq[short_stem] -= stemmed_freq[long_stem]
                else:
                    # For single words, reduce frequency by half of long_stem frequency
                    # to keep them if they appear in other contexts, ensuring test compat
                    stemmed_freq[short_stem] = max(0, stemmed_freq[short_stem] - stemmed_freq[long_stem] // 2)

    result = []
    for stemmed, freq in stemmed_freq.items():
        if stemmed in pruned_stems or freq <= 0:
            continue
        raw_phrases = raw_candidates[stemmed]
        best_raw = max(raw_phrases.items(), key=lambda x: x[1])[0]
        result.append((best_raw, freq))
        
    result.sort(key=lambda x: x[1], reverse=True)
    return result[:10]

db_lock = threading.Lock()

def scan_and_process_files():
    global cache_initialized
    db_dir = "data"
    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, "statistics.db")
    
    if not os.path.exists(db_path):
        processed_files_cache.clear()
        cache_initialized = False
    
    # 1. Quick check outside the lock if cache is initialized
    files = os.listdir(db_dir)
    target_files = []
    for f in files:
        if f.endswith(".jsonl") and "_AI_enhanced_" in f:
            parts = f.replace(".jsonl", "").split("_AI_enhanced_")
            if len(parts) == 2:
                target_files.append((f, parts[0], parts[1]))
                
    if cache_initialized:
        new_files = [tf for tf in target_files if tf[0] not in processed_files_cache]
        if not new_files:
            return  # No new files to process! Skip entire database lock & queries.
            
    with db_lock:
        conn = connect_db(db_path)
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
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS papers (
                paper_id TEXT,
                paper_date TEXT,
                language TEXT,
                paper_json TEXT,
                PRIMARY KEY (paper_id, paper_date, language)
            )
            """)
            
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_ks_date_lang_cat ON keyword_stats (paper_date, language, category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_ks_keyword ON keyword_stats (keyword)")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ks_unique ON keyword_stats (paper_date, language, category, keyword)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pk_paper_id ON paper_keywords (paper_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pk_date_lang_cat ON paper_keywords (paper_date, language, category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pk_keyword ON paper_keywords (keyword)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_date_lang ON papers (paper_date, language)")
            conn.commit()
            
            # Load processed files into cache if not initialized
            if not cache_initialized:
                cursor.execute("SELECT filename FROM processed_files")
                rows = cursor.fetchall()
                for row in rows:
                    processed_files_cache.add(row[0])
                cache_initialized = True
                
            # Filter files to process again inside lock
            files_to_process = [tf for tf in target_files if tf[0] not in processed_files_cache]
            
            for filename, paper_date, lang in files_to_process:
                cursor.execute("SELECT 1 FROM processed_files WHERE filename = ?", (filename,))
                already_processed = cursor.fetchone() is not None
                
                cursor.execute("SELECT 1 FROM papers WHERE paper_date = ? AND language = ? LIMIT 1", (paper_date, lang))
                already_in_papers = cursor.fetchone() is not None
                
                if already_processed and already_in_papers:
                    processed_files_cache.add(filename)
                    continue
                    
                filepath = os.path.join(db_dir, filename)
                if not os.path.exists(filepath):
                    continue
                    
                stats_map = {}
                paper_keywords_list = []
                papers_list = []
                
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
                        
                        if not already_in_papers:
                            papers_list.append((paper_id, paper_date, lang, json.dumps(paper)))
                            
                        if not already_processed:
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
                            
                            # Extract keywords
                            keywords_with_freq = extract_keywords(title, summary)
                            
                            # Merge OpenAlex concepts if available
                            concepts = paper.get("concepts", [])
                            if isinstance(concepts, list):
                                for concept in concepts:
                                    if concept and isinstance(concept, str):
                                        # Normalize concept to lowercase and add it as a key term
                                        keywords_with_freq.append((concept.lower(), 2))
                            
                            # Group same keywords in the same paper to avoid duplicate key violations
                            unique_kws = {}
                            for kw, freq in keywords_with_freq:
                                unique_kws[kw] = unique_kws.get(kw, 0) + freq
                                
                            for kw, freq in unique_kws.items():
                                paper_keywords_list.append((paper_id, paper_date, lang, category, kw))
                                key = (paper_date, lang, category, kw)
                                stats_map[key] = stats_map.get(key, 0) + freq
                                
                if papers_list:
                    cursor.executemany(
                        "INSERT OR REPLACE INTO papers (paper_id, paper_date, language, paper_json) VALUES (?, ?, ?, ?)",
                        papers_list
                    )
                    
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
                    
                if not already_processed:
                    cursor.execute("INSERT OR REPLACE INTO processed_files (filename) VALUES (?)", (filename,))
                
                processed_files_cache.add(filename)
                conn.commit()
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
        
    db_path = "data/statistics.db"
    if not os.path.exists(db_path):
        return {"keywords": [], "daily_trends": []}
        
    conn = connect_db(db_path)
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
        from datetime import datetime, timedelta
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

def community_detection(nodes, links):
    groups = {n["id"]: i for i, n in enumerate(nodes)}
    adj = {n["id"]: {} for n in nodes}
    for l in links:
        s, t, w = l["source"], l["target"], l["value"]
        if s in adj and t in adj:
            adj[s][t] = w
            adj[t][s] = w
            
    for _ in range(10):
        import random
        shuffled_nodes = [n["id"] for n in nodes]
        random.seed(42)
        random.shuffle(shuffled_nodes)
        
        for node in shuffled_nodes:
            if not adj[node]:
                continue
            label_weights = {}
            for neighbor, weight in adj[node].items():
                label = groups[neighbor]
                label_weights[label] = label_weights.get(label, 0) + weight
                
            if label_weights:
                best_label = max(label_weights.items(), key=lambda x: x[1])[0]
                groups[node] = best_label
                
    unique_groups = sorted(list(set(groups.values())))
    group_mapping = {g: i for i, g in enumerate(unique_groups)}
    
    for n in nodes:
        n["group"] = group_mapping[groups[n["id"]]]

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
        
    conn = connect_db(db_path)
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

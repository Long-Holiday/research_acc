import os
import json
import re
import math
import threading
from server_modules.database import connect_db
import server_modules.keywords as keywords

db_lock = threading.Lock()
processed_files_cache = set()
cache_initialized = False

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
                
            # Rebuild/refresh global IDF cache from existing papers
            try:
                cursor.execute("SELECT paper_json FROM papers")
                p_rows = cursor.fetchall()
                keywords.idf_doc_count = len(p_rows)
                df = {}
                for p_row in p_rows:
                    try:
                        p_data = json.loads(p_row[0])
                        p_text = (p_data.get("title", "") + " " + p_data.get("summary", "")).lower()
                        # Simple alphanumeric words of length > 2
                        p_words = set(re.findall(r"\b[a-z]{3,}\b", p_text))
                        for w in p_words:
                            df[w] = df.get(w, 0) + 1
                    except Exception:
                        continue
                keywords.idf_cache = {w: math.log((1 + keywords.idf_doc_count) / (1 + count)) + 1 for w, count in df.items()}
            except Exception as idf_err:
                print(f"Error initializing IDF cache: {idf_err}")
                keywords.idf_cache = {}
                keywords.idf_doc_count = 0
                
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
                            keywords_with_freq = keywords.extract_keywords(title, summary)
                            
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

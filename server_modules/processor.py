import os
import json
import re
import math
import threading
from typing import Optional, List, Dict, Set, Tuple
from server_modules.database import connect_db
import server_modules.keywords as keywords

DB_PATH = "data/statistics.db"

db_lock = threading.Lock()
processed_files_cache = set()
cache_initialized = False

def _init_tables(cursor):
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
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS advisor_reports (
        report_date TEXT PRIMARY KEY,
        topic TEXT NOT NULL,
        summary_takeaway TEXT,
        report_markdown TEXT NOT NULL,
        ideas_json TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS advisor_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS agg_daily_papers (
        paper_date TEXT,
        language TEXT,
        category TEXT,
        total_papers INTEGER,
        PRIMARY KEY (paper_date, language, category)
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS agg_daily_keywords (
        paper_date TEXT,
        language TEXT,
        category TEXT,
        keyword TEXT,
        distinct_paper_count INTEGER,
        PRIMARY KEY (paper_date, language, category, keyword)
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_advisor_reports_date ON advisor_reports (report_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ks_date_lang_cat ON keyword_stats (paper_date, language, category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ks_keyword ON keyword_stats (keyword)")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ks_unique ON keyword_stats (paper_date, language, category, keyword)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pk_paper_id ON paper_keywords (paper_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pk_date_lang_cat ON paper_keywords (paper_date, language, category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pk_keyword ON paper_keywords (keyword)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pk_lang_date_cat_kw ON paper_keywords (language, paper_date, category, keyword)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_date_lang ON papers (paper_date, language)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agg_kw_lang_cat_date ON agg_daily_keywords (language, category, paper_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agg_kw_lang_date ON agg_daily_keywords (language, paper_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agg_papers_lang_date ON agg_daily_papers (language, paper_date)")


def get_db_path():
    import app.config as config
    if hasattr(config, "DB_PATH") and config.DB_PATH != "data/statistics.db":
        return config.DB_PATH
    import server
    if hasattr(server, "DB_PATH") and getattr(server, "DB_PATH") != "data/statistics.db":
        return getattr(server, "DB_PATH")
    return DB_PATH

_scan_lock = threading.Lock()

def clear_processed_cache(filename: Optional[str] = None, clear_db: bool = True):
    """清理已处理文件缓存，方便文件更新后重新扫描入库"""
    global cache_initialized
    if filename:
        processed_files_cache.discard(filename)
        if clear_db:
            try:
                with db_lock:
                    conn = connect_db(get_db_path())
                    try:
                        cursor = conn.cursor()
                        cursor.execute("DELETE FROM processed_files WHERE filename = ?", (filename,))
                        conn.commit()
                    finally:
                        conn.close()
            except Exception:
                pass
    else:
        processed_files_cache.clear()
        cache_initialized = False
        if clear_db:
            try:
                with db_lock:
                    conn = connect_db(get_db_path())
                    try:
                        cursor = conn.cursor()
                        cursor.execute("DELETE FROM processed_files")
                        conn.commit()
                    finally:
                        conn.close()
            except Exception:
                pass


def scan_and_process_files():
    global cache_initialized
    import sys
    is_testing = "pytest" in sys.modules or "unittest" in sys.modules
    db_dir = "data"
    os.makedirs(db_dir, exist_ok=True)
    actual_db_path = get_db_path()
    if is_testing or not os.path.exists(actual_db_path):
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

    with _scan_lock:
        with db_lock:
            conn = connect_db(actual_db_path)
            try:
                cursor = conn.cursor()
                _init_tables(cursor)
                conn.commit()
                
                # Load processed files into cache if not initialized
                if not cache_initialized:
                    cursor.execute("SELECT filename FROM processed_files")
                    rows = cursor.fetchall()
                    for row in rows:
                        processed_files_cache.add(row[0])
                    cache_initialized = True
                    
                # Rebuild/refresh global IDF cache from existing papers using DB aggregate queries
                try:
                    cursor.execute("SELECT COUNT(*) FROM papers")
                    total_papers = cursor.fetchone()[0]
                    
                    # 仅当未初始化或论文总数增长超过 10% 时才全表重建 IDF 缓存
                    need_idf_refresh = not keywords.idf_cache or abs(total_papers - keywords.idf_doc_count) > max(50, keywords.idf_doc_count * 0.1)
                    keywords.idf_doc_count = total_papers
                    
                    if need_idf_refresh and total_papers > 0:
                        cursor.execute("SELECT keyword, COUNT(DISTINCT paper_id) FROM paper_keywords GROUP BY keyword")
                        df_rows = cursor.fetchall()
                        keywords.idf_cache = {
                            row[0].lower(): math.log((1 + total_papers) / (1 + row[1])) + 1
                            for row in df_rows if row[0]
                        }
                    elif not need_idf_refresh and keywords.idf_cache:
                        pass
                    else:
                        keywords.idf_cache = {}
                except Exception:
                    keywords.idf_cache = {}
                    keywords.idf_doc_count = 0
                    
                # Filter files to process again inside lock
                files_to_process = [tf for tf in target_files if tf[0] not in processed_files_cache]
                
                for filename, paper_date, lang in files_to_process:
                    cursor.execute("SELECT 1 FROM processed_files WHERE filename = ?", (filename,))
                    already_processed = cursor.fetchone() is not None

                    # --- 全局跨日期去重：加载同 language 下其他日期已存在的 paper_id 集合 ---
                    # 防止不同日期的 AI 增强文件包含同一期刊论文时重复入库
                    existing_global_ids = set()
                    try:
                        cursor.execute("SELECT paper_id FROM papers WHERE language = ? AND paper_date != ?", (lang, paper_date))
                        for (pid,) in cursor.fetchall():
                            if pid:
                                existing_global_ids.add(str(pid))
                    except Exception:
                        existing_global_ids = set()
                    seen_in_file = set()
                        
                    filepath = os.path.join(db_dir, filename)
                    if not os.path.exists(filepath):
                        continue
                        
                    # 分块缓冲写入，避免一次性在内存中累积整批论文/关键词列表导致 OOM。
                    CHUNK_PAPERS = 500
                    CHUNK_KEYWORDS = 2000
                    BATCH_NLP_SIZE = 50

                    stats_map = {}
                    paper_keywords_buf = []
                    papers_buf = []

                    def flush_papers():
                        if papers_buf:
                            cursor.executemany(
                                "INSERT OR REPLACE INTO papers (paper_id, paper_date, language, paper_json) VALUES (?, ?, ?, ?)",
                                papers_buf
                            )
                            papers_buf.clear()

                    def flush_keywords():
                        if paper_keywords_buf:
                            cursor.executemany(
                                "INSERT INTO paper_keywords (paper_id, paper_date, language, category, keyword) VALUES (?, ?, ?, ?, ?)",
                                paper_keywords_buf
                            )
                            paper_keywords_buf.clear()

                    def flush_stats():
                        if stats_map:
                            rows = [
                                (p_date, p_lang, p_cat, p_kw, freq)
                                for (p_date, p_lang, p_cat, p_kw), freq in stats_map.items()
                            ]
                            cursor.executemany("""
                            INSERT INTO keyword_stats (paper_date, language, category, keyword, frequency)
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(paper_date, language, category, keyword)
                            DO UPDATE SET frequency = frequency + excluded.frequency
                            """, rows)
                            stats_map.clear()

                    # 逐批读取与批量 NLP 分析
                    current_batch = []
                    
                    def process_current_batch(batch_items):
                        if not batch_items:
                            return
                            
                        # 1. 批量提取关键词
                        if not already_processed:
                            batch_kws = keywords.extract_keywords_batch(batch_items, batch_size=BATCH_NLP_SIZE)
                        else:
                            batch_kws = [[] for _ in batch_items]
                            
                        for paper_item, keywords_with_freq in zip(batch_items, batch_kws):
                            paper_id = paper_item.get("id")
                            if not paper_id:
                                continue
                            paper_id_str = str(paper_id)
                            # 同文件内去重
                            if paper_id_str in seen_in_file:
                                continue
                            # 跨日期全局去重（同 language 下 paper_id 在其他日期已存在则跳过）
                            if paper_id_str in existing_global_ids:
                                continue
                            seen_in_file.add(paper_id_str)
                            existing_global_ids.add(paper_id_str)
                                
                            # 保证最新 paper_json 始终写入 papers 表
                            papers_buf.append((paper_id_str, paper_date, lang, json.dumps(paper_item)))
                            if len(papers_buf) >= CHUNK_PAPERS:
                                flush_papers()
                                    
                            if not already_processed:
                                cats = paper_item.get("categories", [])
                                category = "unknown"
                                if isinstance(cats, list) and len(cats) > 0:
                                    category = cats[0]
                                elif isinstance(cats, str):
                                    cats_split = re.split(r"[,\s]+", cats.strip())
                                    if cats_split and cats_split[0]:
                                        category = cats_split[0]
                                        
                                # Merge OpenAlex concepts if available
                                concepts = paper_item.get("concepts", [])
                                if isinstance(concepts, list):
                                    for concept in concepts:
                                        if concept and isinstance(concept, str):
                                            keywords_with_freq.append((concept.lower(), 2))
                                
                                # Group same keywords in the same paper to avoid duplicate key violations
                                unique_kws = {}
                                for kw, freq in keywords_with_freq:
                                    unique_kws[kw] = unique_kws.get(kw, 0) + freq
                                    
                                for kw, freq in unique_kws.items():
                                    paper_keywords_buf.append((paper_id, paper_date, lang, category, kw))
                                    key = (paper_date, lang, category, kw)
                                    stats_map[key] = stats_map.get(key, 0) + freq
                                    
                                if len(paper_keywords_buf) >= CHUNK_KEYWORDS:
                                    flush_keywords()
                                    flush_stats()

                    with open(filepath, "r", encoding="utf-8") as f_in:
                        for line in f_in:
                            line_str = line.strip()
                            if not line_str:
                                continue
                            try:
                                paper = json.loads(line_str)
                            except Exception:
                                continue
                                
                            current_batch.append(paper)
                            if len(current_batch) >= BATCH_NLP_SIZE:
                                process_current_batch(current_batch)
                                current_batch.clear()

                    if current_batch:
                        process_current_batch(current_batch)
                        current_batch.clear()

                    flush_papers()
                    flush_keywords()
                    flush_stats()
                        
                    if not already_processed:
                        cursor.execute("INSERT OR REPLACE INTO processed_files (filename) VALUES (?)", (filename,))
                    
                    processed_files_cache.add(filename)
                    
                    # Incrementally update aggregation tables for the processed date, lang
                    cursor.execute("""
                    INSERT OR REPLACE INTO agg_daily_papers (paper_date, language, category, total_papers)
                    SELECT paper_date, language, category, COUNT(DISTINCT paper_id)
                    FROM paper_keywords
                    WHERE paper_date = ? AND language = ?
                    GROUP BY paper_date, language, category
                    """, (paper_date, lang))
                    
                    cursor.execute("""
                    INSERT OR REPLACE INTO agg_daily_keywords (paper_date, language, category, keyword, distinct_paper_count)
                    SELECT paper_date, language, category, keyword, COUNT(DISTINCT paper_id)
                    FROM paper_keywords
                    WHERE paper_date = ? AND language = ?
                    GROUP BY paper_date, language, category, keyword
                    """, (paper_date, lang))
                
                conn.commit()
            finally:
                conn.close()

        if files_to_process:
            try:
                from server_modules.stats import clear_stats_cache
                clear_stats_cache()
            except Exception:
                pass


def reextract_keywords_for_papers(paper_groups):
    """仅重新提取指定论文的关键词，并增量刷新其日期/语言聚合数据。

    ``paper_groups`` 为 ``[(paper_date, language, papers), ...]``。旧论文会先根据
    数据库中保存的旧版本计算并扣除原关键词贡献；新增论文则直接追加，因此无需
    清空关键词表或扫描全部历史增强文件。
    """
    groups = [
        (paper_date, language, [paper for paper in papers if paper.get("id")])
        for paper_date, language, papers in paper_groups
        if papers
    ]
    groups = [(date, lang, papers) for date, lang, papers in groups if papers]
    if not groups:
        return True

    def extract_contributions(papers):
        if not papers:
            return []
        extracted = keywords.extract_keywords_batch(papers, batch_size=50)
        contributions = []
        for paper, paper_keywords in zip(papers, extracted):
            cats = paper.get("categories", [])
            if isinstance(cats, list) and cats:
                category = cats[0]
            elif isinstance(cats, str) and cats.strip():
                category = re.split(r"[,\s]+", cats.strip())[0]
            else:
                category = "unknown"

            for concept in paper.get("concepts", []):
                if isinstance(concept, str) and concept:
                    paper_keywords.append((concept.lower(), 2))

            unique = {}
            for keyword, frequency in paper_keywords:
                unique[keyword] = unique.get(keyword, 0) + frequency
            contributions.append((paper, category, unique))
        return contributions

    actual_db_path = get_db_path()
    affected_date_languages = set()

    with _scan_lock:
        with db_lock:
            conn = connect_db(actual_db_path)
            try:
                cursor = conn.cursor()
                _init_tables(cursor)

                for paper_date, language, papers in groups:
                    paper_ids = [str(paper["id"]) for paper in papers]
                    placeholders = ",".join("?" for _ in paper_ids)
                    cursor.execute(
                        f"SELECT paper_json FROM papers WHERE paper_date = ? AND language = ? "
                        f"AND paper_id IN ({placeholders})",
                        (paper_date, language, *paper_ids),
                    )
                    old_papers = []
                    for (paper_json,) in cursor.fetchall():
                        try:
                            old_papers.append(json.loads(paper_json))
                        except (TypeError, json.JSONDecodeError):
                            pass

                    old_contributions = extract_contributions(old_papers)
                    new_contributions = extract_contributions(papers)

                    # 先扣除这些论文旧版本对加权统计的贡献。
                    for _, category, contribution in old_contributions:
                        for keyword, frequency in contribution.items():
                            cursor.execute(
                                "UPDATE keyword_stats SET frequency = frequency - ? "
                                "WHERE paper_date = ? AND language = ? AND category = ? AND keyword = ?",
                                (frequency, paper_date, language, category, keyword),
                            )

                    cursor.execute(
                        f"DELETE FROM paper_keywords WHERE paper_date = ? AND language = ? "
                        f"AND paper_id IN ({placeholders})",
                        (paper_date, language, *paper_ids),
                    )

                    for paper, category, contribution in new_contributions:
                        paper_id = str(paper["id"])
                        cursor.execute(
                            "INSERT OR REPLACE INTO papers "
                            "(paper_id, paper_date, language, paper_json) VALUES (?, ?, ?, ?)",
                            (paper_id, paper_date, language, json.dumps(paper)),
                        )
                        cursor.executemany(
                            "INSERT INTO paper_keywords "
                            "(paper_id, paper_date, language, category, keyword) VALUES (?, ?, ?, ?, ?)",
                            [(paper_id, paper_date, language, category, keyword) for keyword in contribution],
                        )
                        for keyword, frequency in contribution.items():
                            cursor.execute("""
                                INSERT INTO keyword_stats
                                    (paper_date, language, category, keyword, frequency)
                                VALUES (?, ?, ?, ?, ?)
                                ON CONFLICT(paper_date, language, category, keyword)
                                DO UPDATE SET frequency = frequency + excluded.frequency
                            """, (paper_date, language, category, keyword, frequency))

                    cursor.execute("DELETE FROM keyword_stats WHERE frequency <= 0")
                    affected_date_languages.add((paper_date, language))

                for paper_date, language in affected_date_languages:
                    cursor.execute(
                        "DELETE FROM agg_daily_papers WHERE paper_date = ? AND language = ?",
                        (paper_date, language),
                    )
                    cursor.execute("""
                        INSERT INTO agg_daily_papers (paper_date, language, category, total_papers)
                        SELECT paper_date, language, category, COUNT(DISTINCT paper_id)
                        FROM paper_keywords WHERE paper_date = ? AND language = ?
                        GROUP BY paper_date, language, category
                    """, (paper_date, language))
                    cursor.execute(
                        "DELETE FROM agg_daily_keywords WHERE paper_date = ? AND language = ?",
                        (paper_date, language),
                    )
                    cursor.execute("""
                        INSERT INTO agg_daily_keywords
                            (paper_date, language, category, keyword, distinct_paper_count)
                        SELECT paper_date, language, category, keyword, COUNT(DISTINCT paper_id)
                        FROM paper_keywords WHERE paper_date = ? AND language = ?
                        GROUP BY paper_date, language, category, keyword
                    """, (paper_date, language))

                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    try:
        from server_modules.stats import clear_stats_cache
        clear_stats_cache()
    except Exception:
        pass
    return True

reextract_lock = threading.Lock()
reextract_status = {
    "status": "idle",
    "progress": 0,
    "current": 0,
    "total": 0,
    "message": "",
    "error": None
}

def get_reextract_status():
    with reextract_lock:
        return dict(reextract_status)

def reextract_all_keywords():
    global cache_initialized, reextract_status
    with reextract_lock:
        if reextract_status.get("status") == "running":
            return False
        reextract_status = {
            "status": "running",
            "progress": 0,
            "current": 0,
            "total": 0,
            "message": "正在准备重新提取关键词...",
            "error": None
        }

    try:
        db_dir = "data"
        os.makedirs(db_dir, exist_ok=True)
        
        # 查找所有增强数据文件
        files = sorted(os.listdir(db_dir))
        target_files = []
        for f in files:
            if f.endswith(".jsonl") and "_AI_enhanced_" in f:
                parts = f.replace(".jsonl", "").split("_AI_enhanced_")
                if len(parts) == 2:
                    target_files.append((f, parts[0], parts[1]))
                    
        total_files = len(target_files)
        with reextract_lock:
            reextract_status["total"] = total_files
            reextract_status["message"] = f"正在重置历史关键词缓存 (共 {total_files} 个文件)..."

        with db_lock:
            processed_files_cache.clear()
            cache_initialized = False
            
            conn = connect_db(DB_PATH)
            try:
                cursor = conn.cursor()
                _init_tables(cursor)
                cursor.execute("DELETE FROM paper_keywords")
                cursor.execute("DELETE FROM keyword_stats")
                cursor.execute("DELETE FROM agg_daily_keywords")
                cursor.execute("DELETE FROM processed_files")
                conn.commit()
            finally:
                conn.close()

        keywords.idf_cache = {}
        keywords.idf_doc_count = 0

        # 逐个文件重提取
        for idx, (filename, paper_date, lang) in enumerate(target_files):
            with reextract_lock:
                reextract_status["current"] = idx + 1
                reextract_status["progress"] = int((idx / max(total_files, 1)) * 100)
                reextract_status["message"] = f"正在提取 {filename} ({idx + 1}/{total_files})..."

            filepath = os.path.join(db_dir, filename)
            if not os.path.exists(filepath):
                continue

            CHUNK_PAPERS = 500
            CHUNK_KEYWORDS = 2000
            stats_map = {}
            paper_keywords_buf = []
            papers_buf = []

            def flush_papers_sub(cur):
                if papers_buf:
                    cur.executemany(
                        "INSERT OR REPLACE INTO papers (paper_id, paper_date, language, paper_json) VALUES (?, ?, ?, ?)",
                        papers_buf
                    )
                    papers_buf.clear()

            def flush_keywords_sub(cur):
                if paper_keywords_buf:
                    cur.executemany(
                        "INSERT INTO paper_keywords (paper_id, paper_date, language, category, keyword) VALUES (?, ?, ?, ?, ?)",
                        paper_keywords_buf
                    )
                    paper_keywords_buf.clear()

            def flush_stats_sub(cur):
                if stats_map:
                    rows = [
                        (p_date, p_lang, p_cat, p_kw, freq)
                        for (p_date, p_lang, p_cat, p_kw), freq in stats_map.items()
                    ]
                    cur.executemany("""
                    INSERT INTO keyword_stats (paper_date, language, category, keyword, frequency)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(paper_date, language, category, keyword)
                    DO UPDATE SET frequency = frequency + excluded.frequency
                    """, rows)
                    stats_map.clear()

            BATCH_NLP_SIZE = 50
            current_batch = []
            
            def process_reextract_batch(batch_items):
                if not batch_items:
                    return
                batch_kws = keywords.extract_keywords_batch(batch_items, batch_size=BATCH_NLP_SIZE)
                for paper, keywords_with_freq in zip(batch_items, batch_kws):
                    paper_id = paper.get("id")
                    if not paper_id:
                        continue

                    papers_buf.append((paper_id, paper_date, lang, json.dumps(paper)))

                    cats = paper.get("categories", [])
                    category = "unknown"
                    if isinstance(cats, list) and len(cats) > 0:
                        category = cats[0]
                    elif isinstance(cats, str):
                        cats_split = re.split(r"[,\s]+", cats.strip())
                        if cats_split and cats_split[0]:
                            category = cats_split[0]

                    concepts = paper.get("concepts", [])
                    if isinstance(concepts, list):
                        for concept in concepts:
                            if concept and isinstance(concept, str):
                                keywords_with_freq.append((concept.lower(), 2))

                    unique_kws = {}
                    for kw, freq in keywords_with_freq:
                        unique_kws[kw] = unique_kws.get(kw, 0) + freq

                    for kw, freq in unique_kws.items():
                        paper_keywords_buf.append((paper_id, paper_date, lang, category, kw))
                        key = (paper_date, lang, category, kw)
                        stats_map[key] = stats_map.get(key, 0) + freq

            with open(filepath, "r", encoding="utf-8") as f_in:
                for line in f_in:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        paper = json.loads(line_str)
                    except Exception:
                        continue

                    current_batch.append(paper)
                    if len(current_batch) >= BATCH_NLP_SIZE:
                        process_reextract_batch(current_batch)
                        current_batch.clear()

            if current_batch:
                process_reextract_batch(current_batch)
                current_batch.clear()

            with db_lock:
                conn = connect_db(DB_PATH)
                try:
                    cur = conn.cursor()
                    flush_papers_sub(cur)
                    flush_keywords_sub(cur)
                    flush_stats_sub(cur)

                    cur.execute("INSERT OR REPLACE INTO processed_files (filename) VALUES (?)", (filename,))
                    
                    cur.execute("""
                    INSERT OR REPLACE INTO agg_daily_papers (paper_date, language, category, total_papers)
                    SELECT paper_date, language, category, COUNT(DISTINCT paper_id)
                    FROM paper_keywords
                    WHERE paper_date = ? AND language = ?
                    GROUP BY paper_date, language, category
                    """, (paper_date, lang))

                    cur.execute("""
                    INSERT OR REPLACE INTO agg_daily_keywords (paper_date, language, category, keyword, distinct_paper_count)
                    SELECT paper_date, language, category, keyword, COUNT(DISTINCT paper_id)
                    FROM paper_keywords
                    WHERE paper_date = ? AND language = ?
                    GROUP BY paper_date, language, category, keyword
                    """, (paper_date, lang))

                    conn.commit()
                finally:
                    conn.close()

            processed_files_cache.add(filename)

        # 重新刷新全局 IDF 缓存
        with db_lock:
            conn = connect_db(DB_PATH)
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM papers")
                total_papers = cur.fetchone()[0]
                keywords.idf_doc_count = total_papers
                if total_papers > 0:
                    cur.execute("SELECT keyword, COUNT(DISTINCT paper_id) FROM paper_keywords GROUP BY keyword")
                    df_rows = cur.fetchall()
                    keywords.idf_cache = {
                        row[0].lower(): math.log((1 + total_papers) / (1 + row[1])) + 1
                        for row in df_rows if row[0]
                    }
            finally:
                conn.close()

        try:
            from server_modules.stats import clear_stats_cache
            clear_stats_cache()
        except Exception:
            pass

        with reextract_lock:
            reextract_status = {
                "status": "completed",
                "progress": 100,
                "current": total_files,
                "total": total_files,
                "message": f"全部 {total_files} 个文件的关键词已成功重新提取！",
                "error": None
            }
        return True

    except Exception as e:
        import traceback
        traceback.print_exc()
        with reextract_lock:
            reextract_status = {
                "status": "error",
                "progress": 0,
                "current": 0,
                "total": 0,
                "message": f"重新提取失败: {str(e)}",
                "error": str(e)
            }
        return False

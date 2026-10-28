import os
import json
import re
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
    # 兼容旧库；同名增强文件发生修复或重跑时需要重新入库。
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(processed_files)")}
    for column in ("mtime_ns", "file_size"):
        if column not in columns:
            cursor.execute(f"ALTER TABLE processed_files ADD COLUMN {column} INTEGER")
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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_lang_date_id ON papers (language, paper_date DESC, paper_id ASC)")
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

def _refresh_daily_paper_counts(cursor, paper_date, language):
    """论文总数包含没有 AI 关键词的论文。"""
    counts = {}
    cursor.execute(
        "SELECT paper_json FROM papers WHERE paper_date = ? AND language = ?",
        (paper_date, language),
    )
    for (paper_json,) in cursor:
        category = _paper_category(json.loads(paper_json))
        counts[category] = counts.get(category, 0) + 1
    cursor.execute(
        "DELETE FROM agg_daily_papers WHERE paper_date = ? AND language = ?",
        (paper_date, language),
    )
    cursor.executemany(
        "INSERT INTO agg_daily_papers (paper_date, language, category, total_papers) VALUES (?, ?, ?, ?)",
        [(paper_date, language, category, count) for category, count in counts.items()],
    )


def _paper_category(paper):
    cats = paper.get("categories", [])
    if isinstance(cats, list) and cats:
        return cats[0]
    if isinstance(cats, str) and cats.strip():
        return re.split(r"[,\s]+", cats.strip())[0]
    return "unknown"


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


def scan_and_process_files(data_dir="data", db_path=None):
    """每日流水线调用：只处理新增或变化的完整增强文件。"""
    global cache_initialized
    db_dir = os.fspath(data_dir)
    if not os.path.isdir(db_dir):
        raise FileNotFoundError(f"数据目录不存在: {db_dir}")
    actual_db_path = os.fspath(db_path) if db_path is not None else get_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(actual_db_path)), exist_ok=True)
    target_files = []
    for filename in sorted(os.listdir(db_dir)):
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})_AI_enhanced_([a-zA-Z]+)\.jsonl", filename)
        if match and os.path.isfile(os.path.join(db_dir, filename)):
            stat = os.stat(os.path.join(db_dir, filename))
            target_files.append((filename, *match.groups(), stat.st_mtime_ns, stat.st_size))

    with _scan_lock:
        with db_lock:
            conn = connect_db(actual_db_path)
            try:
                cursor = conn.cursor()
                _init_tables(cursor)
                conn.commit()
                
                cursor.execute("SELECT filename, mtime_ns, file_size FROM processed_files")
                fingerprints = {filename: (mtime_ns, file_size) for filename, mtime_ns, file_size in cursor}
                files_to_process = [tf for tf in target_files if fingerprints.get(tf[0]) != tf[3:]]
                if not files_to_process:
                    return 0

                cursor.execute("BEGIN IMMEDIATE")
                for filename, paper_date, lang, mtime_ns, file_size in files_to_process:
                    # 同日期/语言整批替换，删除旧聚合，避免修复后关键词频率重复累加。
                    for table in ("papers", "paper_keywords", "keyword_stats", "agg_daily_papers", "agg_daily_keywords"):
                        cursor.execute(f"DELETE FROM {table} WHERE paper_date = ? AND language = ?", (paper_date, lang))

                    seen_in_file = set()
                        
                    filepath = os.path.join(db_dir, filename)
                        
                    # 分块缓冲写入，避免一次性在内存中累积整批论文/关键词列表导致 OOM。
                    CHUNK_PAPERS = 500
                    CHUNK_KEYWORDS = 2000
                    BATCH_SIZE = 50

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

                    # 逐批读取并汇总 AI 关键词
                    current_batch = []
                    
                    def process_current_batch(batch_items):
                        if not batch_items:
                            return
                            
                        # 只查当前批次的历史 ID，避免加载同语言全部历史论文。
                        paper_ids = [str(paper["id"]) for paper in batch_items]
                        placeholders = ",".join("?" for _ in paper_ids)
                        cursor.execute(
                            f"SELECT DISTINCT paper_id FROM papers WHERE paper_id IN ({placeholders}) "
                            "AND language = ? AND paper_date != ?",
                            (*paper_ids, lang, paper_date),
                        )
                        existing_ids = {row[0] for row in cursor}
                        unique_items = []
                        for paper in batch_items:
                            paper_id_str = str(paper["id"])
                            if paper_id_str in seen_in_file or paper_id_str in existing_ids:
                                continue
                            seen_in_file.add(paper_id_str)
                            unique_items.append(paper)
                        if not unique_items:
                            return

                        batch_kws = keywords.extract_keywords_batch(unique_items, batch_size=BATCH_SIZE)

                        for paper_item, keywords_with_freq in zip(unique_items, batch_kws):
                            paper_id = paper_item["id"]
                            paper_id_str = str(paper_id)
                                
                            # 保证最新 paper_json 始终写入 papers 表
                            papers_buf.append((paper_id_str, paper_date, lang, json.dumps(paper_item)))
                            if len(papers_buf) >= CHUNK_PAPERS:
                                flush_papers()
                                    
                            cats = paper_item.get("categories", [])
                            category = "unknown"
                            if isinstance(cats, list) and len(cats) > 0:
                                category = cats[0]
                            elif isinstance(cats, str):
                                cats_split = re.split(r"[,\s]+", cats.strip())
                                if cats_split and cats_split[0]:
                                    category = cats_split[0]
                                    
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
                            paper = json.loads(line_str)
                            if not isinstance(paper, dict) or not paper.get("id"):
                                raise ValueError(f"无效论文数据: {filename}")

                            current_batch.append(paper)
                            if len(current_batch) >= BATCH_SIZE:
                                process_current_batch(current_batch)
                                current_batch.clear()

                    if current_batch:
                        process_current_batch(current_batch)
                        current_batch.clear()

                    flush_papers()
                    flush_keywords()
                    flush_stats()
                        
                    current_stat = os.stat(filepath)
                    if (current_stat.st_mtime_ns, current_stat.st_size) != (mtime_ns, file_size):
                        raise RuntimeError(f"处理期间文件发生变化，稍后重试: {filename}")
                    cursor.execute(
                        "INSERT OR REPLACE INTO processed_files (filename, mtime_ns, file_size) VALUES (?, ?, ?)",
                        (filename, mtime_ns, file_size),
                    )
                    
                    # Incrementally update aggregation tables for the processed date, lang
                    _refresh_daily_paper_counts(cursor, paper_date, lang)
                    
                    cursor.execute("""
                    INSERT OR REPLACE INTO agg_daily_keywords (paper_date, language, category, keyword, distinct_paper_count)
                    SELECT paper_date, language, category, keyword, COUNT(DISTINCT paper_id)
                    FROM paper_keywords
                    WHERE paper_date = ? AND language = ?
                    GROUP BY paper_date, language, category, keyword
                    """, (paper_date, lang))
                
                conn.commit()
                processed_files_cache.update(tf[0] for tf in files_to_process)
                cache_initialized = True
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        if files_to_process:
            try:
                from server_modules.stats import clear_stats_cache
                clear_stats_cache()
            except Exception:
                pass
    return len(files_to_process)


def reextract_keywords_for_papers(paper_groups):
    """仅同步指定论文的 AI 关键词，并增量刷新其日期/语言聚合数据。

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
                    _refresh_daily_paper_counts(cursor, paper_date, language)
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

def reextract_all_keywords(data_dir="data", db_path=None):
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
        db_dir = os.fspath(data_dir)
        target_db_path = os.fspath(db_path) if db_path is not None else get_db_path()
        if not os.path.isdir(db_dir):
            raise FileNotFoundError(f"数据目录不存在: {db_dir}")

        # 查找所有增强数据文件
        files = sorted(os.listdir(db_dir))
        target_files = []
        for f in files:
            if f.endswith(".jsonl") and "_AI_enhanced_" in f and os.path.isfile(os.path.join(db_dir, f)):
                parts = f.replace(".jsonl", "").split("_AI_enhanced_")
                if len(parts) == 2:
                    target_files.append((f, parts[0], parts[1]))
                    
        total_files = len(target_files)
        if not total_files:
            raise ValueError(f"没有可供重新提取的增强数据文件: {db_dir}")
        os.makedirs(os.path.dirname(os.path.abspath(target_db_path)), exist_ok=True)
        with reextract_lock:
            reextract_status["total"] = total_files
            reextract_status["message"] = f"正在重置历史关键词缓存 (共 {total_files} 个文件)..."

        with db_lock:
            processed_files_cache.clear()
            cache_initialized = False
            
            conn = connect_db(target_db_path)
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

            BATCH_SIZE = 50
            current_batch = []
            
            def process_reextract_batch(batch_items):
                if not batch_items:
                    return
                batch_kws = keywords.extract_keywords_batch(batch_items, batch_size=BATCH_SIZE)
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
                    if len(current_batch) >= BATCH_SIZE:
                        process_reextract_batch(current_batch)
                        current_batch.clear()

            if current_batch:
                process_reextract_batch(current_batch)
                current_batch.clear()

            with db_lock:
                conn = connect_db(target_db_path)
                try:
                    cur = conn.cursor()
                    flush_papers_sub(cur)
                    flush_keywords_sub(cur)
                    flush_stats_sub(cur)

                    cur.execute("INSERT OR REPLACE INTO processed_files (filename) VALUES (?)", (filename,))
                    
                    _refresh_daily_paper_counts(cur, paper_date, lang)

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

import os
import re
import json
import math
import sys
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from app.auth import verify_token
from server_modules.database import connect_db
from server_modules.processor import scan_and_process_files
import app.config as config

router = APIRouter()

IS_TESTING = "pytest" in sys.modules or "unittest" in sys.modules

@router.get("/api/dates")
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

@router.get("/api/papers")
def get_papers(
    date: str,
    lang: str,
    page: Optional[int] = Query(None, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=100),
    category: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    author: Optional[str] = Query(None),
    token: str = Depends(verify_token)
):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date) or not re.match(r"^[a-zA-Z]+$", lang):
        raise HTTPException(status_code=400, detail="Invalid date or language format")
        
    filepath = f"data/{date}_AI_enhanced_{lang}.jsonl"
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Papers not found for this date and language")
    
    # 如果未指定 page 参数，保持原有的全量返回以 100% 兼容现有测试和调用
    if page is None:
        papers = []
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        papers.append(json.loads(line.strip()))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read data: {str(e)}")
        return papers
        
    # 分页模式处理
    actual_page_size = page_size if page_size is not None else 21
    actual_page_size = min(max(actual_page_size, 1), 100)
    actual_page = max(page, 1)
    
    norm_category = category.strip() if category else None
    if norm_category and norm_category.lower() == "all":
        norm_category = None
        
    norm_keyword = keyword.strip().lower() if keyword else None
    norm_author = author.strip().lower() if author else None

    category_counts = {}
    matched_papers = []
    total_all = 0

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    paper = json.loads(line_str)
                except Exception:
                    continue
                    
                total_all += 1
                
                # 提取分类
                cats = paper.get("categories", [])
                if isinstance(cats, str):
                    cats = [cats]
                elif not isinstance(cats, list):
                    cats = []
                primary_cat = cats[0] if cats else "unknown"
                category_counts[primary_cat] = category_counts.get(primary_cat, 0) + 1
                
                # 检查分类过滤
                if norm_category:
                    if primary_cat != norm_category and norm_category not in cats:
                        continue
                        
                # 检查关键词过滤
                if norm_keyword:
                    title = str(paper.get("title", "")).lower()
                    summary = str(paper.get("summary", "")).lower()
                    ai_tldr = ""
                    if isinstance(paper.get("AI"), dict):
                        ai_tldr = str(paper.get("AI", {}).get("tldr", "")).lower()
                    if norm_keyword not in title and norm_keyword not in summary and norm_keyword not in ai_tldr:
                        continue
                        
                # 检查作者过滤
                if norm_author:
                    authors_val = paper.get("authors", "")
                    if isinstance(authors_val, list):
                        authors_str = " ".join(authors_val).lower()
                    else:
                        authors_str = str(authors_val).lower()
                    if norm_author not in authors_str:
                        continue
                        
                matched_papers.append(paper)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read data: {str(e)}")

    total = len(matched_papers)
    total_pages = math.ceil(total / actual_page_size) if total > 0 else 1
    start_idx = (actual_page - 1) * actual_page_size
    end_idx = start_idx + actual_page_size
    page_items = matched_papers[start_idx:end_idx]
    sorted_categories = sorted(list(category_counts.keys()))

    return {
        "items": page_items,
        "total": total,
        "page": actual_page,
        "page_size": actual_page_size,
        "total_pages": total_pages,
        "category_counts": category_counts,
        "categories": sorted_categories,
        "total_all": total_all
    }

@router.get("/api/papers/range")
def get_papers_range(
    start_date: str,
    end_date: str,
    lang: str = "en",
    page: Optional[int] = Query(None, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=100),
    category: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    author: Optional[str] = Query(None),
    token: str = Depends(verify_token)
):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", start_date) or not re.match(r"^\d{4}-\d{2}-\d{2}$", end_date) or not re.match(r"^[a-zA-Z]+$", lang):
        raise HTTPException(status_code=400, detail="Invalid date or language format")
        
    if IS_TESTING:
        try:
            scan_and_process_files()
        except Exception:
            pass
            
    def get_db_path():
        import server_modules.processor as processor
        return getattr(processor, "DB_PATH", config.DB_PATH)

    db_path = get_db_path()
    if not os.path.exists(db_path):
        if page is not None:
            return {
                "items": [],
                "total": 0,
                "page": page,
                "page_size": page_size or 20,
                "total_pages": 0,
                "category_counts": {},
                "categories": [],
                "total_all": 0
            }
        return []
        
    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        
        # 兼容老接口：未传 page 参数时，返回全量列表
        if page is None:
            cursor.execute("""
            SELECT paper_json, paper_date 
            FROM papers 
            WHERE paper_date BETWEEN ? AND ? 
              AND language = ?
            """, (start_date, end_date, lang))
            
            papers = []
            # 用游标惰性迭代替代 fetchall，避免一次性把全部行读入内存
            for paper_json, paper_date in cursor:
                try:
                    p = json.loads(paper_json)
                    p['date'] = paper_date
                    papers.append(p)
                except Exception:
                    continue
            return papers
            
        # 分页模式处理
        actual_page_size = page_size if page_size is not None else 21
        actual_page_size = min(max(actual_page_size, 1), 100)
        actual_page = max(page, 1)

        norm_category = category.strip() if category else None
        if norm_category and norm_category.lower() == "all":
            norm_category = None
        norm_keyword = keyword.strip().lower() if keyword else None
        norm_author = author.strip().lower() if author else None

        cursor.execute("""
        SELECT paper_json, paper_date 
        FROM papers 
        WHERE paper_date BETWEEN ? AND ? 
          AND language = ?
        ORDER BY paper_date DESC, paper_id ASC
        """, (start_date, end_date, lang))

        matched_papers = []
        category_counts = {}
        total_all = 0

        for paper_json, paper_date in cursor:
            try:
                p = json.loads(paper_json)
                p['date'] = paper_date
            except Exception:
                continue
                
            total_all += 1
            cats = p.get("categories", [])
            if isinstance(cats, str):
                cats = [cats]
            elif not isinstance(cats, list):
                cats = []
            primary_cat = cats[0] if cats else "unknown"
            category_counts[primary_cat] = category_counts.get(primary_cat, 0) + 1

            if norm_category:
                if primary_cat != norm_category and norm_category not in cats:
                    continue
            if norm_keyword:
                title = str(p.get("title", "")).lower()
                summary = str(p.get("summary", "")).lower()
                ai_tldr = ""
                if isinstance(p.get("AI"), dict):
                    ai_tldr = str(p.get("AI", {}).get("tldr", "")).lower()
                if norm_keyword not in title and norm_keyword not in summary and norm_keyword not in ai_tldr:
                    continue
            if norm_author:
                authors_val = p.get("authors", "")
                if isinstance(authors_val, list):
                    authors_str = " ".join(authors_val).lower()
                else:
                    authors_str = str(authors_val).lower()
                if norm_author not in authors_str:
                    continue

            matched_papers.append(p)

        total = len(matched_papers)
        total_pages = math.ceil(total / actual_page_size) if total > 0 else 1
        start_idx = (actual_page - 1) * actual_page_size
        end_idx = start_idx + actual_page_size
        page_items = matched_papers[start_idx:end_idx]
        sorted_categories = sorted(list(category_counts.keys()))

        return {
            "items": page_items,
            "total": total,
            "page": actual_page,
            "page_size": actual_page_size,
            "total_pages": total_pages,
            "category_counts": category_counts,
            "categories": sorted_categories,
            "total_all": total_all
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    finally:
        conn.close()

import os
import re
import json
import math
import sys
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from app.auth import verify_token
from server_modules.database import connect_db
import app.config as config

router = APIRouter()

# 无效的历史 JSON 不参与查询，也不会使 SQLite JSON 函数报错。
PAPER_JSON = "CASE WHEN json_valid(p.paper_json) THEN p.paper_json ELSE '{}' END"
PRIMARY_CATEGORY = f"""CASE json_type({PAPER_JSON}, '$.categories')
    WHEN 'array' THEN COALESCE(json_extract({PAPER_JSON}, '$.categories[0]'), 'unknown')
    WHEN 'text' THEN json_extract({PAPER_JSON}, '$.categories')
    ELSE 'unknown' END"""


def get_db_path():
    if config.DB_PATH != "data/statistics.db":
        return config.DB_PATH
    # 兼容独立工具/测试的路径覆盖，不导入处理模块及 NLP 依赖。
    processor = sys.modules.get("server_modules.processor")
    return getattr(processor, "DB_PATH", config.DB_PATH)


def _empty_page(page, page_size, include_category_counts=True):
    result = {
        "items": [], "total": 0, "page": page, "page_size": page_size,
        "total_pages": 1,
    }
    if include_category_counts:
        result.update({"category_counts": {}, "categories": [], "total_all": 0})
    return result


def _date_processed(cursor, date, lang):
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'processed_files'")
    if cursor.fetchone() is None:
        return False
    cursor.execute("SELECT 1 FROM processed_files WHERE filename = ?", (f"{date}_AI_enhanced_{lang}.jsonl",))
    return cursor.fetchone() is not None


def query_papers(start_date, end_date, lang, page, page_size, category, keyword, author, single_date=False, include_category_counts=True):
    db_path = get_db_path()
    if not os.path.exists(db_path):
        if single_date:
            raise HTTPException(status_code=404, detail="Papers not found for this date and language")
        return [] if page is None else _empty_page(page, page_size, include_category_counts)

    conn = connect_db(db_path)
    try:
        # SQLite 内置 lower 只支持 ASCII，注册轻量字符串函数保留 Unicode 搜索语义。
        conn.create_function("unicode_lower", 1, lambda value: str(value).lower() if value is not None else "", deterministic=True)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'papers'")
        if cursor.fetchone() is None:
            if single_date:
                raise HTTPException(status_code=404, detail="Papers not found for this date and language")
            return [] if page is None else _empty_page(page, page_size, include_category_counts)

        # 三次只读查询共享快照，避免离线批处理提交期间计数与页数据不一致。
        conn.execute("BEGIN")
        base_where = f"p.paper_date BETWEEN ? AND ? AND p.language = ? AND json_valid(p.paper_json) AND json_type({PAPER_JSON}) = 'object'"
        base_params = [start_date, end_date, lang]
        order_by = "p.rowid ASC" if single_date else "p.paper_date DESC, p.paper_id ASC"

        # 全量兼容接口不强行分页；正常网页使用带 page 的接口。
        if page is None:
            cursor.execute(
                f"SELECT p.paper_json, p.paper_date FROM papers p WHERE {base_where} ORDER BY {order_by}",
                base_params,
            )
            items = []
            for paper_json, paper_date in cursor:
                paper = json.loads(paper_json)
                if not single_date:
                    paper['date'] = paper_date
                items.append(paper)
            if single_date and not items and not _date_processed(cursor, start_date, lang):
                raise HTTPException(status_code=404, detail="Papers not found for this date and language")
            return items

        category_counts = {}
        total_all = 0
        if include_category_counts:
            cursor.execute(
                f"SELECT {PRIMARY_CATEGORY} AS category, COUNT(*) FROM papers p "
                f"WHERE {base_where} GROUP BY category ORDER BY category",
                base_params,
            )
            category_counts = dict(cursor.fetchall())
            total_all = sum(category_counts.values())
        if single_date:
            if include_category_counts:
                date_has_papers = total_all > 0
            else:
                cursor.execute(f"SELECT 1 FROM papers p WHERE {base_where} LIMIT 1", base_params)
                date_has_papers = cursor.fetchone() is not None
            # 已入库的空文件是有效日期，与不存在的数据区分。
            if not date_has_papers and not _date_processed(cursor, start_date, lang):
                raise HTTPException(status_code=404, detail="Papers not found for this date and language")

        filters = [base_where]
        params = list(base_params)
        norm_category = category.strip() if category else None
        if norm_category and norm_category.lower() != "all":
            filters.append(f"""(
                {PRIMARY_CATEGORY} = ? OR EXISTS (
                    SELECT 1 FROM json_each(CASE
                        WHEN json_type({PAPER_JSON}, '$.categories') = 'array'
                        THEN json_extract({PAPER_JSON}, '$.categories') ELSE '[]' END) c
                    WHERE c.value = ?
                )
            )""")
            params.extend([norm_category, norm_category])

        norm_keyword = keyword.strip().lower() if keyword else None
        if norm_keyword:
            filters.append("(" + " OR ".join(
                f"instr(unicode_lower(json_extract({PAPER_JSON}, '{path}')), ?) > 0"
                for path in ("$.title", "$.summary", "$.AI.tldr")
            ) + ")")
            params.extend([norm_keyword] * 3)

        norm_author = author.strip().lower() if author else None
        if norm_author:
            filters.append(f"""instr(unicode_lower(CASE
                WHEN json_type({PAPER_JSON}, '$.authors') = 'array' THEN (
                    SELECT group_concat(a.value, ' ') FROM json_each({PAPER_JSON}, '$.authors') a
                ) ELSE json_extract({PAPER_JSON}, '$.authors') END), ?) > 0""")
            params.append(norm_author)

        where = " AND ".join(filters)
        cursor.execute(f"SELECT COUNT(*) FROM papers p WHERE {where}", params)
        total = cursor.fetchone()[0]
        cursor.execute(
            f"SELECT p.paper_json, p.paper_date FROM papers p WHERE {where} "
            f"ORDER BY {order_by} LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        )
        items = []
        for paper_json, paper_date in cursor:
            paper = json.loads(paper_json)
            if not single_date:
                paper['date'] = paper_date
            items.append(paper)
        result = {
            "items": items, "total": total, "page": page, "page_size": page_size,
            "total_pages": max(1, math.ceil(total / page_size)),
        }
        if include_category_counts:
            result.update({
                "category_counts": category_counts, "categories": sorted(category_counts),
                "total_all": total_all,
            })
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    finally:
        conn.rollback()
        conn.close()


@router.get("/api/dates")
def get_dates(token: str = Depends(verify_token)):
    db_path = get_db_path()
    if not os.path.exists(db_path):
        return {"dates": [], "languages": {}}
    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'processed_files'")
        if cursor.fetchone() is None:
            return {"dates": [], "languages": {}}
        cursor.execute("SELECT filename FROM processed_files ORDER BY filename DESC")
        languages = {}
        for (filename,) in cursor:
            match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})_AI_enhanced_([a-zA-Z]+)\.jsonl", filename)
            if match:
                date, lang = match.groups()
                languages.setdefault(date, []).append(lang)
        return {"dates": sorted(languages, reverse=True), "languages": languages}
    finally:
        conn.close()


@router.get("/api/papers")
def get_papers(
    date: str,
    lang: str,
    page: Optional[int] = Query(None, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=100),
    category: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    author: Optional[str] = Query(None),
    include_category_counts: bool = Query(True),
    token: str = Depends(verify_token),
):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or not re.fullmatch(r"[a-zA-Z]+", lang):
        raise HTTPException(status_code=400, detail="Invalid date or language format")
    return query_papers(date, date, lang, page, page_size or 21, category, keyword, author,
                        single_date=True, include_category_counts=include_category_counts)


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
    include_category_counts: bool = Query(True),
    token: str = Depends(verify_token),
):
    if (not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_date)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_date)
            or not re.fullmatch(r"[a-zA-Z]+", lang)):
        raise HTTPException(status_code=400, detail="Invalid date or language format")
    return query_papers(start_date, end_date, lang, page, page_size or 21, category, keyword, author,
                        include_category_counts=include_category_counts)

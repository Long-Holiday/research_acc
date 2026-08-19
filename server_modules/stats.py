import os
import re
import json
import math
import threading
from datetime import datetime, timedelta
from typing import List, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException
from app.auth import verify_token
from server_modules.database import connect_db
from server_modules.processor import scan_and_process_files
import server_modules.processor as processor
from server_modules.analytics import community_detection
from ai.keyword_filter import filter_meaningless_keywords
import app.config as config

router = APIRouter()


def _filter_network_links(links, min_value=2, max_links=180, max_degree=12):
    """Keep deterministic strong links with high specificity while limiting visual network density."""
    candidates = []
    for link in links:
        source = str(link.get("source", ""))
        target = str(link.get("target", ""))
        value = float(link.get("value", 0))
        npmi = float(link.get("npmi", 0.0))
        if source and target and source != target and value >= min_value:
            sort_weight = value * (1.0 + max(0.0, npmi))
            candidates.append({"source": source, "target": target, "value": value, "sort_weight": sort_weight})

    candidates.sort(key=lambda link: (
        -link.get("sort_weight", link["value"]), -link["value"], link["source"], link["target"]
    ))

    degree = {}
    filtered = []
    for link in candidates:
        source_degree = degree.get(link["source"], 0)
        target_degree = degree.get(link["target"], 0)
        if source_degree >= max_degree or target_degree >= max_degree:
            continue
        filtered.append(link)
        degree[link["source"]] = source_degree + 1
        degree[link["target"]] = target_degree + 1
        if len(filtered) >= max_links:
            break

    return [
        {
            "source": link["source"],
            "target": link["target"],
            "value": int(link["value"]) if link["value"].is_integer() else link["value"],
        }
        for link in filtered
    ]


# Helper to fetch journals Safely
try:
    from daily_paper.daily_journals.constants import JOURNALS
except ModuleNotFoundError:
    import sys
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.append(os.path.join(project_root, 'daily_paper'))
    from daily_journals.constants import JOURNALS

def fetch_top_papers_from_openalex(issn_list, from_date):
    import requests
    issn_str = "|".join(issn_list)
    headers = {
        "User-Agent": "daily-arXiv-ai-enhanced/1.0 (mailto:dw-dengwei@users.noreply.github.com)"
    }
    api_key = os.environ.get("OPENALEX_API_KEY", "")
    
    # 对于 IEEE 等只记录出版年份（如 2026-01-01）的期刊，将查询起始日期拓展到当年年初，避免被排查在外
    try:
        from_year = from_date.split("-")[0]
        year_start = f"{from_year}-01-01"
        query_from_date = min(from_date, year_start)
    except Exception:
        query_from_date = from_date

    url = "https://api.openalex.org/works"
    params = {
        "filter": f"primary_location.source.issn:{issn_str},from_publication_date:{query_from_date}",
        "sort": "cited_by_count:desc",
        "per_page": 200,  # 获取更多候选论文以进行速率和热度排序
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
        
        # Calculate citations per day and hotness score using Bayesian smoothed decay
        citations_per_day = 0.0
        hotness_score = 0.0
        if pub_date:
            try:
                pub_dt = datetime.strptime(pub_date, "%Y-%m-%d")
                days_since = (datetime.now() - pub_dt).days
                days_since = max(days_since, 0)
                citations_per_day = round(cited_by / max(days_since, 1), 2)
                # Bayesian smoothed half-life decay model: avoids zero-citation cliff & over-punishing 30-90 day papers
                half_life_days = 90.0
                decay = math.exp(-0.693 * days_since / half_life_days)
                hotness_score = round(((cited_by + 0.1) / math.pow(days_since + 5, 0.75)) * (1.0 + decay), 4)
            except Exception:
                pass

        formatted_papers.append({
            "id": paper.get("id") or "",
            "title": title,
            "authors": authors_str,
            "cited_by_count": cited_by,
            "citations_per_day": citations_per_day,
            "hotness_score": hotness_score,
            "url": paper_url,
            "publication_date": pub_date
        })
        
    return formatted_papers

import sys
import time
IS_TESTING = "pytest" in sys.modules or "unittest" in sys.modules

# 服务端统计结果内存缓存 (TTL 300秒)
stats_cache = {}


def _calculate_keyword_trend(date_distribution, total_papers_map):
    """Estimate a keyword trend without overreacting to sparse publication days.

    The old implementation used Mann-Kendall over every calendar day.  Days with
    no papers were consequently treated as a real 0% observation and a keyword
    with only a handful of hits could receive the maximum trend score.  Here we
    compare chronological, paper-volume-normalised windows, apply an empirical
    Bayes prior, and shrink the result by its statistical confidence.
    """
    observations = []
    for date in sorted(total_papers_map):
        total = int(total_papers_map.get(date) or 0)
        if total <= 0:
            continue
        count = min(int(date_distribution.get(date, 0) or 0), total)
        observations.append((date, count, total))

    total_count = sum(item[1] for item in observations)
    total_papers = sum(item[2] for item in observations)
    neutral = {
        "growth_rate": 0.0,
        "trend_score": 0.0,
        "trend_confidence": 0.0,
        "early_rate": 0.0,
        "recent_rate": 0.0,
    }
    if len(observations) < 3 or total_count < 3 or total_papers <= 0:
        return neutral

    # Compare equally sized chronological windows.  With an odd number of
    # observations the centre date is deliberately neutral and omitted.
    window_size = len(observations) // 2
    early = observations[:window_size]
    recent = observations[-window_size:]
    early_count, early_total = sum(x[1] for x in early), sum(x[2] for x in early)
    recent_count, recent_total = sum(x[1] for x in recent), sum(x[2] for x in recent)
    if early_total <= 0 or recent_total <= 0:
        return neutral

    overall_rate = total_count / total_papers
    # A small data-driven prior prevents 0 -> 1 occurrences from becoming an
    # infinite/maximum increase while remaining negligible for large windows.
    prior_strength = min(20.0, max(4.0, math.sqrt(total_papers) * 0.5))
    prior_hits = overall_rate * prior_strength
    early_rate = (early_count + prior_hits) / (early_total + prior_strength)
    recent_rate = (recent_count + prior_hits) / (recent_total + prior_strength)

    # Symmetric relative change is bounded to [-2, 2] and behaves sensibly when
    # the early rate is near zero.  It is the value shown to users as growth.
    denominator = max((early_rate + recent_rate) / 2.0, 1.0 / (total_papers + prior_strength))
    growth_rate = max(-2.0, min(2.0, (recent_rate - early_rate) / denominator))

    pooled = (early_count + recent_count + 2 * prior_hits) / (
        early_total + recent_total + 2 * prior_strength
    )
    standard_error = math.sqrt(max(
        pooled * (1.0 - pooled) * (1.0 / (early_total + prior_strength) +
                                   1.0 / (recent_total + prior_strength)),
        1e-12,
    ))
    z_score = abs(recent_rate - early_rate) / standard_error
    sample_reliability = 1.0 - math.exp(-total_count / 8.0)
    confidence = (1.0 - math.exp(-z_score / 2.0)) * sample_reliability
    trend_score = max(-1.0, min(1.0, growth_rate * confidence))

    # A dead band avoids classifying visually tiny/noisy changes as a trend.
    if confidence < 0.2 or abs(trend_score) < 0.05:
        trend_score = 0.0

    return {
        "growth_rate": round(growth_rate, 4),
        "trend_score": round(trend_score, 4),
        "trend_confidence": round(confidence, 4),
        "early_rate": round(early_rate * 100, 4),
        "recent_rate": round(recent_rate * 100, 4),
    }

def clear_stats_cache():
    stats_cache.clear()

def get_db_path():
    if config.DB_PATH != "data/statistics.db":
        return config.DB_PATH
    import server
    import server_modules.processor as processor
    if hasattr(server, "DB_PATH") and getattr(server, "DB_PATH") != "data/statistics.db":
        return getattr(server, "DB_PATH")
    if hasattr(processor, "DB_PATH") and getattr(processor, "DB_PATH") != "data/statistics.db":
        return getattr(processor, "DB_PATH")
    return config.DB_PATH

@router.get("/api/stats/categories")
def get_categories_stats(
    start_date: str,
    end_date: str,
    lang: str = "en",
    token: str = Depends(verify_token)
):
    if IS_TESTING:
        try:
            scan_and_process_files()
        except Exception:
            pass
            
    db_path = get_db_path()
    if not os.path.exists(db_path):
        return {"categories": [], "category_counts": {}, "total_all": 0}
        
    cache_key = f"cat_{start_date}_{end_date}_{lang}"
    if not IS_TESTING and cache_key in stats_cache:
        res, exp = stats_cache[cache_key]
        if time.time() < exp:
            return res
            
    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT category, SUM(total_papers)
        FROM agg_daily_papers
        WHERE paper_date BETWEEN ? AND ? AND language = ?
        GROUP BY category
        ORDER BY category ASC
        """, (start_date, end_date, lang))
        
        category_counts = {}
        total_all = 0
        for cat, cnt in cursor.fetchall():
            if cat and cat.strip():
                category_counts[cat] = cnt
                total_all += cnt
                
        categories = sorted(list(category_counts.keys()))
        result = {
            "categories": categories,
            "category_counts": category_counts,
            "total_all": total_all
        }
        if not IS_TESTING:
            stats_cache[cache_key] = (result, time.time() + 300)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch category stats: {str(e)}")
    finally:
        conn.close()

@router.get("/api/stats/keywords")
def get_keyword_stats(
    start_date: str, 
    end_date: str, 
    lang: str = "en", 
    category: str = "All", 
    token: str = Depends(verify_token)
):
    if IS_TESTING:
        try:
            scan_and_process_files()
        except Exception as e:
            pass
            
    db_path = get_db_path()
    if not os.path.exists(db_path):
        return {"keywords": [], "daily_trends": []}
        
    cache_key = f"kw_{start_date}_{end_date}_{lang}_{category}"
    if not IS_TESTING and cache_key in stats_cache:
        res, exp = stats_cache[cache_key]
        if time.time() < exp:
            return res
        
    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        
        # 1. Fetch daily total papers
        total_papers_map = {}
        if category == 'All':
            total_query = """
            SELECT paper_date, SUM(total_papers)
            FROM agg_daily_papers
            WHERE paper_date BETWEEN ? AND ? AND language = ?
            GROUP BY paper_date
            """
            cursor.execute(total_query, (start_date, end_date, lang))
        else:
            categories = category.split(',')
            placeholders = ','.join(['?'] * len(categories))
            total_query = f"""
            SELECT paper_date, SUM(total_papers)
            FROM agg_daily_papers
            WHERE paper_date BETWEEN ? AND ? AND language = ? AND category IN ({placeholders})
            GROUP BY paper_date
            """
            cursor.execute(total_query, [start_date, end_date, lang] + categories)
            
        for p_date, total in cursor.fetchall():
            total_papers_map[p_date] = total
            
        total_papers_in_period = sum(total_papers_map.values())
        
        # 2. Fetch keyword stats
        if category == 'All':
            kw_query = """
            SELECT keyword, category, paper_date, SUM(distinct_paper_count)
            FROM agg_daily_keywords
            WHERE paper_date BETWEEN ? AND ? AND language = ?
            GROUP BY keyword, category, paper_date
            """
            cursor.execute(kw_query, (start_date, end_date, lang))
        else:
            categories = category.split(',')
            placeholders = ','.join(['?'] * len(categories))
            kw_query = f"""
            SELECT keyword, category, paper_date, SUM(distinct_paper_count)
            FROM agg_daily_keywords
            WHERE paper_date BETWEEN ? AND ? AND language = ? AND category IN ({placeholders})
            GROUP BY keyword, category, paper_date
            """
            cursor.execute(kw_query, [start_date, end_date, lang] + categories)
            
        keyword_data = {}
        for keyword, cat, p_date, count in cursor.fetchall():
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

        # 3. Calculate volume-normalised metrics and confidence-adjusted trends
        for kw, entry in keyword_data.items():
            if total_papers_in_period > 0:
                entry["rate"] = round((entry["count"] / total_papers_in_period) * 100, 2)
            else:
                entry["rate"] = 0.0
                
            entry.update(_calculate_keyword_trend(entry["date_distribution"], total_papers_map))

        # Convert to list and sort by count descending
        keywords_list = sorted(keyword_data.values(), key=lambda x: x["count"], reverse=True)[:100]
        top_keywords = {item["keyword"] for item in keywords_list}

        # Build daily trends
        daily_trends = []
        for kw in top_keywords:
            for p_date, count in keyword_data[kw]["date_distribution"].items():
                total_on_date = total_papers_map.get(p_date, 0)
                rate = round((count / total_on_date) * 100, 2) if total_on_date > 0 else 0.0
                daily_trends.append({
                    "keyword": kw,
                    "date": p_date,
                    "count": count,
                    "rate": rate
                })
                
        daily_trends.sort(key=lambda x: x["date"])
        
        result = {
            "keywords": keywords_list,
            "daily_trends": daily_trends
        }
        if not IS_TESTING:
            stats_cache[cache_key] = (result, time.time() + 300)
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    finally:
        conn.close()

@router.get("/api/stats/network")
def get_network_stats(
    start_date: str, 
    end_date: str, 
    lang: str = "en", 
    category: str = "All", 
    exclude: str = "",
    token: str = Depends(verify_token)
):
    if IS_TESTING:
        try:
            scan_and_process_files()
        except Exception as e:
            pass
            
    db_path = get_db_path()
    if not os.path.exists(db_path):
        return {"nodes": [], "links": []}
        
    cache_key = f"net_{start_date}_{end_date}_{lang}_{category}_{exclude}"
    if not IS_TESTING and cache_key in stats_cache:
        res, exp = stats_cache[cache_key]
        if time.time() < exp:
            return res
        
    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        
        exclude_list = []
        if exclude:
            exclude_list = [w.strip() for w in exclude.split(",") if w.strip()]
            
        exclude_clause = ""
        exclude_params = []
        if exclude_list:
            exclude_clause = "AND keyword NOT IN (" + ",".join(["?"] * len(exclude_list)) + ")"
            exclude_params = exclude_list
        
        if category == 'All':
            query = f"""
            SELECT keyword, SUM(frequency) as total
            FROM keyword_stats
            WHERE paper_date BETWEEN ? AND ?
              AND language = ?
              {exclude_clause}
            GROUP BY keyword
            ORDER BY total DESC
            LIMIT 35
            """
            params = [start_date, end_date, lang] + exclude_params
        else:
            categories = category.split(',')
            placeholders = ','.join(['?'] * len(categories))
            query = f"""
            SELECT keyword, SUM(frequency) as total
            FROM keyword_stats
            WHERE paper_date BETWEEN ? AND ?
              AND language = ?
              AND category IN ({placeholders})
              {exclude_clause}
            GROUP BY keyword
            ORDER BY total DESC
            LIMIT 35
            """
            params = [start_date, end_date, lang] + categories + exclude_params
            
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
                JOIN paper_keywords pk2
                  ON pk1.paper_id = pk2.paper_id
                 AND pk1.paper_date = pk2.paper_date
                 AND pk1.language = pk2.language
                 AND pk1.category = pk2.category
                 AND pk1.keyword < pk2.keyword
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
                JOIN paper_keywords pk2
                  ON pk1.paper_id = pk2.paper_id
                 AND pk1.paper_date = pk2.paper_date
                 AND pk1.language = pk2.language
                 AND pk1.category = pk2.category
                 AND pk1.keyword < pk2.keyword
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
            
            # 计算总论文数与各节点的单点频次以求取 NPMI 特异性
            cursor.execute("SELECT COUNT(DISTINCT paper_id) FROM paper_keywords WHERE paper_date BETWEEN ? AND ? AND language = ?", (start_date, end_date, lang))
            total_papers_count = max(1, cursor.fetchone()[0] or 1)
            node_freq_map = {row[0]: row[1] for row in nodes_rows}
            
            raw_links = []
            for row in links_rows:
                s, t, cooccur = row[0], row[1], row[2]
                n_s = max(1, node_freq_map.get(s, 1))
                n_t = max(1, node_freq_map.get(t, 1))
                p_s = n_s / total_papers_count
                p_t = n_t / total_papers_count
                p_st = cooccur / total_papers_count
                
                npmi = 0.0
                if p_st > 0 and p_s > 0 and p_t > 0:
                    try:
                        pmi = math.log(p_st / (p_s * p_t))
                        npmi = pmi / (-math.log(p_st))
                    except Exception:
                        npmi = 0.0
                raw_links.append({"source": s, "target": t, "value": cooccur, "npmi": npmi})
                
            links = _filter_network_links(raw_links)
            linked_node_ids = {endpoint for link in links for endpoint in (
                link["source"], link["target"]
            )}
            nodes = [node for node in nodes if node["id"] in linked_node_ids]
            
        # Perform community detection on nodes and links
        try:
            community_detection(nodes, links)
        except Exception as e:
            print(f"Error doing community detection: {e}")
            
        result = {
            "nodes": nodes,
            "links": links
        }
        if not IS_TESTING:
            stats_cache[cache_key] = (result, time.time() + 300)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    finally:
        conn.close()

@router.get("/api/stats/journals")
def get_journals(token: str = Depends(verify_token)):
    return [{"name": j["name"], "category": j["category"]} for j in JOURNALS]

_hot_papers_memory_cache = {}

@router.get("/api/stats/hot-papers")
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
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    cache_key = (selected_journal["name"], period, today_str)
    
    # 清理旧日期缓存，防止内存持续增长
    keys_to_delete = [k for k in _hot_papers_memory_cache.keys() if k[2] != today_str]
    for k in keys_to_delete:
        _hot_papers_memory_cache.pop(k, None)
        
    if cache_key in _hot_papers_memory_cache:
        return _hot_papers_memory_cache[cache_key]
        
    db_path = get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = connect_db(db_path)
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT papers_json FROM hot_papers_cache
        WHERE journal = ? AND period = ? AND query_date = ?
        """, (selected_journal["name"], period, today_str))
        
        row = cursor.fetchone()
        if row:
            papers = json.loads(row[0])
            # Ensure citations_per_day and hotness_score are present for cached papers
            updated = False
            for paper in papers:
                pub_date = paper.get("publication_date") or ""
                cited_by = paper.get("cited_by_count") or 0
                
                if "citations_per_day" not in paper or "hotness_score" not in paper:
                    citations_per_day = 0.0
                    hotness_score = 0.0
                    if pub_date:
                        try:
                            pub_dt = datetime.strptime(pub_date, "%Y-%m-%d")
                            days_since = (datetime.now() - pub_dt).days
                            days_since = max(days_since, 0)
                            citations_per_day = round(cited_by / max(days_since, 1), 2)
                            half_life_days = 90.0
                            decay = math.exp(-0.693 * days_since / half_life_days)
                            hotness_score = round(((cited_by + 0.1) / math.pow(days_since + 5, 0.75)) * (1.0 + decay), 4)
                        except Exception:
                            pass
                    if "citations_per_day" not in paper:
                        paper["citations_per_day"] = citations_per_day
                    if "hotness_score" not in paper:
                        paper["hotness_score"] = hotness_score
                    updated = True
            
            _hot_papers_memory_cache[cache_key] = papers
            return papers
            
        # Cache miss, fetch from OpenAlex
        from_date = (datetime.now() - timedelta(days=period)).strftime("%Y-%m-%d")
        papers = fetch_top_papers_from_openalex(selected_journal["issns"], from_date)
        
        # Sort by hotness_score descending and keep top 50
        papers.sort(key=lambda x: x.get("hotness_score", 0.0), reverse=True)
        papers = papers[:50]
        
        # Store in cache
        cursor.execute("""
        INSERT OR REPLACE INTO hot_papers_cache (journal, period, query_date, papers_json)
        VALUES (?, ?, ?, ?)
        """, (selected_journal["name"], period, today_str, json.dumps(papers)))
        conn.commit()
        
        _hot_papers_memory_cache[cache_key] = papers
        return papers
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to fetch hot papers: {str(e)}")
    finally:
        conn.close()


class AIKeywordFilterRequest(BaseModel):
    keywords: List[str] = Field(default_factory=list, description="List of candidate keywords to filter")
    category: str = Field("All", description="Paper category context")
    model_name: Optional[str] = Field(None, description="Optional LLM model name")


@router.post("/api/stats/keywords/ai-filter")
def ai_filter_keywords(
    request: AIKeywordFilterRequest,
    token: str = Depends(verify_token)
):
    try:
        excluded = filter_meaningless_keywords(
            keywords=request.keywords,
            category=request.category,
            model_name=request.model_name
        )
        return {
            "status": "success",
            "excluded_keywords": excluded,
            "total_checked": len(request.keywords),
            "excluded_count": len(excluded)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI Keyword Filtering failed: {str(e)}")


@router.post("/api/stats/reextract-keywords")
def trigger_reextract_keywords(token: str = Depends(verify_token)):
    status = processor.get_reextract_status()
    if status.get("status") == "running":
        return {
            "status": "running",
            "message": "重新提取任务正在进行中",
            "details": status
        }
        
    thread = threading.Thread(target=processor.reextract_all_keywords, daemon=True)
    thread.start()
    return {
        "status": "started",
        "message": "已启动重新提取关键词任务"
    }


@router.get("/api/stats/reextract-status")
def get_reextract_status(token: str = Depends(verify_token)):
    return processor.get_reextract_status()

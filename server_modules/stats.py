import os
import re
import json
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from app.auth import verify_token
from server_modules.database import connect_db
from server_modules.processor import scan_and_process_files
from server_modules.analytics import community_detection
import app.config as config

router = APIRouter()

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
    
    url = "https://api.openalex.org/works"
    params = {
        "filter": f"primary_location.source.issn:{issn_str},from_publication_date:{from_date}",
        "sort": "cited_by_count:desc",
        "per_page": 50,  # 获取更多候选论文以进行速率排序
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
        
        # Calculate citations per day (citations_per_day = cited_by_count / max(days_since_publication, 1))
        citations_per_day = 0.0
        if pub_date:
            try:
                pub_dt = datetime.strptime(pub_date, "%Y-%m-%d")
                days_since = (datetime.now() - pub_dt).days
                days_since = max(days_since, 1)
                citations_per_day = round(cited_by / days_since, 2)
            except Exception:
                pass

        formatted_papers.append({
            "id": paper.get("id") or "",
            "title": title,
            "authors": authors_str,
            "cited_by_count": cited_by,
            "citations_per_day": citations_per_day,
            "url": paper_url,
            "publication_date": pub_date
        })
        
    return formatted_papers

@router.get("/api/stats/keywords")
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
        
    if not os.path.exists(config.DB_PATH):
        return {"keywords": [], "daily_trends": []}
        
    conn = connect_db(config.DB_PATH)
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
                N = delta_days
                x_mean = N / 2.0
                denominator = sum((i - x_mean) ** 2 for i in range(N + 1))
                
                for kw, entry in keyword_data.items():
                    if entry["count"] < 5:
                        entry["growth_rate"] = 0.0
                        continue
                    
                    y = []
                    for i in range(N + 1):
                        dt = start_dt + timedelta(days=i)
                        dt_str = dt.strftime("%Y-%m-%d")
                        y.append(entry["date_distribution"].get(dt_str, 0))
                    
                    numerator = sum((i - x_mean) * y[i] for i in range(N + 1))
                    entry["growth_rate"] = float(numerator / denominator)
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
        
        # Fetch daily total papers count for rate normalization
        total_papers_map = {}
        if category == 'All':
            total_query = """
            SELECT paper_date, COUNT(DISTINCT paper_id)
            FROM paper_keywords
            WHERE paper_date BETWEEN ? AND ?
              AND language = ?
            GROUP BY paper_date
            """
            total_params = [start_date, end_date, lang]
        else:
            categories = category.split(',')
            placeholders = ','.join(['?'] * len(categories))
            total_query = f"""
            SELECT paper_date, COUNT(DISTINCT paper_id)
            FROM paper_keywords
            WHERE paper_date BETWEEN ? AND ?
              AND language = ?
              AND category IN ({placeholders})
            GROUP BY paper_date
            """
            total_params = [start_date, end_date, lang] + categories
        
        cursor.execute(total_query, total_params)
        for p_date, total in cursor.fetchall():
            total_papers_map[p_date] = total

        # Fetch paper counts containing top 10 keywords on each date
        kw_papers_map = {}
        top_10_keywords = [item["keyword"] for item in keywords_list[:10]]
        if top_10_keywords:
            kw_placeholders = ','.join(['?'] * len(top_10_keywords))
            if category == 'All':
                kw_query = f"""
                SELECT keyword, paper_date, COUNT(DISTINCT paper_id)
                FROM paper_keywords
                WHERE paper_date BETWEEN ? AND ?
                  AND language = ?
                  AND keyword IN ({kw_placeholders})
                GROUP BY keyword, paper_date
                """
                kw_params = [start_date, end_date, lang] + top_10_keywords
            else:
                placeholders = ','.join(['?'] * len(categories))
                kw_query = f"""
                SELECT keyword, paper_date, COUNT(DISTINCT paper_id)
                FROM paper_keywords
                WHERE paper_date BETWEEN ? AND ?
                  AND language = ?
                  AND category IN ({placeholders})
                  AND keyword IN ({kw_placeholders})
                GROUP BY keyword, paper_date
                """
                kw_params = [start_date, end_date, lang] + categories + top_10_keywords
            
            cursor.execute(kw_query, kw_params)
            for kw, p_date, kw_count in cursor.fetchall():
                kw_papers_map[(kw, p_date)] = kw_count

        daily_trends = []
        for kw in top_10_keywords:
            if kw in keyword_data:
                for p_date, count in keyword_data[kw]["date_distribution"].items():
                    total_on_date = total_papers_map.get(p_date, 0)
                    kw_papers_on_date = kw_papers_map.get((kw, p_date), 0)
                    rate = 0.0
                    if total_on_date > 0:
                        rate = round((kw_papers_on_date / total_on_date) * 100, 2)
                    daily_trends.append({
                        "keyword": kw,
                        "date": p_date,
                        "count": count,
                        "rate": rate
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

@router.get("/api/stats/network")
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
        
    if not os.path.exists(config.DB_PATH):
        return {"nodes": [], "links": []}
        
    conn = connect_db(config.DB_PATH)
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

@router.get("/api/stats/journals")
def get_journals(token: str = Depends(verify_token)):
    return [{"name": j["name"], "category": j["category"]} for j in JOURNALS]

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
    
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = connect_db(config.DB_PATH)
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT papers_json FROM hot_papers_cache
        WHERE journal = ? AND period = ? AND query_date = ?
        """, (selected_journal["name"], period, today_str))
        
        row = cursor.fetchone()
        if row:
            papers = json.loads(row[0])
            # Ensure citations_per_day is present for cached papers
            updated = False
            for paper in papers:
                if "citations_per_day" not in paper:
                    pub_date = paper.get("publication_date") or ""
                    cited_by = paper.get("cited_by_count") or 0
                    citations_per_day = 0.0
                    if pub_date:
                        try:
                            pub_dt = datetime.strptime(pub_date, "%Y-%m-%d")
                            days_since = (datetime.now() - pub_dt).days
                            days_since = max(days_since, 1)
                            citations_per_day = round(cited_by / days_since, 2)
                        except Exception:
                            pass
                    paper["citations_per_day"] = citations_per_day
                    updated = True
            return papers
            
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

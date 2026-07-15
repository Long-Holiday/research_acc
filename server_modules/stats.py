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

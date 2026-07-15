import os
import re
import json
from fastapi import APIRouter, Depends, HTTPException
from app.auth import verify_token
from server_modules.database import connect_db
from server_modules.processor import scan_and_process_files
import app.config as config

router = APIRouter()

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

@router.get("/api/papers/range")
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
        
    if not os.path.exists(config.DB_PATH):
        return []
        
    conn = connect_db(config.DB_PATH)
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

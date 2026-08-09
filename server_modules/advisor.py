import os
import re
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel

from app.auth import verify_token
from server_modules.database import connect_db
import app.config as config
from ai.advisor import (
    get_advisor_topic,
    set_advisor_topic,
    generate_advisor_report,
    backfill_historical_reports,
    get_unprocessed_dates,
    parse_ideas_json,
)

router = APIRouter()

class GenerateRequest(BaseModel):
    date: str
    topic: Optional[str] = None
    force: bool = False

class BackfillRequest(BaseModel):
    force: bool = False
    topic: Optional[str] = None

class TopicSettingsRequest(BaseModel):
    topic: str

def _get_db_path():
    return getattr(config, "DB_PATH", "data/statistics.db")

@router.get("/api/advisor/dates")
def get_advisor_dates(token: str = Depends(verify_token)):
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return {"dates": []}

    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT report_date FROM advisor_reports ORDER BY report_date DESC")
        rows = cursor.fetchall()
        dates = [r[0] for r in rows]
        return {"dates": dates}
    except Exception as e:
        return {"dates": []}
    finally:
        conn.close()

@router.get("/api/advisor/report")
def get_advisor_report(date: str, token: str = Depends(verify_token)):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise HTTPException(status_code=400, detail="Invalid date format (expected YYYY-MM-DD)")

    db_path = _get_db_path()
    if not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="Advisor report database not found")

    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT report_date, topic, summary_takeaway, report_markdown, ideas_json, created_at, updated_at
            FROM advisor_reports
            WHERE report_date = ?
        """, (date,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"No advisor report found for date {date}")

        ideas = parse_ideas_json(row[4])
        return {
            "report_date": row[0],
            "topic": row[1],
            "summary_takeaway": row[2],
            "report_markdown": row[3],
            "ideas_json": ideas,
            "created_at": row[5],
            "updated_at": row[6]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query error: {str(e)}")
    finally:
        conn.close()

@router.post("/api/advisor/generate")
def generate_report_endpoint(req: GenerateRequest, background_tasks: BackgroundTasks, token: str = Depends(verify_token)):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", req.date):
        raise HTTPException(status_code=400, detail="Invalid date format (expected YYYY-MM-DD)")

    db_path = _get_db_path()
    try:
        report = generate_advisor_report(
            date_str=req.date,
            topic=req.topic,
            force=req.force,
            backfill=False,
            db_path=db_path
        )
        return {"status": "success", "report": report}
    except FileNotFoundError as fnf:
        raise HTTPException(status_code=404, detail=str(fnf))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate advisor report: {str(e)}")

@router.post("/api/advisor/backfill")
def backfill_reports_endpoint(req: BackfillRequest, background_tasks: BackgroundTasks, token: str = Depends(verify_token)):
    db_path = _get_db_path()
    
    if not req.force:
        unprocessed = get_unprocessed_dates(db_path=db_path)
        if not unprocessed:
            return {"status": "already_complete", "message": "所有历史数据均已处理完毕"}

    def _run_backfill():
        try:
            backfill_historical_reports(db_path=db_path, topic=req.topic, force=req.force)
        except Exception as e:
            print(f"Background backfill error: {e}")

    background_tasks.add_task(_run_backfill)
    return {"status": "processing", "message": "Historical backfill task started in background."}

@router.get("/api/advisor/settings")
def get_settings_endpoint(token: str = Depends(verify_token)):
    db_path = _get_db_path()
    topic = get_advisor_topic(db_path)
    return {"topic": topic}

@router.post("/api/advisor/settings")
def save_settings_endpoint(req: TopicSettingsRequest, token: str = Depends(verify_token)):
    if not req.topic.strip():
        raise HTTPException(status_code=400, detail="Topic cannot be empty")
    db_path = _get_db_path()
    topic = set_advisor_topic(req.topic.strip(), db_path)
    return {"status": "success", "topic": topic}

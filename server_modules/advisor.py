import os
import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import verify_token
from server_modules.database import connect_db
import app.config as config
from ai.advisor import (
    get_advisor_topic,
    set_advisor_topic,
    parse_ideas_json,
)

router = APIRouter()

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

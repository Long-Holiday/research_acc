import os
import sqlite3
import pytest
from server_modules.database import connect_db
from server_modules.processor import scan_and_process_files

TEST_DB_PATH = "data/test_advisor_db.db"

def _cleanup():
    for ext in ["", "-wal", "-shm"]:
        p = TEST_DB_PATH + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch):
    import server_modules.processor as processor
    os.makedirs("data", exist_ok=True)
    _cleanup()
    monkeypatch.setattr(processor, "DB_PATH", TEST_DB_PATH)
    processor.cache_initialized = False
    processor.processed_files_cache.clear()
    yield
    _cleanup()

def test_advisor_tables_created_on_scan():
    scan_and_process_files()
    conn = connect_db(TEST_DB_PATH)
    cursor = conn.cursor()
    
    # Check advisor_reports table
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='advisor_reports'")
    assert cursor.fetchone() is not None, "advisor_reports table was not created"
    
    # Check advisor_settings table
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='advisor_settings'")
    assert cursor.fetchone() is not None, "advisor_settings table was not created"
    
    # Test inserting and querying advisor report
    cursor.execute("""
        INSERT INTO advisor_reports (report_date, topic, summary_takeaway, report_markdown, ideas_json)
        VALUES (?, ?, ?, ?, ?)
    """, ("2026-08-08", "Remote Sensing", "Takeaway summary", "# Report", "[]"))
    conn.commit()
    
    cursor.execute("SELECT report_date, topic, summary_takeaway FROM advisor_reports WHERE report_date = ?", ("2026-08-08",))
    row = cursor.fetchone()
    assert row[0] == "2026-08-08"
    assert row[1] == "Remote Sensing"
    assert row[2] == "Takeaway summary"
    conn.close()

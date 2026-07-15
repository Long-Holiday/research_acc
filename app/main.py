import os
import asyncio
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import app.config as config
from server_modules.database import connect_db
from server_modules.processor import scan_and_process_files
from app.auth import router as auth_router, clean_expired_sessions_loop
from server_modules.papers import router as papers_router
from server_modules.stats import router as stats_router

app = FastAPI(title="Daily arXiv AI Enhanced Server")

# Include routers
app.include_router(auth_router)
app.include_router(papers_router)
app.include_router(stats_router)

@app.on_event("startup")
def startup_event():
    try:
        scan_and_process_files()
    except Exception as e:
        print(f"Error during startup scanning: {e}")
        
    try:
        os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
        conn = connect_db(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS hot_papers_cache (
            journal TEXT,
            period INTEGER,
            query_date TEXT,
            papers_json TEXT,
            PRIMARY KEY (journal, period, query_date)
        )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error creating hot_papers_cache table on startup: {e}")
        
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(clean_expired_sessions_loop())
        else:
            asyncio.create_task(clean_expired_sessions_loop())
    except Exception as e:
        print(f"Failed to start background session cleaner: {e}")

# Serve HTML pages directly
@app.get("/")
@app.get("/index.html")
def read_index():
    return FileResponse("index.html")

@app.get("/login.html")
def read_login():
    return FileResponse("login.html")

@app.get("/settings.html")
def read_settings():
    return FileResponse("settings.html")

@app.get("/statistic.html")
def read_statistic():
    return FileResponse("statistic.html")

# Mount static folders if they exist
for folder in ["js", "css", "assets", "images"]:
    if os.path.exists(folder):
        app.mount(f"/{folder}", StaticFiles(directory=folder), name=folder)

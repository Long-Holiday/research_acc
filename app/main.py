import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware

import app.config as config
from server_modules.database import connect_db
from server_modules.processor import scan_and_process_files
from app.auth import router as auth_router, clean_expired_sessions_loop
from server_modules.papers import router as papers_router
from server_modules.stats import router as stats_router
from server_modules.advisor import router as advisor_router

# 限制底层科学计算库单线程，防止多核抢占导致 CPU 100% 假死
for thread_env in ["OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"]:
    os.environ.setdefault(thread_env, "1")

_periodic_scan_lock = asyncio.Lock()

async def periodic_file_scan():
    while True:
        try:
            # 5分钟扫描一次新文件，使用锁避免任务重叠堆积
            if not _periodic_scan_lock.locked():
                async with _periodic_scan_lock:
                    await asyncio.to_thread(scan_and_process_files)
        except Exception as e:
            print(f"Error during background periodic scanning: {e}")
        await asyncio.sleep(300)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时执行一次同步扫描
    try:
        await asyncio.to_thread(scan_and_process_files)
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
        
    # 启动后台定时任务
    session_task = asyncio.create_task(clean_expired_sessions_loop())
    scan_task = asyncio.create_task(periodic_file_scan())
    
    yield
    
    # 停止后台任务
    session_task.cancel()
    scan_task.cancel()

app = FastAPI(title="Make RS Great Again Server", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Include routers
app.include_router(auth_router)
app.include_router(papers_router)
app.include_router(stats_router)
app.include_router(advisor_router)

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

@app.get("/advisor.html")
def read_advisor():
    return FileResponse("advisor.html")

# Mount static folders if they exist
for folder in ["js", "css", "assets", "images"]:
    if os.path.exists(folder):
        app.mount(f"/{folder}", StaticFiles(directory=folder), name=folder)

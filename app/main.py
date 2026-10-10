import os
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware

# 必须在导入业务模块之前限制科学计算库线程数。
for thread_env in ["OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"]:
    os.environ.setdefault(thread_env, "1")

import app.config as config
from server_modules.database import connect_db
from app.auth import router as auth_router, clean_expired_sessions_loop
from server_modules.papers import router as papers_router
from server_modules.stats import router as stats_router
from server_modules.advisor import router as advisor_router


def local_now():
    return datetime.now()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 数据入库由每日流水线负责，Web 启动不扫描或提取关键词。
    try:
        os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
        conn = connect_db(config.DB_PATH)
        try:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS hot_papers_cache (
                journal TEXT,
                period INTEGER,
                query_date TEXT,
                papers_json TEXT,
                PRIMARY KEY (journal, period, query_date)
            )
            """)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"Error creating hot_papers_cache table on startup: {e}")

    session_task = asyncio.create_task(clean_expired_sessions_loop())
    try:
        yield
    finally:
        session_task.cancel()
        try:
            await session_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Make RS Great Again Server", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def maintenance_window(request: Request, call_next):
    now = local_now()
    if now.hour == 4:
        end = now.replace(hour=5, minute=0, second=0, microsecond=0)
        headers = {
            "Retry-After": str(max(1, int((end - now).total_seconds()))),
            "Cache-Control": "no-store",
        }
        if request.url.path.startswith("/api/") or request.url.path == "/api":
            return JSONResponse(
                {"detail": "论文正在更新，每日 04:00–05:00 暂停服务，请稍后再试。"},
                status_code=503, headers=headers,
            )
        return FileResponse("maintenance.html", status_code=503, headers=headers)
    return await call_next(request)


app.include_router(auth_router)
app.include_router(papers_router)
app.include_router(stats_router)
app.include_router(advisor_router)


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


for folder in ["js", "css", "assets", "images"]:
    if os.path.exists(folder):
        app.mount(f"/{folder}", StaticFiles(directory=folder), name=folder)

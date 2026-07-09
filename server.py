import os
import uuid
import time
import json
from fastapi import FastAPI, Depends, HTTPException, Header, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

ACCESS_PASSWORD = os.getenv("ACCESS_PASSWORD", "")
active_sessions = {}  # token -> expiry_timestamp

class LoginRequest(BaseModel):
    password: str

def verify_token(authorization: str = Header(None)):
    if not ACCESS_PASSWORD:
        return "anonymous"
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid token")
    token = authorization.split(" ")[1]
    expiry = active_sessions.get(token)
    if not expiry or time.time() > expiry:
        if token in active_sessions:
            active_sessions.pop(token)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired or invalid")
    return token

app = FastAPI(title="Daily arXiv AI Enhanced Server")

@app.post("/api/auth/login")
def login(req: LoginRequest):
    if not ACCESS_PASSWORD:
        return {"status": "success", "token": "anonymous_token", "expire": 0}
    if req.password == ACCESS_PASSWORD:
        token = str(uuid.uuid4())
        expire_at = time.time() + 7 * 24 * 3600
        active_sessions[token] = expire_at
        return {"status": "success", "token": token, "expire": int(expire_at * 1000)}
    raise HTTPException(status_code=401, detail="Invalid password")

@app.post("/api/auth/check")
def check_auth(token: str = Depends(verify_token)):
    return {"authenticated": True}

@app.get("/api/dates")
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

@app.get("/api/papers")
def get_papers(date: str, lang: str, token: str = Depends(verify_token)):
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

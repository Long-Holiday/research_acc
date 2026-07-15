import uuid
import time
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel
import app.config as config

router = APIRouter()

class LoginRequest(BaseModel):
    password: str

def verify_token(authorization: str = Header(None)):
    if not config.ACCESS_PASSWORD:
        return "anonymous"
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid token")
    token = authorization.split(" ")[1]
    expiry = config.active_sessions.get(token)
    if not expiry or time.time() > expiry:
        config.active_sessions.pop(token, None)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired or invalid")
    return token

@router.post("/api/auth/login")
def login(req: LoginRequest):
    if not config.ACCESS_PASSWORD:
        return {"status": "success", "token": "anonymous_token", "expire": 0}
    if req.password == config.ACCESS_PASSWORD:
        token = str(uuid.uuid4())
        expire_at = time.time() + 7 * 24 * 3600
        config.active_sessions[token] = expire_at
        return {"status": "success", "token": token, "expire": int(expire_at * 1000)}
    raise HTTPException(status_code=401, detail="Invalid password")

@router.post("/api/auth/check")
def check_auth(token: str = Depends(verify_token)):
    return {"authenticated": True}

async def clean_expired_sessions_loop():
    while True:
        try:
            now = time.time()
            expired = [t for t, exp in config.active_sessions.items() if now > exp]
            for t in expired:
                config.active_sessions.pop(t, None)
        except Exception as e:
            print(f"Error cleaning sessions: {e}")
        await asyncio.sleep(3600)  # Clean every hour

import os
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Daily arXiv AI Enhanced Server")

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

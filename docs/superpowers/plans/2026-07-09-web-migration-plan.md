# Web Server Migration Refactoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the project from a GitHub Pages static website to a self-hosted Web application architecture using FastAPI and Vanilla JS, removing redundant build and deployment code.

**Architecture:** A FastAPI backend server handles user authentication, dynamically scans paper dates, converts raw JSONL data files to JSON arrays, and hosts the frontend SPA files. The frontend consumes REST APIs, uses session tokens stored in LocalStorage, and cleans up local JSONL parsing and hashing routines.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, pytest, Vanilla HTML/CSS/JS.

## Global Constraints
* Keep lines short and concise.
* Keep python code compatible with version 3.12.
* Always preserve existing comments and docstrings in files that are modified.
* Do not expose secrets or passwords in the code or git commits.

---

### Task 1: Dependency Update and Env Template

**Files:**
* Modify: `pyproject.toml`
* Create: `.env.example`

**Interfaces:**
* Produces: Python virtual environment containing `fastapi`, `uvicorn`, and `pytest`.

- [ ] **Step 1: Write pyproject.toml changes**
  Modify dependencies section of `pyproject.toml` to add `fastapi`, `uvicorn`, and `python-dotenv`.
  ```toml
  dependencies = [
      "arxiv>=2.1.3",
      "dotenv>=0.9.9",
      "langchain>=0.3.20",
      "langchain-openai>=0.3.9",
      "scrapy>=2.12.0",
      "tqdm>=4.67.1",
      "fastapi>=0.110.0",
      "uvicorn>=0.28.0",
      "python-dotenv>=1.0.1",
  ]
  ```

- [ ] **Step 2: Create `.env.example`**
  ```ini
  ACCESS_PASSWORD=admin_secret_password
  OPENAI_API_KEY=your_openai_key_here
  OPENAI_BASE_URL=https://api.openai.com/v1
  CATEGORIES=cs.CV,cs.CL
  LANGUAGE=Chinese
  MODEL_NAME=gpt-4o-mini
  HOST=0.0.0.0
  PORT=8000
  ```

- [ ] **Step 3: Run uv sync to update environment**
  Verify that `uv sync` installs the packages successfully.
  Run: `uv sync`
  Expected: Successful exit and generation/update of lockfile.

- [ ] **Step 4: Commit dependencies**
  ```bash
  git add pyproject.toml .env.example uv.lock
  git commit -m "chore: add fastapi, uvicorn dependencies and env template"
  ```

---

### Task 2: FastAPI Server Scaffolding & Page Routing

**Files:**
* Create: `server.py`
* Create: `tests/test_server.py`

**Interfaces:**
* Produces: FastAPI entry point routing `/` and other static assets.

- [ ] **Step 1: Write initial `server.py` with static files routing**
  ```python
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
  ```

- [ ] **Step 2: Create simple unit test `tests/test_server.py`**
  ```python
  from fastapi.testclient import TestClient
  from server import app

  client = TestClient(app)

  def test_index_page():
      response = client.get("/")
      assert response.status_code == 200
      assert "Daily arXiv AI Enhanced" in response.text
  ```

- [ ] **Step 3: Run pytest to verify**
  Run: `pytest tests/test_server.py -v`
  Expected: test_index_page passes.

- [ ] **Step 4: Commit scaffold**
  ```bash
  git add server.py tests/test_server.py
  git commit -m "feat: setup fastapi server scaffolding with static route hosting"
  ```

---

### Task 3: Backend Authentication & Paper REST APIs

**Files:**
* Modify: `server.py`
* Modify: `tests/test_server.py`

**Interfaces:**
* Produces: `/api/auth/login`, `/api/auth/check`, `/api/dates`, `/api/papers` REST endpoints.

- [ ] **Step 1: Write backend logic to `server.py`**
  Add memory sessions, auth security dependencies, date scanner, and paper JSONL reader to `server.py`.
  ```python
  import uuid
  import time
  from fastapi import Depends, HTTPException, Header, status
  from pydantic import BaseModel

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

  import json
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
  ```

- [ ] **Step 2: Add API tests to `tests/test_server.py`**
  ```python
  import tempfile
  import shutil

  def test_auth_and_data_apis():
      # Configure fake password
      import server
      server.ACCESS_PASSWORD = "testpassword"
      
      # Unauthenticated access should fail
      response = client.get("/api/dates")
      assert response.status_code == 401

      # Login with bad password
      response = client.post("/api/auth/login", json={"password": "bad"})
      assert response.status_code == 401

      # Login with good password
      response = client.post("/api/auth/login", json={"password": "testpassword"})
      assert response.status_code == 200
      token = response.json()["token"]
      assert token is not None

      headers = {"Authorization": f"Bearer {token}"}
      
      # Check auth
      response = client.post("/api/auth/check", headers=headers)
      assert response.status_code == 200
      assert response.json()["authenticated"] is True

      # Setup temp data folder for testing
      os.makedirs("data", exist_ok=True)
      test_file = "data/2026-07-09_AI_enhanced_Chinese.jsonl"
      with open(test_file, "w") as f:
          f.write(json.dumps({"title": "Test Paper", "authors": ["Author 1"], "categories": ["cs.CV"], "AI": {"tldr": "Tldr"}}) + "\n")
      
      try:
          # Get dates
          response = client.get("/api/dates", headers=headers)
          assert response.status_code == 200
          assert "2026-07-09" in response.json()["dates"]

          # Get papers
          response = client.get("/api/papers?date=2026-07-09&lang=Chinese", headers=headers)
          assert response.status_code == 200
          assert len(response.json()) == 1
          assert response.json()[0]["title"] == "Test Paper"
      finally:
          if os.path.exists(test_file):
              os.remove(test_file)
          # Restore ACCESS_PASSWORD
          server.ACCESS_PASSWORD = ""
  ```

- [ ] **Step 3: Run pytest**
  Run: `pytest tests/test_server.py -v`
  Expected: All tests pass.

- [ ] **Step 4: Commit APIs**
  ```bash
  git add server.py tests/test_server.py
  git commit -m "feat: implement login, date list, and paper data APIs"
  ```

---

### Task 4: Frontend Authentication Integration

**Files:**
* Modify: `js/auth.js`
* Modify: `login.html`

**Interfaces:**
* Consumes: `/api/auth/login`, `/api/auth/check`
* Produces: `fetchWithAuth(url, options)` global function and redirect handling.

- [ ] **Step 1: Simplify and update `js/auth.js`**
  Modify `js/auth.js` to strip SHA-256 fallback routines and query backend authentication.
  ```javascript
  const Auth = {
      // Helper function to call API with Auth token injection & 401 handling
      async fetchWithAuth(url, options = {}) {
          const token = localStorage.getItem('arxiv_auth_token');
          options.headers = options.headers || {};
          if (token) {
              options.headers['Authorization'] = `Bearer ${token}`;
          }
          const response = await fetch(url, options);
          if (response.status === 401) {
              localStorage.removeItem('arxiv_auth_token');
              localStorage.removeItem('arxiv_auth_expire');
              const currentPage = window.location.pathname.split('/').pop() || 'index.html';
              if (currentPage !== 'login.html') {
                  window.location.href = `login.html?redirect=${currentPage}`;
              }
          }
          return response;
      },

      async login(password, remember = true) {
          try {
              const response = await fetch('/api/auth/login', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ password })
              });
              if (!response.ok) {
                  return false;
              }
              const data = await response.json();
              localStorage.setItem('arxiv_auth_token', data.token);
              localStorage.setItem('arxiv_auth_expire', data.expire.toString());
              return true;
          } catch (e) {
              console.error('Login request failed', e);
              return false;
          }
      },

      isAuthenticated() {
          const token = localStorage.getItem('arxiv_auth_token');
          const expireTime = localStorage.getItem('arxiv_auth_expire');

          if (!token || !expireTime) {
              return false;
          }

          const now = Date.now();
          if (now > parseInt(expireTime) && parseInt(expireTime) !== 0) {
              this.logout();
              return false;
          }

          return true;
      },

      logout() {
          localStorage.removeItem('arxiv_auth_token');
          localStorage.removeItem('arxiv_auth_expire');
          window.location.href = 'login.html';
      },

      async isPasswordEnabled() {
          // Send a quick check to see if backend needs auth
          try {
              const response = await fetch('/api/auth/login', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ password: "" })
              });
              return response.status !== 200; // If empty password succeeds, then password is not required
          } catch(e) {
              return true;
          }
      },

      async requireAuth() {
          const enabled = await this.isPasswordEnabled();
          if (!enabled) {
              return;
          }
          if (!this.isAuthenticated()) {
              const currentPage = window.location.pathname.split('/').pop() || 'index.html';
              window.location.href = `login.html?redirect=${currentPage}`;
          }
      }
  };
  ```

- [ ] **Step 2: Update `login.html` scripts**
  Remove script reference to `js/auth-config.js` (around line 178) since authentication is now managed server-side.
  ```html
  -     <script src="js/auth-config.js"></script>
        <script src="js/auth.js"></script>
  ```

- [ ] **Step 3: Verify login redirects manually**
  Verify syntax is valid.
  Run: `python -m py_compile server.py`
  Expected: exit code 0.

- [ ] **Step 4: Commit frontend authentication changes**
  ```bash
  git add js/auth.js login.html
  git commit -m "feat: refactor js/auth.js and login.html to verify authentication on backend"
  ```

---

### Task 5: Frontend Data Loading Integration

**Files:**
* Modify: `js/app.js`
* Modify: `js/data-config.js`
* Modify: `js/statistic.js`
* Modify: `js/settings.js`

**Interfaces:**
* Consumes: `/api/dates`, `/api/papers` via `Auth.fetchWithAuth`

- [ ] **Step 1: Update `js/data-config.js`**
  Simplify API endpoint retrieval logic.
  ```javascript
  const DATA_CONFIG = {
      getDatesUrl: () => '/api/dates',
      getPapersUrl: (date, lang) => `/api/papers?date=${date}&lang=${lang}`
  };
  ```

- [ ] **Step 2: Modify `js/app.js`**
  * Update `fetchAvailableDates()` to request `/api/dates` through `Auth.fetchWithAuth` and decode JSON array.
  * Update `loadPapersByDate()` to request `/api/papers?date=...` through `Auth.fetchWithAuth` and assign the array directly (removing the `parseJsonlData` call).
  * Remove `parseJsonlData(text, date)` function definition from `js/app.js` completely.
  * Replace other plain `fetch` data calls (e.g. settings loading or data file fetches) with `Auth.fetchWithAuth`.

- [ ] **Step 3: Modify `js/statistic.js`**
  * Modify stats data loading fetches to route through `Auth.fetchWithAuth`.
  * Replace the static file loads of JSONL with backend `/api/papers` endpoints in `js/statistic.js`.

- [ ] **Step 4: Modify `js/settings.js`**
  * Route config actions and verification checks through `Auth.fetchWithAuth` where appropriate.

- [ ] **Step 5: Run python compilation check**
  Run: `python -m py_compile server.py`
  Expected: exit code 0.

- [ ] **Step 6: Commit frontend API endpoints**
  ```bash
  git add js/app.js js/data-config.js js/statistic.js js/settings.js
  git commit -m "feat: integrate app data loading with backend JSON APIs, cleaning up client JSONL parsing"
  ```

---

### Task 6: Simplified Crawl Workflow

**Files:**
* Modify: `run.sh`

**Interfaces:**
* Produces: Clean Scrapy crawl, OpenAlex crawl, Dedup check, and AI enhanced `.jsonl` outputs in `data/`.

- [ ] **Step 1: Simplify `run.sh`**
  Remove Jekyll compile markers, Markdown conversion step, and assets file listing (`ls data/*.jsonl ... > assets/file-list.txt`).
  Change steps from line 136 to 166:
  ```bash
  # Step 4 is no longer required (skipped Markdown conversion)
  # Step 5 is no longer required (skipped assets/file-list.txt generation)
  ```
  Ensure `run.sh` only executes:
  1. `scrapy crawl arxiv -o ../data/${today}.jsonl`
  2. `python crawl_openalex.py --date ${today} --output ../data/${today}.jsonl`
  3. `python daily_arxiv/check_stats.py`
  4. `python enhance.py --data ../data/${today}.jsonl` (inside `ai/`)

- [ ] **Step 2: Commit crawl script simplification**
  ```bash
  git add run.sh
  git commit -m "chore: simplify run.sh to only crawl and execute AI enhancement"
  ```

---

### Task 7: Cleanup Obsolete GitHub and Markdown Files

**Files:**
* Delete: `.github/workflows/run.yml`
* Delete: `_config.yml`
* Delete: `setup-local-auth.sh`
* Delete: `update_readme.py`
* Delete: `template.md`
* Delete: `readme_content_template.md`
* Delete: `to_md/convert.py`
* Delete: `to_md/paper_template.md`

- [ ] **Step 1: Remove files from disk and Git**
  Run: `git rm -rf .github/workflows/run.yml _config.yml setup-local-auth.sh update_readme.py template.md readme_content_template.md to_md/`
  Expected: Successful removal.

- [ ] **Step 2: Commit cleanup**
  ```bash
  git commit -m "cleanup: remove redundant GitHub configuration files, static build tools, and Markdown templates"
  ```

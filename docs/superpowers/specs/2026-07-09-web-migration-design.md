# Web Server Migration and Architecture Refactoring Design

This document outlines the design for migrating the `daily-arXiv-ai-enhanced` project from a GitHub Pages static website to a self-hosted, standard Web application architecture using FastAPI and Vanilla JavaScript.

## 1. Directory Structure Changes

### 1.1 Files and Directories to Remove
We will clean up files that are specific to GitHub Actions workflows, static GitHub Pages hosting, Jekyll configurations, and Markdown conversion:
*   `.github/workflows/run.yml` (CI/CD pipelines)
*   `_config.yml` (Jekyll configuration)
*   `setup-local-auth.sh` (Obsolete authentication hash injector script)
*   `js/auth-config.js` (Obsolete static credentials config)
*   `to_md/` directory (Markdown conversion script `convert.py` and its template)
*   `update_readme.py`, `template.md`, `readme_content_template.md` (Readme update tools)

### 1.2 Files to Add or Modify
*   **Create**: `server.py` (FastAPI backend server)
*   **Create**: `.env.example` (Template for server-side environment variables)
*   **Modify**: `pyproject.toml` (Add `fastapi` and `uvicorn` dependencies)
*   **Modify**: `run.sh` (Simplify crawl-and-enhance workflow by removing Markdown generation and file listing)
*   **Modify**: `js/auth.js` (Replace client-side hashing logic with server-side API auth)
*   **Modify**: `js/app.js` (Simplify date/paper loading to consume FastAPI JSON API directly)
*   **Modify**: `js/data-config.js` (Remove raw github URLs, direct to local API root)

---

## 2. Backend Design (`server.py`)

The backend will be built with **FastAPI** and run using **Uvicorn**.

### 2.1 Configuration
Configurations will be loaded from environment variables (optionally via a `.env` file):
*   `ACCESS_PASSWORD`: Clear text password for web portal access. If empty, the system runs in password-less mode.
*   `HOST`: Network interface to bind (default: `0.0.0.0`).
*   `PORT`: Port to listen (default: `8000`).

### 2.2 Memory Session Cache
The backend will manage user session state in-memory:
*   `active_sessions`: A python dictionary `dict[str, float]` mapping `session_token` (UUID) to its expiration timestamp.
*   On server restart, users must re-authenticate. (Appropriate for simple setups; keeps code clean and dependency-free).

### 2.3 Endpoint Design

#### 2.3.1 Security Middleware / Dependency
A dependency function `verify_token` will check the `Authorization` header:
*   Extract `Bearer <token>` from headers.
*   Validate `<token>` against `active_sessions`.
*   If invalid/expired and `ACCESS_PASSWORD` is configured, raise `401 Unauthorized`.
*   If `ACCESS_PASSWORD` is not configured, bypass authentication.

#### 2.3.2 Endpoints

*   **`POST /api/auth/login`**
    *   *Payload*: `{ "password": "..." }`
    *   *Behavior*:
        *   If `ACCESS_PASSWORD` is empty, auto-succeed.
        *   Compare password with `ACCESS_PASSWORD`.
        *   On success, generate UUID `token`, save to `active_sessions` (expiry = `now + 7 days`), return `{ "token": token, "expire": expire_time }`.
        *   On failure, return `401 Unauthorized`.

*   **`POST /api/auth/check`**
    *   *Behavior*: Validates token in header. Returns `{ "authenticated": true/false }`.

*   **`GET /api/dates`** (Protected)
    *   *Behavior*: Scans the `data/` directory for files matching `*_(Chinese|English).jsonl`.
    *   *Logic*: Parse dates and languages from filenames like `YYYY-MM-DD_AI_enhanced_{lang}.jsonl`.
    *   *Response*:
        ```json
        {
          "dates": ["2026-07-09", "2026-07-08"],
          "languages": {
            "2026-07-09": ["Chinese"],
            "2026-07-08": ["Chinese", "English"]
          }
        }
        ```

*   **`GET /api/papers`** (Protected)
    *   *Parameters*: `date: str`, `lang: str`
    *   *Behavior*: Reads `data/{date}_AI_enhanced_{lang}.jsonl`. Parses each line as a JSON object, aggregates them into a list, and returns them as a standard JSON Array.
    *   *Response*:
        ```json
        [
          {
            "id": "...",
            "title": "...",
            "authors": ["..."],
            "AI": {
              "tldr": "...",
              "motivation": "...",
              "method": "...",
              "result": "...",
              "conclusion": "...",
              "remote_sensing_cross": "..."
            }
          }
        ]
        ```

#### 2.4 Static Files and Pages
FastAPI will host the website files:
*   Serve HTML pages directly on root paths:
    *   `/` and `/index.html` $\rightarrow$ `index.html`
    *   `/login.html` $\rightarrow$ `login.html`
    *   `/settings.html` $\rightarrow$ `settings.html`
    *   `/statistic.html` $\rightarrow$ `statistic.html`
*   Mount directories: `/js` $\rightarrow$ `js/`, `/css` $\rightarrow$ `css/`, `/assets` $\rightarrow$ `assets/`, `/images` $\rightarrow$ `images/`.

---

## 3. Frontend Design

### 3.1 Authentication & Interception (`js/auth.js`)
*   **API Interception**: Create `fetchWithAuth(url, options)`:
    *   Injects `Authorization: Bearer <token>` to request headers if token is present in LocalStorage.
    *   Checks response status. If `401 Unauthorized`, clears local storage token/expire values and redirects to `login.html`.
*   **Login Logic**:
    *   Replace `Auth.login` with a `POST` request to `/api/auth/login`.
    *   Store resulting token and expire time in LocalStorage.
*   **Remove Code**:
    *   Remove `sha256Fallback` and `hashPassword` methods.
    *   Remove dependency on `js/auth-config.js`.

### 3.2 Data Integration (`js/app.js` & `js/statistic.js`)
*   **Fetch Dates**:
    *   Request `fetchWithAuth('/api/dates')` instead of reading `assets/file-list.txt`.
    *   Construct `window.dateLanguageMap` directly from the `languages` map object returned by the API.
*   **Fetch Papers**:
    *   Request `fetchWithAuth('/api/papers?date=YYYY-MM-DD&lang=xxx')` instead of reading the raw JSONL static file.
    *   Assign response array directly to `paperData`. **Delete `parseJsonlData` from JS files** since JSON parsing is handled natively by the browser on the API JSON response.

### 3.3 Configuration Integration (`js/data-config.js`)
*   Simplify `DATA_CONFIG` to return relative API endpoints:
    ```javascript
    const DATA_CONFIG = {
        getDatesUrl: () => '/api/dates',
        getPapersUrl: (date, lang) => `/api/papers?date=${date}&lang=${lang}`
    };
    ```

---

## 4. Crawl Workflow Design (`run.sh`)

Simplify the `./run.sh` script to perform crawler execution and AI processing without the post-processing steps:
1.  **Crawl arXiv**: Scrapy spider output to `data/{date}.jsonl`.
2.  **Crawl OpenAlex**: Append OpenAlex results to `data/{date}.jsonl`.
3.  **Check Dedup**: Execute `check_stats.py` to dedup.
4.  **AI Enhancement**: Run `enhance.py` to produce `data/{date}_AI_enhanced_{lang}.jsonl`.
5.  *Skip Markdown conversion* (`to_md/convert.py`) and *skip file listing* (`ls data/*.jsonl ... > assets/file-list.txt`).

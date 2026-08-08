# AI Academic Advisor Feature Implementation Plan (v1.1.0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement an end-to-end "AI Academic Advisor" system that performs multi-stage LLM dialogue analysis on raw daily paper metadata, tracks 7-day/30-day temporal research evolution, generates 3 actionable research plans with experiment designs for remote sensing & computer vision, supports automatic chronological backfill for any unprocessed past data, and delivers a minimalist frontend interface (`advisor.html`) integrated with `run.sh`.

**Architecture:** 
- **Multi-Stage Dialogue Engine:** `ai/advisor.py` decomposes the LLM analysis into two focused turns:
  1. **Stage 1 (Trend & Temporal Distillation)**: Evaluates today's raw paper abstracts against 7-day & 30-day temporal summaries, produces core insights and a 150-word takeaway for SQLite caching.
  2. **Stage 2 (Ideation & Experiment Design)**: Takes Stage 1's trend conclusions and top representative papers to formulate 3 reviewer-proof research proposals with experimental designs.
- **Historical Backlog Auto-Backfill:** Automatically detects any unprocessed historical date in `data/*.jsonl` and processes them in chronological order (oldest to newest) to maintain an unbroken temporal context chain.
- **Backend & Database Layer:** `server_modules/advisor.py` exposes REST APIs (`/api/advisor/dates`, `/api/advisor/report`, `/api/advisor/generate`, `/api/advisor/backfill`, `/api/advisor/settings`) connected to SQLite (`advisor_reports` and `advisor_settings` in `data/statistics.db`).
- **Automation Layer:** Step 4 added to `run.sh` with `--backfill` support for seamless crawling-to-report generation.
- **Frontend Layer:** `advisor.html`, `css/advisor.css`, `js/advisor.js` with responsive cards for daily briefing, temporal evolution, and 3 tabbed research ideas with one-click clipboard copying.

**Tech Stack:** Python 3.10+, FastAPI, SQLite (WAL mode, SQLitePool), LangChain (ChatOpenAI / ChatGoogleGenerativeAI), HTML5 / Vanilla CSS / Vanilla JS (Flatpickr, Marked.js).

---

## Global Constraints

- **No post-processing reference:** Analysis must strictly derive from raw paper metadata (`Title`, `Authors`, `Categories`, `Abstract`), without relying on keyword frequency stats or clustering.
- **Context & Attention Management:** Split into 2 focused dialogue stages to prevent prompt attention dilution and output token truncation.
- **Historical Backfill Ordering:** Historical dates must be processed chronologically ascending (oldest first) so each day builds upon past temporal summaries.
- **Authentication & Security:** All `/api/advisor/*` endpoints must enforce Bearer token authentication consistent with `app/auth.py`.
- **Database Concurrency:** All SQLite access must use `server_modules.database.connect_db` with `db_lock` or pooled connections to prevent database lock contention.
- **UI Minimalism & Consistency:** Maintain the existing dark/light theme, Inter typography, glassmorphism card design, and responsive layout.

---

### Task 1: Database Schema for Advisor Reports and Settings

**Files:**
- Modify: `server_modules/processor.py`
- Test: `tests/test_advisor_db.py`

**Interfaces:**
- Consumes: `server_modules.database.connect_db`
- Produces: SQLite tables `advisor_reports` (`report_date`, `topic`, `summary_takeaway`, `report_markdown`, `ideas_json`, `created_at`, `updated_at`) and `advisor_settings` (`key`, `value`, `updated_at`).

- [ ] **Step 1: Write the failing database tests**

Create `tests/test_advisor_db.py`:
```python
import os
import sqlite3
import pytest
from server_modules.database import connect_db
from server_modules.processor import scan_and_process_files

TEST_DB_PATH = "data/test_advisor_db.db"

@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch):
    import server_modules.processor as processor
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    monkeypatch.setattr(processor, "DB_PATH", TEST_DB_PATH)
    yield
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_advisor_db.py -v`
Expected: FAIL with assertion error that `advisor_reports` table does not exist.

- [ ] **Step 3: Update `server_modules/processor.py` to create the advisor tables**

In `server_modules/processor.py`, add schema creation for `advisor_reports` and `advisor_settings` inside `scan_and_process_files()`:
```python
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS advisor_reports (
                report_date TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                summary_takeaway TEXT,
                report_markdown TEXT NOT NULL,
                ideas_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS advisor_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_advisor_reports_date ON advisor_reports (report_date)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_advisor_db.py -v`
Expected: PASS

- [ ] **Step 5: Commit changes**

```bash
git add server_modules/processor.py tests/test_advisor_db.py
git commit -m "feat(advisor): add advisor_reports and advisor_settings database tables"
```

---

### Task 2: Multi-Stage AI Advisor Engine & Backlog Processor (`ai/advisor.py`)

**Files:**
- Create: `ai/advisor.py`
- Test: `tests/test_advisor_engine.py`

**Interfaces:**
- Consumes: Raw JSONL data files (`data/{date}.jsonl` or `data/{date}_AI_enhanced_*.jsonl`), SQLite `advisor_reports` table via `server_modules.database.connect_db`, LLM client configuration (`MODEL_NAME`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `GOOGLE_API_KEY`).
- Produces: 
  - `generate_stage1_trend_analysis(date_str, topic, papers_text, week_context, month_context, llm) -> Tuple[str, str, str]` (Part 1, Part 2 markdown and summary_takeaway)
  - `generate_stage2_ideas(date_str, topic, stage1_analysis, representative_papers, llm) -> Tuple[str, List[Dict]]` (Part 3 markdown and structured ideas)
  - `get_unprocessed_dates(data_dir, db_path) -> List[str]` (sorted ascending)
  - `backfill_historical_reports(data_dir, db_path, topic, force) -> List[str]`
  - `generate_advisor_report(date: str, topic: Optional[str] = None, force: bool = False, backfill: bool = False, db_path: str = "data/statistics.db") -> Dict`
  - CLI entry point: `python ai/advisor.py [--date YYYY-MM-DD] [--backfill] [--force]`

- [ ] **Step 1: Write unit tests for AI Advisor multi-stage engine & backfill**

Create `tests/test_advisor_engine.py`:
```python
import os
import json
import sqlite3
import pytest
from unittest.mock import MagicMock, patch
from ai.advisor import (
    load_raw_papers_compact,
    fetch_temporal_context,
    parse_stage1_output,
    parse_stage2_output,
    get_unprocessed_dates,
    generate_advisor_report,
    DEFAULT_TOPIC
)
from server_modules.database import connect_db

TEST_DB = "data/test_advisor_engine.db"

@pytest.fixture(autouse=True)
def setup_env():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    
    # Initialize test DB schema
    conn = connect_db(TEST_DB)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS advisor_reports (
        report_date TEXT PRIMARY KEY,
        topic TEXT NOT NULL,
        summary_takeaway TEXT,
        report_markdown TEXT NOT NULL,
        ideas_json TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS advisor_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()

    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

def test_load_raw_papers_compact(tmp_path):
    jsonl_file = tmp_path / "2026-08-08.jsonl"
    sample_papers = [
        {
            "id": f"paper_{i}",
            "title": f"Paper Title {i}",
            "authors": ["Author A", "Author B"],
            "categories": ["cs.CV", "eess.IV"],
            "summary": f"This is abstract of paper {i} discussing remote sensing object detection."
        }
        for i in range(5)
    ]
    with open(jsonl_file, "w", encoding="utf-8") as f:
        for p in sample_papers:
            f.write(json.dumps(p) + "\n")

    papers_text = load_raw_papers_compact(str(jsonl_file), max_papers=10)
    assert "Paper Title 0" in papers_text
    assert "Paper Title 4" in papers_text
    assert "remote sensing object detection" in papers_text

def test_fetch_temporal_context_fallback():
    week_context, month_context = fetch_temporal_context("2026-08-08", db_path=TEST_DB)
    assert "暂无过去7天" in week_context
    assert "暂无过去30天" in month_context

def test_fetch_temporal_context_with_history():
    conn = connect_db(TEST_DB)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO advisor_reports (report_date, topic, summary_takeaway, report_markdown, ideas_json)
        VALUES (?, ?, ?, ?, ?)
    """, ("2026-08-07", DEFAULT_TOPIC, "8月7日热点：旋转目标检测与扩散模型数据增强。", "# Markdown", "[]"))
    conn.commit()
    conn.close()

    week_context, month_context = fetch_temporal_context("2026-08-08", db_path=TEST_DB)
    assert "2026-08-07" in week_context
    assert "旋转目标检测" in week_context

def test_parse_stage1_output():
    stage1_text = """
# 今日遥感智能解译前沿与学术导师研判 (2026-08-08)

## 1. 今日前沿速递与导师研判
- **核心技术演进**：今日在弱监督解译与物理先验方向有显著突破...
- **重点论文深度点评**：论文 [1] 提出的自适应感受野机制极具启发性...

## 2. 时序演进对比（7天/30天趋势）
- **7天技术演变观察**：旋转框检测持续升温...
- **30天宏观脉络与顶会审稿偏好**：更偏好轻量化与物理先验结合。

## 核心精炼摘要
今日热点聚焦于物理先验引导的遥感目标检测与弱监督高光谱分割，时序上演进至跨模态轻量化融合。
"""
    part1_2, takeaway = parse_stage1_output(stage1_text)
    assert "## 1. 今日前沿速递与导师研判" in part1_2
    assert "## 2. 时序演进对比" in part1_2
    assert "物理先验引导" in takeaway

def test_parse_stage2_output_structured():
    stage2_text = """
## 3. 3篇落地科研思路与实验设计

### 思路1【顶会理论/架构创新型】
- **【选题名称】**: 基于物理先验引导的遥感跨尺度旋转目标检测网络 (Physics-Guided Cross-Scale Oriented Detector)
- **【研究痛点与动机】**: 复杂背景下小目标易漏检。
- **【核心方法设计】**: 引入物理光学成像先验与动态可变形自注意力。
- **【推荐公开数据集与Baseline】**: DOTA-v2.0, FAIR1M; 基线: RoI Transformer, Oriented R-CNN.
- **【实验验证与消融方案】**: 验证 mAP 提升及消融物理先验模块。
- **【审稿人潜在质疑点与防守策略】**: 质疑计算复杂度，防守方案为 FLOPs 约束分析。

### 思路2【高价值痛点/任务落地型】
- **【选题名称】**: 极少样本弱监督高光谱语义分割 (Few-Shot Weakly Supervised Hyperspectral Segmentation)
- **【研究痛点与动机】**: 高光谱标注成本极高。
- **【核心方法设计】**: 双向原型对齐与跨波段特征解耦。
- **【推荐公开数据集与Baseline】**: Houston 2018, Pavia University; 基线: DeepLabV3+, FreeSolar.
- **【实验验证与消融方案】**: 1-shot/5-shot 下 OA, AA, Kappa 指标对比。
- **【审稿人潜在质疑点与防守策略】**: 质疑波段泛化性，提供多传感器泛化消融。

### 思路3【多模态/大模型跨界融合型】
- **【选题名称】**: 遥感视觉语言大模型交互式指令分割 (RS-LLaVA: Interactive Instruction Segmentation)
- **【研究痛点与动机】**: 遥感领域缺乏细粒度文本引导的目标解译。
- **【核心方法设计】**: 构建遥感指令微调数据集并微调轻量多模态投影层。
- **【推荐公开数据集与Baseline】**: RSVG, GeoChat; 基线: LLaVA-1.5, Shikra.
- **【实验验证与消融方案】**: Zero-shot 跨域分割精度。
- **【审稿人潜在质疑点与防守策略】**: 质疑数据偏见，加入跨传感器鲁棒性评测。
"""
    ideas = parse_stage2_output(stage2_text)
    assert len(ideas) == 3
    assert ideas[0]["type"] == "顶会理论/架构创新型"
    assert "Physics-Guided" in ideas[0]["title"]
    assert ideas[1]["type"] == "高价值痛点/任务落地型"
    assert ideas[2]["type"] == "多模态/大模型跨界融合型"

def test_get_unprocessed_dates(tmp_path):
    # Create mock data files
    (tmp_path / "2026-07-10.jsonl").write_text("{}\n")
    (tmp_path / "2026-07-11_AI_enhanced_Chinese.jsonl").write_text("{}\n")
    (tmp_path / "2026-07-12.jsonl").write_text("{}\n")
    
    # Mark 2026-07-10 as already processed in DB
    conn = connect_db(TEST_DB)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO advisor_reports (report_date, topic, report_markdown) VALUES (?, ?, ?)",
                   ("2026-07-10", DEFAULT_TOPIC, "# Markdown"))
    conn.commit()
    conn.close()

    unprocessed = get_unprocessed_dates(data_dir=str(tmp_path), db_path=TEST_DB)
    assert unprocessed == ["2026-07-11", "2026-07-12"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_advisor_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai.advisor'`.

- [ ] **Step 3: Implement `ai/advisor.py`**

Implement `/home/default_user/research_acc/ai/advisor.py` with multi-stage dialogue prompts (Stage 1 & Stage 2), LLM execution, stage merging, and chronological backfill logic.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_advisor_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit changes**

```bash
git add ai/advisor.py tests/test_advisor_engine.py
git commit -m "feat(advisor): implement multi-stage dialogue advisor engine and chronological backfill"
```

---

### Task 3: Backend API Router for Advisor (`server_modules/advisor.py`)

**Files:**
- Create: `server_modules/advisor.py`
- Modify: `app/main.py`
- Test: `tests/test_advisor_api.py`

**Interfaces:**
- Consumes: `app.auth.verify_token`, `ai.advisor.generate_advisor_report`, `ai.advisor.backfill_historical_reports`, `ai.advisor.get_advisor_topic`, `server_modules.database.connect_db`.
- Produces: Router endpoints:
  - `GET /api/advisor/dates`
  - `GET /api/advisor/report`
  - `POST /api/advisor/generate`
  - `POST /api/advisor/backfill`
  - `GET /api/advisor/settings`
  - `POST /api/advisor/settings`

- [ ] **Step 1: Write API tests**

Create `tests/test_advisor_api.py`:
```python
import os
import json
import pytest
from fastapi.testclient import TestClient
from server import app
import server
import server_modules.processor as processor

client = TestClient(app)
TEST_DB_PATH = "data/test_advisor_api.db"

@pytest.fixture(autouse=True)
def setup_api_env(monkeypatch):
    server.ACCESS_PASSWORD = "testpassword"
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
        
    monkeypatch.setattr(server, "DB_PATH", TEST_DB_PATH)
    monkeypatch.setattr(processor, "DB_PATH", TEST_DB_PATH)
    
    # Run schema creation
    processor.scan_and_process_files()

    yield
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)

def get_auth_header():
    resp = client.post("/api/auth/login", json={"password": "testpassword"})
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}

def test_advisor_dates_empty():
    headers = get_auth_header()
    resp = client.get("/api/advisor/dates", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["dates"] == []

def test_advisor_report_not_found():
    headers = get_auth_header()
    resp = client.get("/api/advisor/report?date=2026-08-08", headers=headers)
    assert resp.status_code == 404

def test_advisor_settings_get_and_post():
    headers = get_auth_header()
    resp = client.get("/api/advisor/settings", headers=headers)
    assert resp.status_code == 200
    assert "topic" in resp.json()

    # Update topic
    new_topic = "高光谱遥感弱监督分类"
    resp = client.post("/api/advisor/settings", json={"topic": new_topic}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["topic"] == new_topic

    # Re-fetch
    resp = client.get("/api/advisor/settings", headers=headers)
    assert resp.json()["topic"] == new_topic
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_advisor_api.py -v`
Expected: FAIL with 404 for `/api/advisor/*` endpoints.

- [ ] **Step 3: Implement `server_modules/advisor.py`**

Create `/home/default_user/research_acc/server_modules/advisor.py` with all 6 endpoints (`dates`, `report`, `generate`, `backfill`, `settings` GET & POST) and background task handling.

- [ ] **Step 4: Register `advisor_router` in `app/main.py` and serve `advisor.html`**

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_advisor_api.py -v`
Expected: PASS

- [ ] **Step 6: Commit changes**

```bash
git add server_modules/advisor.py app/main.py tests/test_advisor_api.py
git commit -m "feat(advisor): add FastAPI advisor router with backfill and advisor.html static route"
```

---

### Task 4: Pipeline Automation Integration (`run.sh`)

**Files:**
- Modify: `run.sh:133-160`
- Test: `tests/test_advisor_pipeline.py`

- [ ] **Step 1: Write pipeline test**

Create `tests/test_advisor_pipeline.py`:
```python
import subprocess
import os
import pytest

def test_advisor_cli_help():
    result = subprocess.run(["python", "ai/advisor.py", "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "--date" in result.stdout
    assert "--backfill" in result.stdout
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_advisor_pipeline.py -v`
Expected: PASS

- [ ] **Step 3: Update `run.sh` to include Step 4 with `--backfill`**

In `run.sh`, after Step 3 (AI enhancement), add Step 4:
```bash
# 第四步：AI 学术导师前沿研报生成与历史补漏 / Step 4: AI Academic Advisor Report Generation & Backfill
if [ "$PARTIAL_MODE" = "false" ]; then
    echo "步骤4：生成学术导师前沿分析与科研思路研报（含历史补漏）... / Step 4: Generating Academic Advisor Report..."
    python ai/advisor.py --date "${today}" --backfill
    
    if [ $? -ne 0 ]; then
        echo "⚠️ 学术导师研报生成跳过或遇到警告，不影响主爬取数据"
    else
        echo "✅ 学术导师研报生成完成 / Academic Advisor report generated successfully"
    fi
else
    echo "⏭️  跳过学术导师研报生成（部分模式）/ Skipping Academic Advisor report (partial mode)"
fi
```

- [ ] **Step 4: Commit changes**

```bash
git add run.sh tests/test_advisor_pipeline.py
git commit -m "feat(advisor): integrate advisor report generation and backfill into run.sh step 4"
```

---

### Task 5: Minimalist Frontend (`advisor.html`, `css/advisor.css`, `js/advisor.js`)

**Files:**
- Create: `advisor.html`
- Create: `css/advisor.css`
- Create: `js/advisor.js`
- Modify: `index.html`, `statistic.html` (add navigation icon for Academic Advisor `🎓`)
- Test: `tests/test_server.py`

- [ ] **Step 1: Write frontend route verification test**

In `tests/test_server.py`, add:
```python
def test_advisor_page():
    response = client.get("/advisor.html")
    assert response.status_code == 200
    assert "学术导师" in response.text or "Academic Advisor" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_server.py::test_advisor_page -v`
Expected: FAIL.

- [ ] **Step 3: Create `advisor.html`**

Create `advisor.html` with:
- Top navigation with Back button, Date selector (Flatpickr), Topic settings modal button, Backfill button, Regenerate button, Logout button.
- Card 1: Today's Frontier & Advisor Insights (Markdown rendered via marked.js).
- Card 2: 7-Day & 30-Day Temporal Evolution.
- Card 3: 3 Actionable Research Ideas with Tab Switcher and "📋 复制本篇实验设计" (Copy Experiment Plan) buttons.
- Topic settings modal.

- [ ] **Step 4: Create `css/advisor.css`**

Create `css/advisor.css` maintaining system theme variables, card elevation, clean badges, tab animations, markdown typography, and copy toast feedback.

- [ ] **Step 5: Create `js/advisor.js`**

Create `js/advisor.js`:
- Auth check on load via `Auth.requireAuth()`.
- Load available dates and current date report.
- Markdown rendering with `marked.parse()`.
- Tab switching logic for the 3 research ideas.
- Clipboard copy with toast notification.
- Topic settings dialog with live save and refresh.
- Backfill action with progress indication.
- Loading spinner and error handling states.

- [ ] **Step 6: Update navigation headers in `index.html` and `statistic.html`**

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_server.py -v`
Expected: PASS

- [ ] **Step 8: Commit changes**

```bash
git add advisor.html css/advisor.css js/advisor.js index.html statistic.html tests/test_server.py
git commit -m "feat(advisor): create minimalist frontend advisor.html, CSS, JS and nav links"
```

---

### Task 6: Full System Integration & Regression Verification

**Files:**
- Test: `tests/`

- [ ] **Step 1: Run all test suites across the repository**

Run: `pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 2: Test CLI end-to-end report generation and backfill verification**

Run: `python ai/advisor.py --help`
Run: `python ai/advisor.py --date 2026-07-10 --force`

- [ ] **Step 3: Commit final integration verification**

```bash
git commit -m "chore(advisor): complete end-to-end integration and verification"
```

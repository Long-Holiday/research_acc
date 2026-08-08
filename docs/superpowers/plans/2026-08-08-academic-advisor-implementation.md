# AI Academic Advisor Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement an end-to-end "AI Academic Advisor" system that analyzes raw daily paper metadata, tracks 7-day/30-day temporal research evolution, generates 3 actionable research plans with experiment designs for remote sensing & computer vision, and delivers a minimalist frontend interface (`advisor.html`) with automated generation in `run.sh`.

**Architecture:** 
- **Data & Context Layer:** `ai/advisor.py` reads raw daily papers directly from `data/{date}.jsonl`, applies hierarchical temporal distillation using prior `advisor_reports` summaries from SQLite for 7-day and 30-day trends, keeping prompt tokens under 8,000.
- **Backend & Database Layer:** `server_modules/advisor.py` exposes REST APIs (`/api/advisor/dates`, `/api/advisor/report`, `/api/advisor/generate`, `/api/advisor/settings`) connected to SQLite (`advisor_reports` and `advisor_settings` tables in `data/statistics.db`).
- **Automation Layer:** Step 4 added to `run.sh` for automatic advisor report generation following paper crawling and AI enhancement.
- **Frontend Layer:** `advisor.html`, `css/advisor.css`, `js/advisor.js` with responsive cards for daily briefing, temporal evolution, and 3 tabbed research ideas with one-click clipboard copying.

**Tech Stack:** Python 3.10+, FastAPI, SQLite (WAL mode, SQLitePool), LangChain (ChatOpenAI / ChatGoogleGenerativeAI / structured output / json_repair), HTML5 / Vanilla CSS / Vanilla JS (Flatpickr, Marked.js).

## Global Constraints

- **No post-processing reference:** Analysis must strictly derive from raw paper metadata (`Title`, `Authors`, `Categories`, `Abstract`), without relying on keyword frequency stats or clustering.
- **Context Budget:** Strict prompt size budget (4,000 - 8,000 tokens) using hierarchical temporal distillation (raw today + condensed 7-day/30-day takeaways).
- **Authentication & Security:** All `/api/advisor/*` endpoints must enforce Bearer token authentication consistent with `app/auth.py`.
- **Database Concurrency:** All SQLite access must use `server_modules.database.connect_db` with `db_lock` or pooled connections to prevent database lock contention.
- **UI Minimalism & Consistency:** Maintain the existing dark/light theme, Inter typography, glassmorphism card design, and responsive layout.

---

### Task 1: Database Schema for Advisor Reports and Settings

**Files:**
- Modify: `server_modules/processor.py:40-85`
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

### Task 2: AI Advisor Engine Core (`ai/advisor.py`)

**Files:**
- Create: `ai/advisor.py`
- Test: `tests/test_advisor_engine.py`

**Interfaces:**
- Consumes: Raw JSONL data files (`data/{date}.jsonl` or `data/{date}_AI_enhanced_*.jsonl`), SQLite `advisor_reports` table via `server_modules.database.connect_db`, LLM client configuration (`MODEL_NAME`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `GOOGLE_API_KEY`).
- Produces: `generate_advisor_report(date: str, topic: Optional[str] = None, force: bool = False, db_path: str = "data/statistics.db") -> Dict` and CLI entry point `python ai/advisor.py --date YYYY-MM-DD`.

- [ ] **Step 1: Write unit tests for AI Advisor engine**

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
    parse_advisor_output,
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
    # Empty DB should return empty context gracefully
    week_context, month_context = fetch_temporal_context("2026-08-08", db_path=TEST_DB)
    assert week_context == "暂无过去7天的历史研报沉淀（系统初始化或首周运行）。"
    assert month_context == "暂无过去30天的历史研报沉淀。"

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

def test_parse_advisor_output_structured():
    sample_output = """
# 今日遥感智能解译前沿与学术导师研判

## 1. 今日前沿速递与导师研判
今日在遥感多模态大模型与弱监督语义分割方向有重要突破...

## 2. 时序演进对比（7天/30天趋势）
近7天内高分辨率光学遥感检测持续上升...

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
    summary, markdown, ideas = parse_advisor_output(sample_output)
    assert len(summary) > 0
    assert len(ideas) == 3
    assert ideas[0]["type"] == "顶会理论/架构创新型"
    assert "Physics-Guided" in ideas[0]["title"]
    assert ideas[1]["type"] == "高价值痛点/任务落地型"
    assert ideas[2]["type"] == "多模态/大模型跨界融合型"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_advisor_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai.advisor'`.

- [ ] **Step 3: Implement `ai/advisor.py`**

Create `/home/default_user/research_acc/ai/advisor.py` with full prompt engineering, temporal extraction, LLM fallback handling, and database persistence.
```python
import os
import sys
import json
import re
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

import dotenv
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
if os.path.exists(os.path.join(root_dir, '.env')):
    dotenv.load_dotenv(os.path.join(root_dir, '.env'))
elif os.path.exists('.env'):
    dotenv.load_dotenv()

# Clean environment variables
for k in os.environ:
    if os.environ[k]:
        os.environ[k] = os.environ[k].strip()

try:
    from server_modules.database import connect_db
except ImportError:
    sys.path.append(root_dir)
    from server_modules.database import connect_db

DEFAULT_TOPIC = "遥感图像的处理与信息提取（目标检测、语义分割等）"
DEFAULT_DB_PATH = os.path.join(root_dir, "data", "statistics.db")

ADVISOR_SYSTEM_PROMPT = """你是一位在遥感图像智能解译（Remote Sensing Image Interpretation）、计算机视觉（Computer Vision）与多模态人工智能领域具有深厚造诣的资深学术导师、博导与顶会（CVPR, ICCV, ECCV, NeurIPS, IEEE TGRS）资深审稿人。
你的科研指导风格：立意深远、直击痛点、论证严谨、注重落地可行性与严密的实验设计。
你当前的科研关注主题为：{topic}。

你将接收到以下原始学术信息：
1. 【今日原始论文集】：当天爬取的所有相关论文（Title, Authors, Categories, Abstract）。
2. 【近7天演进参考脉络】：过去一周的每日技术动向沉淀。
3. 【近30天宏观趋势背景】：过去一月的宏观研究脉络。

请根据这些原始论文，产出一份高水准、逻辑严密、洞察深刻的学术导师研判报告。

输出格式必须严格遵循以下结构（Markdown）：

# 今日遥感智能解译前沿与学术导师研判 ({date})

## 1. 今日前沿速递与导师研判
- **核心技术演进**：研判今日论文在网络架构、特征表示、物理先验或学习范式上的共性趋势与亮点。
- **重点论文深度点评**：挑选 2-3 篇最值得关注的论文进行导师视角点评（分析其切入点、创新机制及对本领域的启示）。
- **跨领域交叉启发**：指出通用 CV/NLP/物理建模领域的哪些新范式可被迁移至遥感任务中。

## 2. 时序演进对比（7天/30天趋势）
- **7天技术演变观察**：哪些细分方向正在快速升温？哪些方向已进入套路化/红海期？
- **30天宏观脉络与顶会审稿偏好**：结合近期顶会录用风向，指明当前最具录用潜力的创新范式与审稿人最反感的缺陷。

## 3. 3篇落地科研思路与实验设计

### 思路1【顶会理论/架构创新型】
- **【选题名称】**: 中英文题目
- **【研究痛点与动机】**: 现有方案瓶颈与核心洞察
- **【核心方法设计】**: 网络架构设计构想、关键模块、核心机制/公式设计
- **【推荐公开数据集与Baseline】**: 明确评测数据集与典型对比 Baseline 方法
- **【实验验证与消融方案】**: 核心对比实验指标、关键消融实验设定
- **【审稿人潜在质疑点与防守策略】**: 预判审稿人可能指出的软肋及防守方案

### 思路2【高价值痛点/任务落地型】
- **【选题名称】**: 中英文题目
- **【研究痛点与动机】**: 复杂场景下的具体应用瓶颈
- **【核心方法设计】**: 针对性解耦、轻量化、弱监督或先验引导方案
- **【推荐公开数据集与Baseline】**: 评测数据集与强基线
- **【实验验证与消融方案】**: 验证方案与关键消融
- **【审稿人潜在质疑点与防守策略】**: 潜在质疑与应对方案

### 思路3【多模态/大模型跨界融合型】
- **【选题名称】**: 中英文题目
- **【研究痛点与动机】**: 遥感多模态大模型、视觉-语言对齐或图文交互难点
- **【核心方法设计】**: 适配遥感特性的跨模态融合机制或指令微调框架
- **【推荐公开数据集与Baseline】**: 多模态基准数据集与主流多模态基线
- **【实验验证与消融方案】**: 零样本/少样本泛化与消融实验
- **【审稿人潜在质疑点与防守策略】**: 针对泛化性/计算代价的质疑与防守
"""

def load_raw_papers_compact(filepath: str, max_papers: int = 80) -> str:
    """从原始 jsonl 文件中紧凑提取论文元数据"""
    if not os.path.exists(filepath):
        return ""
    
    papers = []
    seen_ids = set()
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                item = json.loads(line.strip())
                pid = item.get("id", "")
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    papers.append(item)
            except Exception:
                continue

    if not papers:
        return ""

    # Sort or prioritize (taking up to max_papers)
    selected_papers = papers[:max_papers]
    
    lines = []
    for idx, p in enumerate(selected_papers, 1):
        title = p.get("title", "").strip().replace("\n", " ")
        authors = ", ".join(p.get("authors", [])[:4])
        cats = p.get("categories", [])
        cat_str = ", ".join(cats) if isinstance(cats, list) else str(cats)
        abstract = p.get("summary", "").strip().replace("\n", " ")
        # Truncate abstract if too long
        if len(abstract) > 600:
            abstract = abstract[:597] + "..."
        lines.append(f"[{idx}] Title: {title}\nAuthors: {authors} | Categories: {cat_str}\nAbstract: {abstract}\n")

    return "\n".join(lines)

def fetch_temporal_context(target_date_str: str, db_path: str = DEFAULT_DB_PATH) -> Tuple[str, str]:
    """查询过去 7 天和 30 天的历史研报摘要作为时序演进上下文"""
    if not os.path.exists(db_path):
        return ("暂无过去7天的历史研报沉淀（系统初始化或首周运行）。", "暂无过去30天的历史研报沉淀。")
    
    try:
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d")
    except ValueError:
        return ("日期格式无效", "日期格式无效")

    date_7d_ago = (target_date - timedelta(days=7)).strftime("%Y-%m-%d")
    date_30d_ago = (target_date - timedelta(days=30)).strftime("%Y-%m-%d")

    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        
        # 过去 7 天历史研报
        cursor.execute("""
            SELECT report_date, summary_takeaway
            FROM advisor_reports
            WHERE report_date >= ? AND report_date < ?
            ORDER BY report_date ASC
        """, (date_7d_ago, target_date_str))
        rows_7d = cursor.fetchall()

        # 过去 30 天历史研报
        cursor.execute("""
            SELECT report_date, summary_takeaway
            FROM advisor_reports
            WHERE report_date >= ? AND report_date < ?
            ORDER BY report_date ASC
        """, (date_30d_ago, target_date_str))
        rows_30d = cursor.fetchall()

        if rows_7d:
            week_context = "\n".join([f"- **{r[0]}**: {r[1]}" for r in rows_7d if r[1]])
        else:
            week_context = "暂无过去7天的历史研报沉淀（系统初始化或首周运行）。"

        if rows_30d:
            month_context = f"过去30天共记录 {len(rows_30d)} 篇研报概要：\n" + "\n".join([f"- **{r[0]}**: {r[1][:100]}..." for r in rows_30d if r[1]])
        else:
            month_context = "暂无过去30天的历史研报沉淀。"

        return (week_context, month_context)
    except Exception as e:
        return (f"获取7天历史出错: {e}", f"获取30天历史出错: {e}")
    finally:
        conn.close()

def parse_advisor_output(output_text: str) -> Tuple[str, str, List[Dict]]:
    """从 LLM 生成的 Markdown 文本中解析概要和 3 篇结构化科研思路"""
    # 提取简短摘要 (summary_takeaway)，取前 200 字核心洞察
    summary_takeaway = ""
    overview_match = re.search(r"## 1\. 今日前沿速递与导师研判\s+([\s\S]*?)(?=## 2\.|\Z)", output_text)
    if overview_match:
        clean_text = re.sub(r"[#*\-\n`]", " ", overview_match.group(1)).strip()
        summary_takeaway = (clean_text[:180] + "...") if len(clean_text) > 180 else clean_text
    else:
        clean_text = re.sub(r"[#*\-\n`]", " ", output_text).strip()
        summary_takeaway = clean_text[:180] + "..." if len(clean_text) > 180 else clean_text

    # 解析 3 个思路
    ideas = []
    idea_sections = re.findall(r"### (思路\s*\d+[^#\n]*)\n([\s\S]*?)(?=### 思路|\Z)", output_text)
    
    type_map = {
        "1": "顶会理论/架构创新型",
        "2": "高价值痛点/任务落地型",
        "3": "多模态/大模型跨界融合型"
    }

    for idx, (header, content) in enumerate(idea_sections, 1):
        idea_type = type_map.get(str(idx), "前沿科研思路")
        if "【" in header and "】" in header:
            idea_type = header.split("【")[1].split("】")[0]

        def extract_field(pattern: str) -> str:
            m = re.search(pattern, content)
            return m.group(1).strip() if m else ""

        title = extract_field(r"【选题名称】[：:]?\s*([^\n]+)")
        pain_point = extract_field(r"【研究痛点与动机】[：:]?\s*([\s\S]*?)(?=- \*\*【|\Z)")
        method = extract_field(r"【核心方法设计】[：:]?\s*([\s\S]*?)(?=- \*\*【|\Z)")
        dataset = extract_field(r"【推荐公开数据集与Baseline】[：:]?\s*([\s\S]*?)(?=- \*\*【|\Z)")
        experiment = extract_field(r"【实验验证与消融方案】[：:]?\s*([\s\S]*?)(?=- \*\*【|\Z)")
        defense = extract_field(r"【审稿人潜在质疑点与防守策略】[：:]?\s*([\s\S]*?)(?=- \*\*【|\Z)")

        ideas.append({
            "id": idx,
            "type": idea_type,
            "title": title or f"科研思路 {idx}",
            "pain_point": pain_point,
            "method": method,
            "dataset_baseline": dataset,
            "experiment_plan": experiment,
            "reviewer_defense": defense,
            "raw_content": content.strip()
        })

    return (summary_takeaway, output_text.strip(), ideas)

def get_advisor_topic(db_path: str = DEFAULT_DB_PATH) -> str:
    """获取用户配置的研究主题"""
    if not os.path.exists(db_path):
        return DEFAULT_TOPIC
    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM advisor_settings WHERE key = 'topic'")
        row = cursor.fetchone()
        return row[0] if row and row[0] else DEFAULT_TOPIC
    except Exception:
        return DEFAULT_TOPIC
    finally:
        conn.close()

def generate_advisor_report(
    date_str: str,
    topic: Optional[str] = None,
    force: bool = False,
    db_path: str = DEFAULT_DB_PATH
) -> Dict:
    """执行 AI 导师研报生成并落库"""
    # 检查是否已存在
    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT report_date, topic, summary_takeaway, report_markdown, ideas_json, created_at, updated_at
            FROM advisor_reports
            WHERE report_date = ?
        """, (date_str,))
        existing = cursor.fetchone()
        if existing and not force:
            return {
                "date": existing[0],
                "topic": existing[1],
                "summary_takeaway": existing[2],
                "report_markdown": existing[3],
                "ideas": json.loads(existing[4]) if existing[4] else [],
                "created_at": existing[5],
                "updated_at": existing[6],
                "cached": True
            }
    finally:
        conn.close()

    if not topic:
        topic = get_advisor_topic(db_path)

    # 寻找原始数据文件
    data_dir = os.path.join(root_dir, "data")
    raw_file = os.path.join(data_dir, f"{date_str}.jsonl")
    
    # 优先使用 raw jsonl，若不存在则回退至 AI enhanced jsonl
    if not os.path.exists(raw_file):
        # 查找带有 date_str 的 jsonl 文件
        candidates = [f for f in os.listdir(data_dir) if f.startswith(date_str) and f.endswith(".jsonl")]
        if candidates:
            raw_file = os.path.join(data_dir, candidates[0])
        else:
            raise FileNotFoundError(f"未找到 {date_str} 的论文数据文件")

    papers_text = load_raw_papers_compact(raw_file)
    if not papers_text:
        raise ValueError(f"文件 {raw_file} 中没有可供分析的论文数据")

    week_context, month_context = fetch_temporal_context(date_str, db_path)

    # 初始化 LLM
    model_name = os.environ.get("MODEL_NAME", "deepseek-chat")
    if model_name.lower().startswith("gemini"):
        llm = ChatGoogleGenerativeAI(model=model_name)
    else:
        llm = ChatOpenAI(
            model=model_name,
            extra_body={"thinking": {"type": "disabled"}} if "deepseek" in model_name.lower() else None
        )

    human_template = """请对以下论文和时序上下文进行学术研判：

【分析目标日期】：{date}
【科研关注主题】：{topic}

【今日原始论文集】：
{papers_text}

【近7天演进参考脉络】：
{week_context}

【近30天宏观趋势背景】：
{month_context}
"""

    prompt = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(ADVISOR_SYSTEM_PROMPT),
        HumanMessagePromptTemplate.from_template(human_template)
    ])

    chain = prompt | llm
    response = chain.invoke({
        "topic": topic,
        "date": date_str,
        "papers_text": papers_text,
        "week_context": week_context,
        "month_context": month_context
    })

    content = response.content if hasattr(response, "content") else str(response)
    summary_takeaway, report_markdown, ideas = parse_advisor_output(content)

    # 存入 SQLite
    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO advisor_reports (report_date, topic, summary_takeaway, report_markdown, ideas_json, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(report_date) DO UPDATE SET
                topic = excluded.topic,
                summary_takeaway = excluded.summary_takeaway,
                report_markdown = excluded.report_markdown,
                ideas_json = excluded.ideas_json,
                updated_at = CURRENT_TIMESTAMP
        """, (date_str, topic, summary_takeaway, report_markdown, json.dumps(ideas, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()

    return {
        "date": date_str,
        "topic": topic,
        "summary_takeaway": summary_takeaway,
        "report_markdown": report_markdown,
        "ideas": ideas,
        "cached": False
    }

def main():
    parser = argparse.ArgumentParser(description="AI Academic Advisor Report Generator")
    parser.add_argument("--date", type=str, required=True, help="Target date in YYYY-MM-DD")
    parser.add_argument("--topic", type=str, default=None, help="Custom research topic")
    parser.add_argument("--force", action="store_true", help="Force regenerate even if cached")
    parser.add_argument("--db", type=str, default=DEFAULT_DB_PATH, help="Database path")
    args = parser.parse_args()

    print(f"🎓 Starting Academic Advisor Analysis for {args.date}...", file=sys.stderr)
    try:
        result = generate_advisor_report(args.date, args.topic, args.force, args.db)
        print(f"✅ Advisor report successfully created/loaded for {args.date}.", file=sys.stderr)
        print(f"Summary: {result['summary_takeaway']}", file=sys.stderr)
    except Exception as e:
        print(f"❌ Failed to generate advisor report: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_advisor_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit changes**

```bash
git add ai/advisor.py tests/test_advisor_engine.py
git commit -m "feat(advisor): implement AI advisor analysis engine and CLI"
```

---

### Task 3: Backend API Router for Advisor (`server_modules/advisor.py`)

**Files:**
- Create: `server_modules/advisor.py`
- Modify: `app/main.py:10-70`
- Test: `tests/test_advisor_api.py`

**Interfaces:**
- Consumes: `app.auth.verify_token`, `ai.advisor.generate_advisor_report`, `ai.advisor.get_advisor_topic`, `server_modules.database.connect_db`.
- Produces: Router endpoints `GET /api/advisor/dates`, `GET /api/advisor/report`, `POST /api/advisor/generate`, `GET /api/advisor/settings`, `POST /api/advisor/settings`.

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

Create `/home/default_user/research_acc/server_modules/advisor.py`:
```python
import os
import json
import sqlite3
from typing import Optional, List, Dict
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import app.config as config
from app.auth import verify_token
from server_modules.database import connect_db
from ai.advisor import generate_advisor_report, get_advisor_topic, DEFAULT_TOPIC

router = APIRouter(prefix="/api/advisor", tags=["advisor"])

class GenerateReportRequest(BaseModel):
    date: str
    topic: Optional[str] = None
    force: bool = False

class UpdateSettingsRequest(BaseModel):
    topic: str

@router.get("/dates")
def get_advisor_dates(user: dict = Depends(verify_token)):
    """获取所有已生成研报的日期列表"""
    db_path = getattr(config, "DB_PATH", "data/statistics.db")
    if not os.path.exists(db_path):
        return {"dates": []}
    
    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT report_date FROM advisor_reports ORDER BY report_date DESC")
        rows = cursor.fetchall()
        return {"dates": [r[0] for r in rows]}
    except sqlite3.OperationalError:
        return {"dates": []}
    finally:
        conn.close()

@router.get("/report")
def get_advisor_report(date: str = Query(..., regex=r"^\d{4}-\d{2}-\d{2}$"), user: dict = Depends(verify_token)):
    """获取指定日期的导师研报"""
    db_path = getattr(config, "DB_PATH", "data/statistics.db")
    if not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="研报数据库不存在")
    
    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT report_date, topic, summary_takeaway, report_markdown, ideas_json, created_at, updated_at
            FROM advisor_reports
            WHERE report_date = ?
        """, (date,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"未找到 {date} 的导师研报")
        
        return {
            "date": row[0],
            "topic": row[1],
            "summary_takeaway": row[2],
            "report_markdown": row[3],
            "ideas": json.loads(row[4]) if row[4] else [],
            "created_at": row[5],
            "updated_at": row[6]
        }
    finally:
        conn.close()

@router.post("/generate")
def generate_report_api(req: GenerateReportRequest, user: dict = Depends(verify_token)):
    """生成或重新生成指定日期的研报"""
    db_path = getattr(config, "DB_PATH", "data/statistics.db")
    try:
        result = generate_advisor_report(
            date_str=req.date,
            topic=req.topic,
            force=req.force,
            db_path=db_path
        )
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"研报生成失败: {str(e)}")

@router.get("/settings")
def get_advisor_settings(user: dict = Depends(verify_token)):
    """获取导师科研主题配置"""
    db_path = getattr(config, "DB_PATH", "data/statistics.db")
    topic = get_advisor_topic(db_path)
    return {"topic": topic}

@router.post("/settings")
def update_advisor_settings(req: UpdateSettingsRequest, user: dict = Depends(verify_token)):
    """更新导师科研主题配置"""
    db_path = getattr(config, "DB_PATH", "data/statistics.db")
    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO advisor_settings (key, value, updated_at)
            VALUES ('topic', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
        """, (req.topic.strip(),))
        conn.commit()
        return {"topic": req.topic.strip()}
    finally:
        conn.close()
```

- [ ] **Step 4: Register `advisor_router` in `app/main.py` and serve `advisor.html`**

In `app/main.py`:
- Import `from server_modules.advisor import router as advisor_router`
- Register `app.include_router(advisor_router)`
- Add route `@app.get("/advisor.html") def read_advisor(): return FileResponse("advisor.html")`

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_advisor_api.py -v`
Expected: PASS

- [ ] **Step 6: Commit changes**

```bash
git add server_modules/advisor.py app/main.py tests/test_advisor_api.py
git commit -m "feat(advisor): add FastAPI advisor router and advisor.html static route"
```

---

### Task 4: Pipeline Automation Integration (`run.sh`)

**Files:**
- Modify: `run.sh:133-160`
- Test: `tests/test_advisor_pipeline.py`

**Interfaces:**
- Consumes: Shell execution environment with `data/${today}.jsonl` and `python ai/advisor.py --date ${today}`.
- Produces: Seamless execution in `run.sh` step 4 without breaking previous crawl steps.

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
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_advisor_pipeline.py -v`
Expected: PASS

- [ ] **Step 3: Update `run.sh` to include Step 4**

In `run.sh`, after Step 3 (AI enhancement), add Step 4:
```bash
# 第四步：AI 学术导师前沿研报生成 / Step 4: AI Academic Advisor Report Generation
if [ "$PARTIAL_MODE" = "false" ]; then
    echo "步骤4：生成学术导师前沿分析与科研思路研报... / Step 4: Generating Academic Advisor Report..."
    python ai/advisor.py --date "${today}"
    
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
git commit -m "feat(advisor): integrate advisor report generation into run.sh step 4"
```

---

### Task 5: Minimalist Frontend (`advisor.html`, `css/advisor.css`, `js/advisor.js`)

**Files:**
- Create: `advisor.html`
- Create: `css/advisor.css`
- Create: `js/advisor.js`
- Modify: `index.html:70-93`, `statistic.html:53-76` (add header navigation icon for Academic Advisor `🎓`)
- Test: `tests/test_server.py` (add tests for `advisor.html` and `css/advisor.css`)

**Interfaces:**
- Consumes: `/api/advisor/dates`, `/api/advisor/report`, `/api/advisor/generate`, `/api/advisor/settings`, `Flatpickr`, `marked.js`.
- Produces: Modern responsive advisor workspace with 3 core cards (Daily briefing markdown, temporal comparison, 3 ideas tabs with one-click copy and topic modal).

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
Expected: FAIL (file `advisor.html` does not exist yet).

- [ ] **Step 3: Create `advisor.html`**

Create `/home/default_user/research_acc/advisor.html` with:
- Top navigation with Logo, Back button, Date selector (Flatpickr), Topic settings badge button, Regenerate button, Logout button.
- Breadcrumb and topic banner.
- Card 1: Today's Frontier & Advisor Insights (Markdown rendered via marked.js).
- Card 2: 7-Day & 30-Day Temporal Evolution.
- Card 3: 3 Actionable Research Ideas with Tab Switcher and "📋 复制本篇实验设计" (Copy Experiment Plan) buttons.
- Topic settings modal.

- [ ] **Step 4: Create `css/advisor.css`**

Create `/home/default_user/research_acc/css/advisor.css` maintaining the design system (CSS custom properties, card elevation, clean badge tags, tab animations, markdown typography, copy feedback toast).

- [ ] **Step 5: Create `js/advisor.js`**

Create `/home/default_user/research_acc/js/advisor.js`:
- Auth check on load via `Auth.requireAuth()`.
- Load available dates and current date report.
- Markdown rendering with `marked.parse()`.
- Tab switching logic for the 3 research ideas.
- Clipboard copy with toast notification.
- Topic settings dialog with live save and refresh.
- Loading spinner and error handling states.

- [ ] **Step 6: Update navigation headers in `index.html` and `statistic.html`**

Add an advisor navigation icon button (`🎓` / Academic Cap icon) linking to `advisor.html` in both `index.html` and `statistic.html`.

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

- [ ] **Step 2: Test CLI end-to-end report generation on dummy data**

Run: `python ai/advisor.py --date 2026-07-10 --force`
Expected: Successfully generates report or outputs proper error if no data file.

- [ ] **Step 3: Commit final integration verification**

```bash
git commit -m "chore(advisor): complete end-to-end integration and verification"
```

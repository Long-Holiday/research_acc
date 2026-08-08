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

def _cleanup():
    for ext in ["", "-wal", "-shm"]:
        p = TEST_DB + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

@pytest.fixture(autouse=True)
def setup_env():
    os.makedirs("data", exist_ok=True)
    _cleanup()
    
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
    _cleanup()

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
## 3. 3篇梯队化科研选题与实验设计方案

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

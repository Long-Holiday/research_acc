import os
import sys
import json
import glob
import re
import datetime
import argparse
from typing import List, Dict, Tuple, Optional
import dotenv

import langchain_core.exceptions
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)
from server_modules.database import connect_db

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
if os.path.exists(os.path.join(root_dir, '.env')):
    dotenv.load_dotenv(os.path.join(root_dir, '.env'))
elif os.path.exists('.env'):
    dotenv.load_dotenv()

for k in list(os.environ.keys()):
    if os.environ[k]:
        os.environ[k] = os.environ[k].strip()

DEFAULT_TOPIC = "遥感图像的处理与信息提取（目标检测、语义分割等）"
DEFAULT_DB_PATH = "data/statistics.db"
DEFAULT_DATA_DIR = "data"

def get_advisor_topic(db_path: str = DEFAULT_DB_PATH) -> str:
    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM advisor_settings WHERE key = 'topic'")
        row = cursor.fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    finally:
        conn.close()
    return DEFAULT_TOPIC

def set_advisor_topic(topic: str, db_path: str = DEFAULT_DB_PATH) -> str:
    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO advisor_settings (key, value, updated_at)
            VALUES ('topic', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """, (topic,))
        conn.commit()
    finally:
        conn.close()
    return topic

def init_llm(model_name: Optional[str] = None):
    if not model_name:
        model_name = os.environ.get("MODEL_NAME", "deepseek-chat")
    
    if model_name.lower().startswith("gemini"):
        return ChatGoogleGenerativeAI(
            model=model_name,
            temperature=0.3
        )
    else:
        return ChatOpenAI(
            model=model_name,
            temperature=0.3,
            extra_body={"thinking": {"type": "disabled"}} if "deepseek" in model_name.lower() else None
        )

def load_raw_papers_compact(filepath: str, max_papers: int = 60) -> str:
    if not os.path.exists(filepath):
        return ""
    
    papers = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                item = json.loads(line_str)
                papers.append(item)
            except Exception:
                continue

    # Deduplicate
    seen_ids = set()
    unique_papers = []
    for p in papers:
        pid = p.get("id") or p.get("title")
        if pid and pid not in seen_ids:
            seen_ids.add(pid)
            unique_papers.append(p)

    unique_papers = unique_papers[:max_papers]
    
    formatted_list = []
    for i, p in enumerate(unique_papers, 1):
        title = p.get("title", "").strip()
        authors = ", ".join(p.get("authors", [])[:3])
        cats = ", ".join(p.get("categories", [])[:3]) if isinstance(p.get("categories"), list) else str(p.get("categories", ""))
        summary = p.get("summary", "").strip().replace("\n", " ")
        # Truncate summary to ~300 chars
        if len(summary) > 350:
            summary = summary[:350] + "..."
            
        formatted_list.append(
            f"[{i}] Title: {title}\n"
            f"    Authors: {authors} | Categories: {cats}\n"
            f"    Abstract: {summary}"
        )
        
    return "\n\n".join(formatted_list)

def fetch_temporal_context(target_date_str: str, db_path: str = DEFAULT_DB_PATH) -> Tuple[str, str]:
    try:
        target_date = datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except Exception:
        return "暂无过去7天历史演变记录。", "暂无过去30天宏观脉络记录。"

    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT report_date, summary_takeaway FROM advisor_reports WHERE summary_takeaway IS NOT NULL AND summary_takeaway != '' ORDER BY report_date ASC")
        rows = cursor.fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    week_items = []
    month_items = []
    
    for r_date_str, takeaway in rows:
        try:
            r_date = datetime.datetime.strptime(r_date_str, "%Y-%m-%d").date()
        except Exception:
            continue
            
        delta = (target_date - r_date).days
        if 1 <= delta <= 7:
            week_items.append(f"- **{r_date_str}**: {takeaway}")
        if 1 <= delta <= 30:
            month_items.append(f"- **{r_date_str}**: {takeaway}")

    week_context = "\n".join(week_items) if week_items else "暂无过去7天历史演化摘要。"
    month_context = "\n".join(month_items) if month_items else "暂无过去30天历史演化摘要。"
    
    return week_context, month_context

def parse_stage1_output(text: str) -> Tuple[str, str]:
    part1_2 = text.strip()
    takeaway = ""

    if "## 核心精炼摘要" in text:
        parts = text.split("## 核心精炼摘要", 1)
        part1_2 = parts[0].strip()
        takeaway = parts[1].strip()
    elif "### 核心精炼摘要" in text:
        parts = text.split("### 核心精炼摘要", 1)
        part1_2 = parts[0].strip()
        takeaway = parts[1].strip()

    if not takeaway:
        # Fallback takeaway extraction
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        takeaway = " ".join(lines[:4])[:200]

    return part1_2, takeaway

def parse_stage2_output(text: str) -> List[Dict]:
    ideas = []
    # Split by ### 思路 or ### Idea
    blocks = re.split(r"###\s*(?:思路|Idea)", text)
    
    for block in blocks[1:]:
        lines = block.strip().split("\n")
        header = lines[0] if lines else ""
        
        idea_type = "顶会理论/架构创新型"
        if "落地" in header or "2" in header:
            idea_type = "高价值痛点/任务落地型"
        elif "多模态" in header or "大模型" in header or "3" in header:
            idea_type = "多模态/大模型跨界融合型"
            
        title = ""
        motivation = ""
        method = ""
        datasets = ""
        experiments = ""
        defense = ""

        full_block = "\n" + block
        
        title_match = re.search(r"-\s*\*\*【选题名称】\*\*[:：]\s*(.*)", full_block)
        if title_match:
            title = title_match.group(1).strip()

        motivation_match = re.search(r"-\s*\*\*【研究痛点与动机】\*\*[:：]\s*(.*)", full_block)
        if motivation_match:
            motivation = motivation_match.group(1).strip()

        method_match = re.search(r"-\s*\*\*【核心方法设计】\*\*[:：]\s*(.*)", full_block)
        if method_match:
            method = method_match.group(1).strip()

        datasets_match = re.search(r"-\s*\*\*【推荐公开数据集与Baseline】\*\*[:：]\s*(.*)", full_block)
        if datasets_match:
            datasets = datasets_match.group(1).strip()

        exp_match = re.search(r"-\s*\*\*【实验验证与消融方案】\*\*[:：]\s*(.*)", full_block)
        if exp_match:
            experiments = exp_match.group(1).strip()

        def_match = re.search(r"-\s*\*\*【审稿人潜在质疑点与防守策略】\*\*[:：]\s*(.*)", full_block)
        if def_match:
            defense = def_match.group(1).strip()

        ideas.append({
            "type": idea_type,
            "title": title or header,
            "motivation": motivation,
            "method": method,
            "datasets": datasets,
            "experiments": experiments,
            "defense": defense,
            "raw_text": "### 思路" + block.strip()
        })
        
    return ideas

def generate_stage1_trend_analysis(
    date_str: str,
    topic: str,
    papers_text: str,
    week_context: str,
    month_context: str,
    llm = None
) -> Tuple[str, str]:
    if llm is None:
        llm = init_llm()

    system_prompt = (
        "你是一位遥感图像智能解译（Remote Sensing Image Interpretation）与计算机视觉领域的资深学术导师、博导兼顶会审稿人（CVPR/ICCV/ECCV/TGRS）。\n"
        "你的主要职责是分析研究论文，研判最新技术演进与时序趋势。\n"
        "请严格只根据传入的原始论文摘要信息和历史研报上下文进行逻辑推演与评价，切勿依赖词频统计或外部假设。\n"
        "你的语言风格应当专业、严谨、敏锐且富有启发性。"
    )

    human_prompt = (
        "【导师科研主题】: {topic}\n"
        "【研判目标日期】: {date_str}\n\n"
        "【过去 7 天研报历史摘要上下文】:\n{week_context}\n\n"
        "【过去 30 天宏观脉络上下文】:\n{month_context}\n\n"
        "【今日原始论文紧凑集】:\n{papers_text}\n\n"
        "--- 请产出 Part 1 和 Part 2 研判内容，格式要求如下 ---\n"
        "# 今日遥感智能解译前沿与学术导师研判 ({date_str})\n\n"
        "## 1. 今日前沿速递与导师研判\n"
        "- **核心技术演进**：[研判今日技术突破与主要范式]\n"
        "- **重点论文深度点评**：[挑选 2-3 篇最值得关注的论文点评，分析切入点、创新机制与领域启示]\n"
        "- **跨领域交叉启发**：[通用 CV/NLP/大模型领域的哪些新范式可迁移至遥感任务中]\n\n"
        "## 2. 时序演进对比（7天/30天趋势）\n"
        "- **7天技术演变观察**：[对比过去7天，哪些方向升温，哪些方向趋于同质化/红海]\n"
        "- **30天宏观脉络与顶会审稿偏好**：[近1个月最具录用潜力的创新范式与审稿人最反感的缺陷]\n\n"
        "## 核心精炼摘要\n"
        "[此处给出 150-200 字的精炼摘要，用于后续系统沉淀与时序对比]"
    )

    prompt = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(system_prompt),
        HumanMessagePromptTemplate.from_template(human_prompt)
    ])

    chain = prompt | llm
    res = chain.invoke({
        "topic": topic,
        "date_str": date_str,
        "week_context": week_context,
        "month_context": month_context,
        "papers_text": papers_text
    })

    raw_text = res.content if hasattr(res, "content") else str(res)
    return parse_stage1_output(raw_text)

def generate_stage2_ideas(
    date_str: str,
    topic: str,
    stage1_analysis: str,
    papers_text: str,
    llm = None
) -> Tuple[str, List[Dict]]:
    if llm is None:
        llm = init_llm()

    system_prompt = (
        "你是一位遥感图像智能解译与计算机视觉领域的资深学术导师兼顶会资深审稿人。\n"
        "你的任务是基于第一阶段的前沿趋势研判与今日论文，为课题组研究生设计 3 篇高质量、高可行性、论证严密的落地科研选题与实验方案。\n"
        "选题必须具备明确的创新切入点、可防守的方法架构设计、公认的 Benchmark 对齐方案及审稿人质疑预演。"
    )

    human_prompt = (
        "【导师科研主题】: {topic}\n"
        "【研判目标日期】: {date_str}\n\n"
        "【Stage 1 前沿研判成果】:\n{stage1_analysis}\n\n"
        "【今日代表性论文】:\n{papers_text}\n\n"
        "--- 请构思并输出 3 篇梯队化科研选题与实验设计方案 ---\n\n"
        "## 3. 3篇落地科研思路与实验设计\n\n"
        "### 思路1【顶会理论/架构创新型】\n"
        "- **【选题名称】**: 中英文题目\n"
        "- **【研究痛点与动机】**: 现有方案瓶颈与核心洞察\n"
        "- **【核心方法设计】**: 网络架构设计构想、关键模块、核心机制/公式设计\n"
        "- **【推荐公开数据集与Baseline】**: 明确评测数据集与典型对比 Baseline 方法\n"
        "- **【实验验证与消融方案】**: 核心对比实验指标、关键消融实验设定\n"
        "- **【审稿人潜在质疑点与防守策略】**: 预判审稿人可能指出的软肋及防守方案\n\n"
        "### 思路2【高价值痛点/任务落地型】\n"
        "- **【选题名称】**: 中英文题目\n"
        "- **【研究痛点与动机】**: 复杂场景下的具体应用瓶颈\n"
        "- **【核心方法设计】**: 针对性解耦、轻量化、弱监督或先验引导方案\n"
        "- **【推荐公开数据集与Baseline】**: 评测数据集与强基线\n"
        "- **【实验验证与消融方案】**: 验证方案与关键消融\n"
        "- **【审稿人潜在质疑点与防守策略】**: 潜在质疑与应对方案\n\n"
        "### 思路3【多模态/大模型跨界融合型】\n"
        "- **【选题名称】**: 中英文题目\n"
        "- **【研究痛点与动机】**: 遥感多模态大模型、视觉-语言对齐或图文交互难点\n"
        "- **【核心方法设计】**: 适配遥感特性的跨模态融合机制或指令微调框架\n"
        "- **【推荐公开数据集与Baseline】**: 多模态基准数据集与主流多模态基线\n"
        "- **【实验验证与消融方案】**: 零样本/少样本泛化与消融实验\n"
        "- **【审稿人潜在质疑点与防守策略】**: 针对泛化性/计算代价的质疑与防守"
    )

    prompt = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(system_prompt),
        HumanMessagePromptTemplate.from_template(human_prompt)
    ])

    chain = prompt | llm
    res = chain.invoke({
        "topic": topic,
        "date_str": date_str,
        "stage1_analysis": stage1_analysis,
        "papers_text": papers_text[:8000]
    })

    raw_text = res.content if hasattr(res, "content") else str(res)
    ideas = parse_stage2_output(raw_text)
    return raw_text, ideas

def get_unprocessed_dates(data_dir: str = DEFAULT_DATA_DIR, db_path: str = DEFAULT_DB_PATH) -> List[str]:
    if not os.path.exists(data_dir):
        return []

    # Find all dates in data_dir
    files = os.listdir(data_dir)
    file_dates = set()
    date_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})")
    
    for f in files:
        if f.endswith(".jsonl"):
            match = date_pattern.match(f)
            if match:
                file_dates.add(match.group(1))

    # Query DB processed dates
    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT report_date FROM advisor_reports")
        db_dates = {row[0] for row in cursor.fetchall()}
    except Exception:
        db_dates = set()
    finally:
        conn.close()

    unprocessed = sorted(list(file_dates - db_dates))
    return unprocessed

def generate_advisor_report(
    date_str: str,
    topic: Optional[str] = None,
    force: bool = False,
    backfill: bool = False,
    data_dir: str = DEFAULT_DATA_DIR,
    db_path: str = DEFAULT_DB_PATH
) -> Dict:
    if backfill:
        backfill_historical_reports(data_dir=data_dir, db_path=db_path, topic=topic, force=False)

    if not topic:
        topic = get_advisor_topic(db_path)

    # Check if report exists and not force
    conn = connect_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT report_date, topic, summary_takeaway, report_markdown, ideas_json FROM advisor_reports WHERE report_date = ?", (date_str,))
        row = cursor.fetchone()
        if row and not force:
            return {
                "report_date": row[0],
                "topic": row[1],
                "summary_takeaway": row[2],
                "report_markdown": row[3],
                "ideas_json": json.loads(row[4]) if row[4] else [],
                "cached": True
            }
    finally:
        conn.close()

    # Find matching paper file
    target_file = None
    possible_files = [
        os.path.join(data_dir, f"{date_str}_AI_enhanced_Chinese.jsonl"),
        os.path.join(data_dir, f"{date_str}.jsonl")
    ]
    for pf in possible_files:
        if os.path.exists(pf):
            target_file = pf
            break
            
    if not target_file:
        pattern = os.path.join(data_dir, f"{date_str}*.jsonl")
        matches = glob.glob(pattern)
        if matches:
            target_file = matches[0]

    if not target_file or not os.path.exists(target_file):
        raise FileNotFoundError(f"未找到 {date_str} 对应的论文数据文件。")

    papers_text = load_raw_papers_compact(target_file)
    if not papers_text:
        raise ValueError(f"{target_file} 数据为空。")

    week_context, month_context = fetch_temporal_context(date_str, db_path=db_path)
    llm = init_llm()

    # Stage 1 execution
    part1_2, summary_takeaway = generate_stage1_trend_analysis(
        date_str=date_str,
        topic=topic,
        papers_text=papers_text,
        week_context=week_context,
        month_context=month_context,
        llm=llm
    )

    # Stage 2 execution
    part3_markdown, ideas_json = generate_stage2_ideas(
        date_str=date_str,
        topic=topic,
        stage1_analysis=part1_2,
        papers_text=papers_text,
        llm=llm
    )

    full_markdown = f"{part1_2}\n\n{part3_markdown}"

    # Save to SQLite
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
        """, (date_str, topic, summary_takeaway, full_markdown, json.dumps(ideas_json, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()

    return {
        "report_date": date_str,
        "topic": topic,
        "summary_takeaway": summary_takeaway,
        "report_markdown": full_markdown,
        "ideas_json": ideas_json,
        "cached": False
    }

def backfill_historical_reports(
    data_dir: str = DEFAULT_DATA_DIR,
    db_path: str = DEFAULT_DB_PATH,
    topic: Optional[str] = None,
    force: bool = False
) -> List[str]:
    unprocessed = get_unprocessed_dates(data_dir=data_dir, db_path=db_path)
    if not unprocessed:
        print("所有历史数据均已处理完毕，无须补全。")
        return []

    print(f"检测到 {len(unprocessed)} 个未处理历史日期，按时间升序补全: {unprocessed}")
    processed = []
    
    for date_str in unprocessed:
        print(f"➡️ 正在回溯生成 {date_str} 学术导师研报...")
        try:
            generate_advisor_report(
                date_str=date_str,
                topic=topic,
                force=force,
                backfill=False,
                data_dir=data_dir,
                db_path=db_path
            )
            processed.append(date_str)
            print(f"✅ {date_str} 研报生成成功。")
        except Exception as e:
            print(f"⚠️ {date_str} 研报生成失败: {e}", file=sys.stderr)

    return processed

def main():
    parser = argparse.ArgumentParser(description="AI 学术导师前沿研报生成与历史补漏")
    parser.add_argument("--date", type=str, help="目标日期 YYYY-MM-DD (默认今日)")
    parser.add_argument("--topic", type=str, help="自定义导师科研主题")
    parser.add_argument("--backfill", action="store_true", help="自动按时序补全所有缺失的历史研报")
    parser.add_argument("--force", action="store_true", help="强制重新生成研报")
    parser.add_argument("--data_dir", type=str, default=DEFAULT_DATA_DIR, help="数据目录")
    parser.add_argument("--db_path", type=str, default=DEFAULT_DB_PATH, help="数据库路径")

    args = parser.parse_args()

    date_str = args.date
    if not date_str:
        date_str = datetime.date.today().strftime("%Y-%m-%d")

    if args.backfill:
        print("开始执行历史未处理研报回溯补全...")
        backfill_historical_reports(data_dir=args.data_dir, db_path=args.db_path, topic=args.topic, force=args.force)

    print(f"正在生成/获取 {date_str} 的学术导师研报...")
    report = generate_advisor_report(
        date_str=date_str,
        topic=args.topic,
        force=args.force,
        backfill=False,
        data_dir=args.data_dir,
        db_path=args.db_path
    )
    print(f"✅ 完成！日期: {report['report_date']} | 缓存: {report['cached']}")

if __name__ == "__main__":
    main()

import os
import sys
import json
import glob
import re
import datetime
import argparse
from typing import Any, List, Dict, Tuple, Optional
import dotenv
from json_repair import repair_json

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import langchain_core.exceptions
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)
from server_modules.database import connect_db

if os.path.exists(os.path.join(root_dir, '.env')):
    dotenv.load_dotenv(os.path.join(root_dir, '.env'))
elif os.path.exists('.env'):
    dotenv.load_dotenv()

for k in list(os.environ.keys()):
    if os.environ[k]:
        os.environ[k] = os.environ[k].strip()

DEFAULT_TOPIC = "计算机视觉算法（含VLM、智能体等）在遥感中的应用"
DEFAULT_DB_PATH = "data/statistics.db"
DEFAULT_DATA_DIR = "data"


def _content_to_text(content: Any) -> str:
    """Convert LangChain/OpenAI text blocks and ordinary values to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [_content_to_text(item) for item in content]
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        for key in ("text", "content", "value"):
            if key in content:
                value = _content_to_text(content[key])
                if value:
                    return value
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def _repair_json_output(raw_text: Any) -> Optional[Any]:
    """Extract and repair a JSON object/list from a model response.

    Models commonly wrap JSON in Markdown fences, add a short preamble, use
    single quotes, or stop before the final closing brace. json-repair handles
    those syntax issues; the candidate extraction keeps normal Markdown from
    being mistaken for JSON.
    """
    if isinstance(raw_text, (dict, list)):
        return raw_text

    text = _content_to_text(raw_text).strip().lstrip("\ufeff")
    if not text:
        return None

    candidates = []
    fenced_candidates = [
        match.group(1).strip()
        for match in re.finditer(r"```(?:json|javascript|js)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    ]
    candidates.extend(fenced_candidates)

    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("[{") or stripped == "[]":
        candidates.append(stripped)

    # Also support prose before a JSON object/list and truncated JSON.
    first_json = re.search(r"[\[{]", text)
    prefix = text[:first_json.start()].strip() if first_json else ""
    if first_json and prefix and len(prefix) <= 120 and (
        re.search(r"json|response|output|result|结果|输出|对象|如下", prefix, re.IGNORECASE)
        or "\n" not in prefix
    ):
        candidates.append(text[first_json.start():])

    seen = set()
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)

        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, (dict, list)):
                return parsed
        except (TypeError, json.JSONDecodeError):
            pass

        try:
            repaired = repair_json(candidate, return_objects=True)
            if isinstance(repaired, (dict, list)) and repaired:
                return repaired
        except Exception:
            continue

    return None


def repair_and_extract_json(raw_text: Any) -> Any:
    """Public JSON repair helper used by the advisor pipeline and tests."""
    repaired = _repair_json_output(raw_text)
    return repaired if repaired is not None else {}


def _mapping_value(mapping: Dict, names: List[str]) -> Any:
    for name in names:
        if name in mapping and mapping[name] not in (None, ""):
            return mapping[name]
    return None


def _value_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_value_to_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            item_text = _value_to_text(item)
            if item_text:
                parts.append(f"- **{key}**：{item_text}")
        return "\n".join(parts)
    return str(value).strip()

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
        # Keep the site-wide model as the primary setting while supporting
        # the advisor-specific setting used by older deployments.
        model_name = (
            os.environ.get("MODEL_NAME")
            or os.environ.get("ADVISOR_MODEL_NAME")
            or "deepseek-chat"
        )
    model_name = model_name.strip()
    model_lower = model_name.lower()

    if model_lower.startswith("gemini"):
        return ChatGoogleGenerativeAI(
            model=model_name,
            temperature=0.3,
            google_api_key=os.environ.get("GOOGLE_API_KEY"),
        )

    openai_kwargs = {
        "model": model_name,
        "temperature": 0.3,
        "openai_api_key": os.environ.get("OPENAI_API_KEY"),
    }
    # Advisor reports require DeepSeek's reasoning mode for multi-stage
    # analysis. The HTTP endpoint runs this work in the background.
    if "deepseek" in model_lower:
        openai_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

    return ChatOpenAI(**openai_kwargs)

def load_raw_papers_compact(filepath: str, max_papers: int = 60) -> str:
    if not os.path.exists(filepath):
        return ""
    
    # 流式读取 + 边读边去重边格式化，达到 max_papers 即停止，
    # 避免把整个数据文件（含全部论文 JSON）一次性读入内存。
    seen_ids = set()
    formatted_list = []
    
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                item = json.loads(line_str)
            except Exception:
                continue

            pid = item.get("id") or item.get("title")
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)

            i = len(formatted_list) + 1
            title = item.get("title", "").strip()
            authors = ", ".join(item.get("authors", [])[:3])
            cats = ", ".join(item.get("categories", [])[:3]) if isinstance(item.get("categories"), list) else str(item.get("categories", ""))
            
            # Prefer AI-enhanced fields for higher information density
            ai_info = item.get("AI", {})
            
            # Use AI.tldr if available (more concise than raw abstract)
            tldr = (ai_info.get("tldr") or "").strip()
            if not tldr:
                summary = item.get("summary", "").strip().replace("\n", " ")
                if len(summary) > 350:
                    summary = summary[:350] + "..."
                tldr = summary
            
            # Build structured info from AI fields
            motivation = (ai_info.get("motivation") or "").strip()
            method = (ai_info.get("method") or "").strip()
            cross_potential = (ai_info.get("remote_sensing_cross") or "").strip()
            translated_title = (ai_info.get("translated_title") or "").strip()
            
            entry = f"[{i}] Title: {title}"
            if translated_title:
                entry += f" ({translated_title})"
            entry += f"\n    Authors: {authors} | Categories: {cats}"
            entry += f"\n    TLDR: {tldr}"
            if motivation:
                entry += f"\n    Motivation: {motivation}"
            if method:
                entry += f"\n    Method: {method}"
            if cross_potential:
                entry += f"\n    CrossPotential: {cross_potential}"
            
            formatted_list.append(entry)

            if len(formatted_list) >= max_papers:
                break

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

def _parse_stage1_markdown(text: str, fallback_takeaway: str = "") -> Tuple[str, str]:
    text = _content_to_text(text).strip()
    takeaway = _content_to_text(fallback_takeaway).strip()

    summary_match = re.search(
        r"(?im)^\s*#{2,4}\s*(?:核心精炼摘要|核心摘要|摘要)\s*:?[ \t]*$",
        text,
    )
    if summary_match:
        part1_2 = text[:summary_match.start()].strip()
        parsed_takeaway = text[summary_match.end():].strip()
        if parsed_takeaway:
            takeaway = parsed_takeaway
    else:
        part1_2 = text

    if not takeaway:
        # Fallback takeaway extraction for Markdown responses without the
        # requested summary heading.
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        takeaway = " ".join(lines[:4])[:200]

    return part1_2, takeaway


def _format_stage1_json(data: Dict) -> Tuple[str, str]:
    """Convert a structured stage-1 response to the normal Markdown shape."""
    payload = data
    nested = _mapping_value(data, ["report", "result", "data"])
    if isinstance(nested, dict):
        payload = nested

    takeaway = _value_to_text(
        _mapping_value(payload, [
            "summary_takeaway", "takeaway", "summary", "核心精炼摘要", "核心摘要"
        ])
    )
    markdown = _value_to_text(
        _mapping_value(payload, [
            "report_markdown", "markdown", "content", "analysis", "report"
        ])
    )
    if markdown:
        return _parse_stage1_markdown(markdown, takeaway)

    section1 = _mapping_value(payload, ["part1", "section1", "前沿研判", "今日前沿速递与导师研判"])
    section2 = _mapping_value(payload, ["part2", "section2", "时序演进对比", "趋势对比"])
    technical = _mapping_value(payload, ["core_trends", "技术演进", "核心技术演进"])
    papers = _mapping_value(payload, ["paper_reviews", "重点论文点评", "重点论文深度点评"])
    cross_domain = _mapping_value(payload, ["cross_domain", "跨领域交叉启发"])
    temporal_7d = _mapping_value(payload, ["trend_7d", "7天技术演变观察"])
    temporal_30d = _mapping_value(payload, ["trend_30d", "30天宏观脉络与审稿偏好"])

    lines = []
    if section1:
        lines.extend(["## 1. 今日前沿速递与导师研判", _value_to_text(section1)])
    elif any(value is not None for value in (technical, papers, cross_domain)):
        lines.append("## 1. 今日前沿速递与导师研判")
        for label, value in (
            ("核心技术演进", technical),
            ("重点论文深度点评", papers),
            ("跨领域交叉启发", cross_domain),
        ):
            if value is not None:
                lines.append(f"- **{label}**：{_value_to_text(value)}")

    if section2:
        lines.extend(["## 2. 时序演进对比（7天 / 30天趋势）", _value_to_text(section2)])
    elif any(value is not None for value in (temporal_7d, temporal_30d)):
        lines.append("## 2. 时序演进对比（7天 / 30天趋势）")
        for label, value in (
            ("7天技术演变观察", temporal_7d),
            ("30天宏观脉络与审稿偏好", temporal_30d),
        ):
            if value is not None:
                lines.append(f"- **{label}**：{_value_to_text(value)}")

    if lines:
        return "\n\n".join(lines).strip(), takeaway
    return "", takeaway


def parse_stage1_output(text: str) -> Tuple[str, str]:
    text = _content_to_text(text)
    structured = _repair_json_output(text)
    if isinstance(structured, dict):
        part1_2, takeaway = _format_stage1_json(structured)
        if part1_2:
            return part1_2, takeaway

    return _parse_stage1_markdown(text)

def _extract_field(block: str, field_name: str) -> str:
    """Extract a field value from a block with flexible pattern matching."""
    # Try multiple common formatting variants the LLM might produce
    patterns = [
        rf"-\s*\*\*【{field_name}】\*\*[:：]\s*(.*)",        # - **【X】**: val
        rf"-\s*\*\*【{field_name}】\*\*\s+(.*)",             # - **【X】** val (missing colon)
        rf"\*\*【{field_name}】\*\*[:：]\s*(.*)",             # **【X】**: val (no leading dash)
        rf"-\s*【{field_name}】[:：]\s*(.*)",                  # - 【X】: val (no bold)
        rf"【{field_name}】[:：]\s*(.*)",                       # 【X】: val
    ]
    for pat in patterns:
        match = re.search(pat, block)
        if match:
            return match.group(1).strip()
    return ""


_IDEA_FIELD_ALIASES = {
    "type": ["type", "idea_type", "类别", "类型", "选题类型"],
    "title": ["title", "name", "选题名称", "科研选题", "题目"],
    "motivation": ["motivation", "研究痛点与动机", "痛点与动机", "动机"],
    "method": ["method", "核心方法设计", "方法设计", "方法"],
    "datasets": ["datasets", "推荐公开数据集与Baseline", "数据集与Baseline", "数据集", "baseline"],
    "experiments": ["experiments", "实验验证与消融方案", "实验与消融", "实验方案"],
    "defense": ["defense", "审稿人潜在质疑点与防守策略", "质疑与防守", "防守策略"],
}


def _default_idea_type(index: int, header: str = "") -> str:
    header_lower = (header or "").lower()
    if any(kw in header_lower for kw in ["落地", "痛点", "应用", "任务"]):
        return "高价值痛点/任务落地型"
    if any(kw in header_lower for kw in ["多模态", "大模型", "跨界", "融合", "multimodal", "llm", "foundation"]):
        return "多模态/大模型跨界融合型"
    if index == 1:
        return "高价值痛点/任务落地型"
    if index >= 2:
        return "多模态/大模型跨界融合型"
    return "顶会理论/架构创新型"


def _format_idea_markdown(idea: Dict, index: int) -> str:
    idea_type = idea.get("type") or _default_idea_type(index)
    lines = [f"### 思路{index + 1}【{idea_type}】"]
    labels = (
        ("选题名称", "title"),
        ("研究痛点与动机", "motivation"),
        ("核心方法设计", "method"),
        ("推荐公开数据集与Baseline", "datasets"),
        ("实验验证与消融方案", "experiments"),
        ("审稿人潜在质疑点与防守策略", "defense"),
    )
    for label, key in labels:
        value = _value_to_text(idea.get(key))
        if value:
            lines.append(f"- **【{label}】**：{value}")
    return "\n".join(lines)


def _normalize_idea(value: Any, index: int) -> Optional[Dict]:
    if not isinstance(value, dict):
        return None

    nested = _mapping_value(value, ["idea", "research_idea", "方案"])
    if isinstance(nested, dict):
        value = nested

    idea_type = _value_to_text(_mapping_value(value, _IDEA_FIELD_ALIASES["type"]))
    title = _value_to_text(_mapping_value(value, _IDEA_FIELD_ALIASES["title"]))
    idea = {
        "type": idea_type or _default_idea_type(index, title),
        "title": title or f"思路 {index + 1}",
    }
    for key, aliases in _IDEA_FIELD_ALIASES.items():
        if key in ("type", "title"):
            continue
        idea[key] = _value_to_text(_mapping_value(value, aliases))

    raw_text = _value_to_text(_mapping_value(value, ["raw_text", "raw", "原文"]))
    idea["raw_text"] = raw_text or _format_idea_markdown(idea, index)
    return idea


def _extract_structured_ideas(payload: Any) -> List[Dict]:
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        raw_ideas = _mapping_value(payload, ["ideas", "research_ideas", "items", "选题", "科研选题"])
        if isinstance(raw_ideas, str):
            raw_ideas = _repair_json_output(raw_ideas)
        if isinstance(raw_ideas, dict):
            # Support {"idea1": {...}, "idea2": {...}} as well as one idea.
            if any(isinstance(item, dict) for item in raw_ideas.values()):
                raw_ideas = list(raw_ideas.values())
            else:
                raw_ideas = [raw_ideas]
        if isinstance(raw_ideas, list):
            candidates = raw_ideas
        elif any(key in payload for key in _IDEA_FIELD_ALIASES["title"]):
            candidates = [payload]
        else:
            candidates = [value for key, value in payload.items() if re.match(r"(?:思路|idea)\s*\d*", str(key), re.IGNORECASE)]
    else:
        candidates = []

    ideas = []
    for value in candidates:
        normalized = _normalize_idea(value, len(ideas))
        if normalized:
            ideas.append(normalized)
    return ideas


def _extract_stage2_markdown(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return _value_to_text(_mapping_value(payload, ["report_markdown", "markdown", "content", "report"]))


def _parse_stage2_markdown(text: str) -> List[Dict]:
    ideas = []
    # Split by level-2/3/4 "思路" or "Idea" headings, with optional numbers.
    blocks = re.split(r"(?im)^\s*#{2,4}\s*(?:思路|Idea)\s*\d*\s*", text)
    
    for block in blocks[1:]:
        lines = block.strip().split("\n")
        header = lines[0] if lines else ""
        
        # Determine idea type from header content
        idea_type = _default_idea_type(len(ideas), header)
            
        full_block = "\n" + block

        title = _extract_field(full_block, "选题名称")
        motivation = _extract_field(full_block, "研究痛点与动机")
        method = _extract_field(full_block, "核心方法设计")
        datasets = _extract_field(full_block, "推荐公开数据集与Baseline")
        experiments = _extract_field(full_block, "实验验证与消融方案")
        defense = _extract_field(full_block, "审稿人潜在质疑点与防守策略")

        ideas.append({
            "type": idea_type,
            "title": title or header.strip(),
            "motivation": motivation,
            "method": method,
            "datasets": datasets,
            "experiments": experiments,
            "defense": defense,
            "raw_text": "### 思路" + block.strip()
        })
        
    return ideas


def parse_stage2_output(text: str) -> List[Dict]:
    text = _content_to_text(text)
    structured = _repair_json_output(text)
    if structured is not None:
        ideas = _extract_structured_ideas(structured)
        if ideas:
            return ideas
        markdown = _extract_stage2_markdown(structured)
        if markdown:
            text = markdown

    return _parse_stage2_markdown(text)


def _format_ideas_markdown(ideas: List[Dict]) -> str:
    return "## 3. 3篇梯队化科研选题与实验设计方案\n\n" + "\n\n".join(
        _format_idea_markdown(idea, index) for index, idea in enumerate(ideas)
    )


def parse_ideas_json(raw_value: Any) -> List[Dict]:
    """Read stored ideas safely, repairing legacy or partially written JSON."""
    if raw_value in (None, ""):
        return []

    decoded = raw_value if isinstance(raw_value, (dict, list)) else _repair_json_output(raw_value)
    if decoded is not None:
        ideas = _extract_structured_ideas(decoded)
        if ideas:
            return ideas
        if decoded == []:
            return []

    # A legacy row may contain the original Markdown rather than JSON.
    return parse_stage2_output(_content_to_text(raw_value))

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

    has_week = week_context and "暂无" not in week_context
    has_month = month_context and "暂无" not in month_context

    system_prompt = (
        "你是计算机视觉（Computer Vision）与遥感交叉领域的资深学术导师（博导）、顶会/顶刊审稿人（CVPR/ICCV/ECCV/TGRS/ISPRS）。\n"
        "你当前指导的课题组聚焦于「计算机视觉算法在遥感中的应用（{topic}）」方向，主要关注视觉大模型（VLM）、AI智能体（Agents）、生成式AI等计算机前沿方向在遥感场景下的交叉创新与落地。\n\n"
        "## 工作准则\n"
        "1. **证据驱动**：所有观点必须锚定到传入论文集中具体论文的编号（如[3]、[17]），不得凭空臆断。\n"
        "2. **深度优先**：点评论文时，聚焦其核心创新机制和方法论突破，而非复述摘要。分析切入角度应当是审稿人视角——指出创新点的成立条件与潜在局限。\n"
        "3. **时序连贯**：若提供了历史研报上下文，需与之对比发现演进趋势、范式转移或同质化信号。若无历史数据则如实说明，绝不虚构。\n"
        "4. **语言风格**：专业、严谨、敏锐且有启发性。多用判断句，少用描述句。"
    )

    # Build temporal analysis instructions dynamically based on data availability
    temporal_7d_instruction = (
        "[对比过去7天研报，识别哪些技术方向在升温、哪些在同质化/红海化，并锚定到具体论文编号]"
        if has_week else
        "[直接书写'暂无过去7天历史数据，不作趋势对比。']"
    )
    temporal_30d_instruction = (
        "[结合近30天宏观脉络，判断哪些创新范式最具顶会/顶刊录用潜力，审稿人最常拒稿的缺陷模式是什么]"
        if has_month else
        "[直接书写'暂无过去30天历史数据，不作趋势对比。']"
    )

    human_prompt = (
        "【科研主题】: {topic}\n"
        "【目标日期】: {date_str}\n\n"
        "【过去 7 天研报摘要】:\n{week_context}\n\n"
        "【过去 30 天宏观脉络】:\n{month_context}\n\n"
        "【今日论文集（含AI结构化增强信息）】:\n{papers_text}\n\n"
        "---\n"
        "请按以下格式输出研判报告，每个要点都必须引用具体论文编号：\n\n"
        "# 今日计算机视觉与遥感交叉前沿与导师研判 ({date_str})\n\n"
        "## 1. 今日前沿速递与导师研判\n"
        "- **核心技术演进**：[从今日论文集中提炼 2-3 条最显著的技术趋势或范式变化，每条锚定论文编号]\n"
        "- **重点论文深度点评**：[精选 2-3 篇最值得关注的论文，以审稿人视角分析：(a) 创新切入点与成立条件 (b) 核心技术机制 (c) 对领域的启示与局限]\n"
        "- **跨领域交叉启发**：[今日论文中哪些通用CV/NLP/大模型的新范式可迁移至遥感「{topic}」任务中，指出具体迁移路径与适配要点]\n\n"
        "## 2. 时序演进对比（7天 / 30天趋势）\n"
        "- **7天技术演变观察**：" + temporal_7d_instruction + "\n"
        "- **30天宏观脉络与审稿偏好**：" + temporal_30d_instruction + "\n\n"
        "## 核心精炼摘要\n"
        "[150-200 字精炼摘要：概括今日最重要的 2-3 个发现，用于后续日期的时序对比输入。格式为纯文本段落，不含 Markdown 标记]\n\n"
        "【机器可读输出协议】优先只输出一个合法 JSON 对象：{{\"report_markdown\": \"包含第1、2节的 Markdown\", \"summary_takeaway\": \"纯文本摘要\"}}。不要输出代码围栏或 JSON 之外的解释。若模型无法遵守 JSON 协议，则按上面的 Markdown 模板输出，服务端会自动修复。"
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

    raw_text = _content_to_text(res.content if hasattr(res, "content") else res)
    if not raw_text.strip():
        raise ValueError("导师模型在前沿研判阶段返回了空内容。")
    return parse_stage1_output(raw_text)

def _truncate_papers_by_count(papers_text: str, max_chars: int = 12000) -> str:
    """Truncate papers text by complete paper entries instead of hard character cut."""
    if len(papers_text) <= max_chars:
        return papers_text
    papers = papers_text.split("\n\n")
    result = []
    total = 0
    for p in papers:
        if total + len(p) > max_chars:
            break
        result.append(p)
        total += len(p) + 2  # +2 for "\n\n"
    return "\n\n".join(result)


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
        "你是计算机视觉与遥感交叉领域的资深学术导师兼顶会/顶刊审稿人。\n"
        "你当前指导的课题组聚焦「计算机视觉算法在遥感中的应用（{topic}）」方向。你的任务：基于前沿趋势研判和今日论文，为课题组研究生设计 3 篇高质量、可落地的科研选题与完整实验方案，重点突出前沿算法（如VLM、智能体、生成式AI等）在遥感任务中的交叉创新应用。\n\n"
        "## 选题质量标准\n"
        "1. **创新锚定**：每个选题必须明确指出它受到今日哪篇论文（引用编号如[3]）的启发，以及在其基础上的差异化创新点。\n"
        "2. **可行性优先**：方法设计要具体到核心模块和关键公式/机制层面，不能只是概念性描述。\n"
        "3. **防守意识**：每个选题都要预演审稿人最可能的 2-3 个质疑，并给出有说服力的防守策略。\n"
        "4. **梯队互补**：3个选题应覆盖不同的创新维度（理论深度、应用价值、跨界融合），形成梯队互补。"
    )

    human_prompt = (
        "【科研主题】: {topic}\n"
        "【目标日期】: {date_str}\n\n"
        "【Stage 1 前沿研判成果】:\n{stage1_analysis}\n\n"
        "【今日论文集】:\n{papers_text}\n\n"
        "---\n"
        "请构思 3 篇梯队化科研选题。严格按以下格式输出，不要修改标记符号：\n\n"
        "## 3. 3篇梯队化科研选题与实验设计方案\n\n"
        "### 思路1【顶会理论/架构创新型】\n"
        "- **【选题名称】**：中英文题目\n"
        "- **【研究痛点与动机】**：该方向当前的核心瓶颈是什么，受到今日哪篇论文[编号]的启发\n"
        "- **【核心方法设计】**：具体的网络架构/算法设计、关键模块命名与功能、核心机制或公式描述\n"
        "- **【推荐公开数据集与Baseline】**：具体的评测数据集名称及对比Baseline方法\n"
        "- **【实验验证与消融方案】**：主实验指标、关键消融实验设定（逐模块消融）\n"
        "- **【审稿人潜在质疑点与防守策略】**：2-3个预判质疑及对应防守方案\n\n"
        "### 思路2【高价值痛点/任务落地型】\n"
        "- **【选题名称】**：中英文题目\n"
        "- **【研究痛点与动机】**：实际应用场景中的具体瓶颈，受哪篇论文[编号]启发\n"
        "- **【核心方法设计】**：针对性的轻量化/解耦/弱监督/先验引导方案设计\n"
        "- **【推荐公开数据集与Baseline】**：评测数据集与强基线方法\n"
        "- **【实验验证与消融方案】**：验证方案与关键消融设定\n"
        "- **【审稿人潜在质疑点与防守策略】**：2-3个预判质疑与应对方案\n\n"
        "### 思路3【多模态/大模型跨界融合型】\n"
        "- **【选题名称】**：中英文题目\n"
        "- **【研究痛点与动机】**：跨模态/大模型在该领域的具体难点，受哪篇论文[编号]启发\n"
        "- **【核心方法设计】**：跨模态融合机制或大模型适配/微调框架的具体设计\n"
        "- **【推荐公开数据集与Baseline】**：多模态基准数据集与主流基线\n"
        "- **【实验验证与消融方案】**：泛化性验证与消融实验\n"
        "- **【审稿人潜在质疑点与防守策略】**：2-3个预判质疑与防守策略\n\n"
        "【机器可读输出协议】优先只输出一个合法 JSON 对象：{{\"report_markdown\": \"第3节 Markdown，可选\", \"ideas\": [{{\"type\": \"选题类型\", \"title\": \"选题名称\", \"motivation\": \"研究痛点与动机\", \"method\": \"核心方法设计\", \"datasets\": \"数据集与Baseline\", \"experiments\": \"实验验证与消融方案\", \"defense\": \"质疑与防守策略\"}}]}}。数组必须包含3个方案。不要输出代码围栏或 JSON 之外的解释。若无法遵守 JSON 协议，则严格按上面的 Markdown 模板输出，服务端会自动修复。"
    )

    prompt = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(system_prompt),
        HumanMessagePromptTemplate.from_template(human_prompt)
    ])

    chain = prompt | llm
    # Truncate by complete paper entries instead of hard character cut
    truncated_papers = _truncate_papers_by_count(papers_text, max_chars=12000)
    res = chain.invoke({
        "topic": topic,
        "date_str": date_str,
        "stage1_analysis": stage1_analysis,
        "papers_text": truncated_papers
    })

    raw_text = _content_to_text(res.content if hasattr(res, "content") else res)
    if not raw_text.strip():
        raise ValueError("导师模型在科研选题阶段返回了空内容。")
    ideas = parse_stage2_output(raw_text)
    structured = _repair_json_output(raw_text)
    if structured is not None:
        markdown = _extract_stage2_markdown(structured)
        if not markdown and ideas:
            markdown = _format_ideas_markdown(ideas)
        if markdown:
            raw_text = markdown
    return raw_text, ideas

def get_unprocessed_dates(data_dir: str = DEFAULT_DATA_DIR, db_path: str = DEFAULT_DB_PATH, force: bool = False) -> List[str]:
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

    if force:
        return sorted(list(file_dates))

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
                "ideas_json": parse_ideas_json(row[4]),
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
    unprocessed = get_unprocessed_dates(data_dir=data_dir, db_path=db_path, force=force)
    if not unprocessed:
        print("所有历史数据均已处理完毕，无须补全。")
        return []

    print(f"检测到 {len(unprocessed)} 个历史日期，按时间升序处理: {unprocessed}")
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

"""Shared advisor settings and response parsing, without model clients."""

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from json_repair import repair_json
from server_modules.database import connect_db

DEFAULT_TOPIC = "计算机视觉算法（含VLM、智能体等）在遥感中的应用"



DEFAULT_DB_PATH = "data/statistics.db"



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


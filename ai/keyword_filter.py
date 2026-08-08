import os
import json
import re
from typing import List, Optional, Set
import dotenv

from json_repair import repair_json
from langchain_core.messages import SystemMessage, HumanMessage

try:
    from ai.advisor import init_llm
except ImportError:
    from advisor import init_llm

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
if os.path.exists(os.path.join(root_dir, '.env')):
    dotenv.load_dotenv(os.path.join(root_dir, '.env'))
elif os.path.exists('.env'):
    dotenv.load_dotenv()

# 常规无意义/泛化学术停用词库（用于保底启发式规则和测试模式）
DEFAULT_MEANINGLESS_KEYWORDS: Set[str] = {
    # 论文结构与通用描述词
    "proposed method", "novel method", "novel approach", "proposed approach", 
    "comparative study", "case study", "ablation study", "experimental result", 
    "experimental results", "state of the art", "state-of-the-art", "evaluation metric", 
    "evaluation metrics", "performance analysis", "comprehensive review", "future work", 
    "real-world application", "real-time application", "simulation result", "simulation results", 
    "different scenarios", "various datasets", "new framework", "deep learning technique", 
    "machine learning approach", "data analysis", "extensive experiment", "extensive experiments",
    "empirical study", "numerical simulation", "benchmark dataset", "synthetic dataset",
    "comparative analysis", "methodology", "framework", "system", "algorithm", "technique", 
    "approach", "solution", "strategy", "process", "investigation", "performance", 
    "accuracy", "efficiency", "effectiveness", "robustness", "challenge", "problem",
    "high precision", "high efficiency", "good performance", "superior performance",
    "present study", "existing method", "existing methods", "traditional method", "traditional methods",
    "main contribution", "key finding", "key findings", "practical application", "practical applications",
    "overview", "survey", "review", "summary", "analysis", "result", "results", "model", "models"
}

SYSTEM_PROMPT = """你是一个专业的学术论文知识图谱与文献统计分析助手，精通计算机视觉（CV）、遥感（Remote Sensing）、人工智能（AI）、机器学习及地球科学等领域。

任务目标：
用户会提供一组从近期学术论文中提取出来的候选热门关键词列表（可能包含特定分类或领域）。
你的任务是**利用你的学术专业判断，识别并挑出其中“没有学术意义”、“过于泛化”、“无实质技术内涵”或“纯论文套话/停用词”的无意义关键词**，用于从统计图表和共现网络中排除。

【必须排除的关键词特征】（无意义 / 泛化学术词汇）：
1. 论文元词汇/套话：如 "proposed method", "novel approach", "comparative study", "case study", "experimental results", "state-of-the-art", "evaluation metric", "performance analysis", "ablation study", "extensive experiments", "future work", "real-world application" 等。
2. 缺乏具体技术或领域内涵的泛化术语：如 "framework", "methodology", "algorithm", "technique", "approach", "accuracy", "efficiency", "effectiveness", "deep learning technique", "machine learning approach", "system", "process", "solution" 等。
3. 英文虚词、无指向性的度量词或词组片段：如 "based on", "due to", "high precision", "various methods", "different scenarios", "good performance" 等。

【严禁排除的关键词特征】（必须保留的有价值技术关键词）：
1. 具体的算法/模型/网络架构：如 "YOLOv8", "Vision Transformer", "Diffusion Model", "U-Net", "CLIP", "SAM (Segment Anything)", "ResNet", "LoRA", "GNN", "NeRF", "Mamba", "CNN" 等。
2. 具体的学术任务/研究方向：如 "Change Detection", "Semantic Segmentation", "Object Detection", "Super-Resolution", "Cloud Removal", "Pan-sharpening", "Pose Estimation", "Anomaly Detection", "Hyperspectral Unmixing", "3D Reconstruction" 等。
3. 具体的传感器/数据模态/对地观测术语：如 "SAR", "InSAR", "PolSAR", "Hyperspectral", "Multispectral", "LiDAR", "Sentinel-2", "Landsat-8", "UAV Imagery", "Point Cloud", "Optical Remote Sensing" 等。
4. 具体的关键技术机制/模块：如 "Self-Attention", "Contrastive Learning", "Domain Adaptation", "Feature Pyramid", "Cross-Modal", "Prompt Tuning", "Knowledge Distillation", "Loss Function" 等。

输出格式要求：
请直接返回 JSON 格式，不要包含任何多余的 Markdown 标记或代码块外的解释，格式如下：
{
    "excluded_keywords": ["keyword1", "keyword2", ...]
}
注意：`excluded_keywords` 中的词必须与输入列表中的原词完全一致（包括拼写和大小写）。如果所有词都有实际学术意义，返回空列表 `[]`。
"""

def heuristic_filter(keywords: List[str]) -> List[str]:
    """
    启发式快速规则过滤无学术意义关键词
    """
    excluded = []
    for kw in keywords:
        kw_clean = kw.strip()
        kw_lower = kw_clean.lower()
        
        # 1. 精确匹配无意义停用词
        if kw_lower in DEFAULT_MEANINGLESS_KEYWORDS:
            excluded.append(kw_clean)
            continue
            
        # 2. 单个字符或过短非专有名词（例如单一纯数字、无意义短词）
        if len(kw_clean) <= 2 and not kw_clean.isupper():
            excluded.append(kw_clean)
            continue
            
        # 3. 常见泛化前缀/后缀模式识别
        generic_patterns = [
            r"^(novel|proposed|new|our|traditional|existing)\s+(method|approach|framework|model|algorithm|strategy|technique)s?$",
            r"^(comparative|ablation|case|empirical|experimental)\s+(study|analysis|results?|evaluation)$",
            r"^(performance|accuracy|efficiency|effectiveness|robustness)\s+(analysis|evaluation|improvement|comparison)$",
            r"^(real-time|practical|real-world|industrial)\s+(application|applications|scenario|scenarios)$",
            r"^(state-of-the-art|high-precision|high-performance|cost-effective)$"
        ]
        for pat in generic_patterns:
            if re.match(pat, kw_lower):
                excluded.append(kw_clean)
                break
                
    return excluded


def filter_meaningless_keywords(keywords: List[str], category: str = "All", model_name: Optional[str] = None) -> List[str]:
    """
    使用大模型智能识别并过滤列表中无学术意义、泛化的关键词
    
    :param keywords: 候选关键词列表
    :param category: 论文分类/领域上下文
    :param model_name: 指定模型名称（可选）
    :return: 应被排除的无意义关键词列表
    """
    if not keywords:
        return []
    
    # 限制单次检测关键词数量以防超长 token，取前 150 个
    candidate_keywords = [kw.strip() for kw in keywords if kw and kw.strip()][:150]
    if not candidate_keywords:
        return []

    # 尝试调用大模型
    try:
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            # 未配置 API Key 时走启发式规则保底
            return heuristic_filter(candidate_keywords)

        llm = init_llm(model_name)
        
        user_content = {
            "category_context": category,
            "candidate_keywords": candidate_keywords
        }
        
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=f"请分析以下候选关键词，选出无学术意义、过于泛化或套话性质的词：\n{json.dumps(user_content, ensure_ascii=False)}")
        ]
        
        response = llm.invoke(messages)
        response_text = response.content if hasattr(response, "content") else str(response)
        
        # 尝试修复并解析 JSON
        cleaned_json = repair_json(response_text)
        data = json.loads(cleaned_json)
        
        if isinstance(data, dict) and "excluded_keywords" in data and isinstance(data["excluded_keywords"], list):
            llm_excluded = set(data["excluded_keywords"])
            # 保证只返回原本就在 candidate_keywords 中的词，并与启发式高置信词结合
            heuristics = set(heuristic_filter(candidate_keywords))
            final_set = llm_excluded.union(heuristics)
            
            # 按原候选词顺序返回
            result = [kw for kw in candidate_keywords if kw in final_set]
            return result
        elif isinstance(data, list):
            llm_excluded = set(data)
            heuristics = set(heuristic_filter(candidate_keywords))
            final_set = llm_excluded.union(heuristics)
            return [kw for kw in candidate_keywords if kw in final_set]
            
    except Exception as e:
        # LLM 调用异常时（如网络超时、无额度等），自动回退到启发式规则
        pass
        
    return heuristic_filter(candidate_keywords)

import os
import re
import math
import spacy
from typing import Dict, List, Tuple, Set, Optional

# 学术元词汇与通用停用词库
STOPWORDS = {
    'method', 'based', 'towards', 'via', 'using', 'paper', 'propose', 'proposes',
    'proposed', 'approach', 'system', 'framework', 'result', 'results', 'show', 'shows',
    'demonstrated', 'demonstrates', 'demonstrate', 'experimental', 'experiment',
    'evaluation', 'performance', 'state', 'art', 'sota', 'dataset', 'task',
    'efficient', 'novel', 'modality', 'large', 'unsupervised', 'supervised',
    'semi', 'self', 'new', 'study', 'analysis', 'application', 'development',
    'design', 'process', 'technique', 'strategy', 'problem', 'challenge',
    'model', 'models', 'solution', 'algorithm', 'structure', 'architecture',
    
    'a', 'about', 'above', 'after', 'against', 'all', 'am', 'an', 'and', 'any', 'are', 'aren',
    'arent', 'as', 'at', 'be', 'because', 'been', 'before', 'being', 'below', 'between', 'both',
    'but', 'by', 'can', 'cannot', 'cant', 'could', 'couldn', 'couldnt', 'd', 'did', 'didn',
    'didnt', 'do', 'does', 'doesn', 'doesnt', 'doing', 'don', 'dont', 'down', 'during', 'each',
    'else', 'few', 'for', 'from', 'further', 'had', 'hadn', 'hadnt', 'has', 'hasn', 'hasnt',
    'have', 'haven', 'havent', 'having', 'he', 'hed', 'hell', 'hes', 'her', 'here', 'heres',
    'hers', 'herself', 'him', 'himself', 'his', 'how', 'hows', 'i', 'id', 'if', 'ill', 'im',
    'in', 'into', 'is', 'isn', 'isnt', 'it', 'its', 'itself', 'just', 'lets', 'll', 'm', 'me',
    'more', 'most', 'mustn', 'mustnt', 'my', 'myself', 'no', 'nor', 'not', 'now', 'o', 'of',
    'off', 'on', 'once', 'only', 'or', 'other', 'ought', 'our', 'ours', 'ourselves', 'out',
    'over', 'own', 're', 'same', 'shan', 'shant', 'she', 'shed', 'shell', 'shes', 'should',
    'shouldn', 'shouldnt', 'so', 'some', 'such', 't', 'than', 'that', 'thats', 'the', 'their',
    'theirs', 'them', 'themselves', 'then', 'there', 'theres', 'these', 'they', 'theyd',
    'theyll', 'theyre', 'theyve', 'this', 'those', 'through', 'to', 'too', 'under', 'until',
    'up', 've', 'very', 'was', 'wasn', 'wasnt', 'we', 'wed', 'well', 'were', 'weren', 'werent',
    'weve', 'what', 'whats', 'when', 'whens', 'where', 'wheres', 'which', 'while', 'who',
    'whos', 'whom', 'why', 'whys', 'will', 'with', 'won', 'wont', 'would', 'wouldn', 'wouldnt',
    'y', 'you', 'youd', 'youll', 'youre', 'youve', 'your', 'yours', 'yourself', 'yourselves'
}

# 常见学术专有名词大写映射表
KNOWN_ACRONYMS = {
    'llm': 'LLM',
    'llms': 'LLM',
    'large language model': 'Large Language Model',
    'large language models': 'Large Language Model',
    'vit': 'ViT',
    'vits': 'ViT',
    'vision transformer': 'Vision Transformer',
    'vision transformers': 'Vision Transformer',
    'cnn': 'CNN',
    'cnns': 'CNN',
    'convolutional neural network': 'Convolutional Neural Network',
    'convolutional neural networks': 'Convolutional Neural Network',
    'gnn': 'GNN',
    'gnns': 'GNN',
    'graph neural network': 'Graph Neural Network',
    'graph neural networks': 'Graph Neural Network',
    'nerf': 'NeRF',
    'nerfs': 'NeRF',
    'neural radiance field': 'Neural Radiance Field',
    'neural radiance fields': 'Neural Radiance Field',
    'lora': 'LoRA',
    'sam': 'SAM',
    'clip': 'CLIP',
    'yolo': 'YOLO',
    'sar': 'SAR',
    'insar': 'InSAR',
    'polsar': 'PolSAR',
    'lidar': 'LiDAR',
    'uav': 'UAV',
    'rl': 'Reinforcement Learning',
    'reinforcement learning': 'Reinforcement Learning',
    'rag': 'RAG',
    'retrieval augmented generation': 'Retrieval-Augmented Generation',
    'retrieval-augmented generation': 'Retrieval-Augmented Generation',
    'diffusion model': 'Diffusion Model',
    'diffusion models': 'Diffusion Model',
    'semantic segmentation': 'Semantic Segmentation',
    'object detection': 'Object Detection',
    'change detection': 'Change Detection',
    'super-resolution': 'Super-Resolution',
    'super resolution': 'Super-Resolution',
    'contrastive learning': 'Contrastive Learning',
    'domain adaptation': 'Domain Adaptation',
    'zero-shot': 'Zero-Shot Learning',
    'zero shot': 'Zero-Shot Learning',
    'few-shot': 'Few-Shot Learning',
    'few shot': 'Few-Shot Learning'
}

nlp = None
nlp_loaded = False

def get_nlp():
    global nlp, nlp_loaded
    if not nlp_loaded:
        try:
            import spacy
            # 仅加载分词和词性、词形还原、依存句法（用于 noun_chunks），禁用其余耗内存组件
            nlp = spacy.load("en_core_web_sm", disable=["ner", "textcat"])
            nlp.max_length = 50000
        except Exception as e:
            print(f"Failed to load spaCy model 'en_core_web_sm': {e}")
            nlp = None
        nlp_loaded = True
    return nlp

idf_cache = {}
idf_doc_count = 0


def extract_abbreviations_schwartz_hearst(text: str) -> Dict[str, str]:
    """
    基于 Schwartz-Hearst 算法的轻量级缩写抽取，在 CPU 上执行极快。
    能够准确抽取 'Large Language Model (LLM)' 或 'Vision Transformer (ViT)'。
    """
    if not text:
        return {}
        
    pairs = {}
    # 模式 1: Full Term (Short Term)
    matches_1 = re.findall(r'\b([A-Za-z][A-Za-z0-9\s-]{3,50})\s+\(([A-Za-z0-9]{2,10})\)', text)
    for long_form, short_form in matches_1:
        lf_clean = long_form.strip().lower()
        sf_clean = short_form.strip().lower()
        # 验证缩写首字母匹配
        words = [w for w in re.split(r'[\s-]+', lf_clean) if w and w not in STOPWORDS]
        if words and len(words) >= len(sf_clean):
            acronym_cand = "".join(w[0] for w in words[:len(sf_clean)])
            if acronym_cand.lower() == sf_clean.lower() or sf_clean[0].lower() == words[0][0].lower():
                pairs[sf_clean] = long_form.strip()

    # 模式 2: Short Term (Full Term)
    matches_2 = re.findall(r'\b([A-Za-z0-9]{2,10})\s+\(([A-Za-z][A-Za-z0-9\s-]{3,50})\)', text)
    for short_form, long_form in matches_2:
        lf_clean = long_form.strip().lower()
        sf_clean = short_form.strip().lower()
        words = [w for w in re.split(r'[\s-]+', lf_clean) if w and w not in STOPWORDS]
        if words and len(words) >= len(sf_clean):
            pairs[sf_clean] = long_form.strip()

    return pairs


def canonicalize_keyword(term: str, acronym_map: Optional[Dict[str, str]] = None) -> str:
    """
    将关键词归一化为标准的学术 Canonical 形式：
    1. 查表对齐已知领域专有名词与缩写；
    2. 文内缩写对齐；
    3. 规范化为统一词形并保留小写兼容性与专有大写。
    """
    clean_term = term.strip().strip("-.,;:")
    clean_lower = clean_term.lower()
    
    if not clean_term or clean_lower in STOPWORDS:
        return ""
        
    # 1. 优先查已知专有名词表
    if clean_lower in KNOWN_ACRONYMS:
        return KNOWN_ACRONYMS[clean_lower].lower()
        
    # 2. 查文内提取的缩写表
    if acronym_map and clean_lower in acronym_map:
        mapped_long = acronym_map[clean_lower]
        if mapped_long.lower() in KNOWN_ACRONYMS:
            return KNOWN_ACRONYMS[mapped_long.lower()].lower()
        return mapped_long.lower()
        
    # 3. 专有全大写缩写保留（如 CNN, SAR, UAV, GNN）
    if len(clean_term) <= 5 and clean_term.isupper():
        return clean_term.lower()
        
    return clean_lower


def extract_candidates_cvalue(
    title: str,
    summary: str = "",
    active_nlp = None,
    doc_title = None,
    doc_summary = None
) -> Tuple[Dict[str, float], Dict[str, str]]:
    """
    使用轻量级句法分析 + C-Value (Nested Term Specificity) 算法抽取高特异度复合术语与关键概念。
    专为 CPU 优化，零 GPU 依赖，内存极低。
    支持直接传入已解析好的 doc_title 与 doc_summary（支持 pipe 批处理）。
    """
    candidates_tf = {}
    raw_representations = {}
    
    # 长度安全保护：防止极端长文本导致卡死
    title = (title or "")[:500]
    summary = (summary or "")[:3000]
    
    doc_pairs = []
    if doc_title is not None or doc_summary is not None:
        if doc_title is not None and title:
            doc_pairs.append((doc_title, 3.0))
        if doc_summary is not None and summary:
            doc_pairs.append((doc_summary, 1.0))
    elif active_nlp is not None:
        if title:
            try:
                doc_pairs.append((active_nlp(title), 3.0))
            except Exception:
                pass
        if summary:
            try:
                doc_pairs.append((active_nlp(summary), 1.0))
            except Exception:
                pass
    
    for doc, weight in doc_pairs:
        if doc is None:
            continue
        try:
            # 1. 抽取名词块与复合学术术语
            for chunk in doc.noun_chunks:
                tokens = []
                for t in chunk:
                    if t.pos_ in ["NOUN", "PROPN", "ADJ"] and not t.is_stop and t.text.lower() not in STOPWORDS and len(t.text) > 1:
                        tokens.append(t)
                        
                if not tokens:
                    continue
                    
                for length in range(1, min(4, len(tokens) + 1)):
                    for i in range(len(tokens) - length + 1):
                        sub_tokens = tokens[i:i+length]
                        lemma_phrase = " ".join(t.text.lower() for t in sub_tokens)
                        raw_phrase = " ".join(t.text for t in sub_tokens)
                        
                        if lemma_phrase not in STOPWORDS:
                            candidates_tf[lemma_phrase] = candidates_tf.get(lemma_phrase, 0.0) + weight
                            if lemma_phrase not in raw_representations:
                                raw_representations[lemma_phrase] = raw_phrase

            # 2. 单独扫描关键名词与专有名词（确保基础核心词不遗漏）
            for t in doc:
                if t.pos_ in ["NOUN", "PROPN", "ADJ"] and not t.is_stop and t.text.lower() not in STOPWORDS and len(t.text) > 1:
                    w_lower = t.text.lower()
                    candidates_tf[w_lower] = candidates_tf.get(w_lower, 0.0) + weight
                    if w_lower not in raw_representations:
                        raw_representations[w_lower] = t.text
        except Exception:
            pass
            
    # C-Value 嵌套扣减计算
    # C-Value(a) = log2(|a|) * (TF(a) - 1/|T_a| * sum_{b in T_a} TF(b))
    # 限制参与双层嵌套计算的最多前 120 个候选词，避免长尾词进行 O(N^2) 耗时计算
    sorted_all_terms = sorted(candidates_tf.keys(), key=lambda x: len(x.split()), reverse=True)
    sorted_terms = sorted_all_terms[:120]
    cvalue_scores = {}
    nested_parent_map = {}
    
    for i, long_term in enumerate(sorted_terms):
        long_words = long_term.split()
        for short_term in sorted_terms[i+1:]:
            short_words = short_term.split()
            if len(short_words) < len(long_words):
                for k in range(len(long_words) - len(short_words) + 1):
                    if long_words[k:k+len(short_words)] == short_words:
                        nested_parent_map.setdefault(short_term, []).append(long_term)
                        break
                        
    for term, tf in candidates_tf.items():
        words_len = len(term.split())
        log_len = math.log2(words_len + 1)
        parents = nested_parent_map.get(term, [])
        if parents:
            avg_parent_tf = sum(candidates_tf[p] for p in parents) / len(parents)
            nested_tf = max(0.1, tf - avg_parent_tf * 0.6)
            cvalue_scores[term] = log_len * nested_tf
        else:
            cvalue_scores[term] = log_len * tf
            
    return cvalue_scores, raw_representations


def _process_candidate_scores_to_keywords(
    candidates_scores: dict,
    raw_map: dict,
    full_text: str,
    acronym_map: dict,
    active_idf: dict,
    default_idf: float
) -> list:
    """内部通用函数：将候选打分、缩写与 IDF 加权聚合成最终 Top 10 关键词。"""
    # Fallback 快速规则抽取
    if not candidates_scores:
        cleaned = re.sub(r"[^\w\s-]", " ", full_text.lower())
        words = [w.strip("-_") for w in cleaned.split() if w.strip("-_") and w.strip("-_") not in STOPWORDS and len(w.strip("-_")) > 1]
        for i in range(len(words)):
            for l in range(1, min(4, len(words) - i + 1)):
                phrase = " ".join(words[i:i+l])
                candidates_scores[phrase] = candidates_scores.get(phrase, 0.0) + (3.0 if phrase in full_text.lower() else 1.0)
                raw_map[phrase] = phrase

    # 结合全局 IDF 加权与实体规范化 (Canonical Normalization)
    canonical_aggregated = {}
    for term, base_score in candidates_scores.items():
        raw_repr = raw_map.get(term, term)
        canonical = canonicalize_keyword(raw_repr, acronym_map)
        if not canonical or len(canonical) <= 1 or canonical.lower() in STOPWORDS:
            continue
            
        # IDF 加权
        words_list = term.split()
        idf_val = sum(active_idf.get(w, default_idf) for w in words_list) / max(1, len(words_list))
        final_score = base_score * idf_val
        
        # 归一化实体聚合累加
        canonical_aggregated[canonical] = canonical_aggregated.get(canonical, 0.0) + final_score

    # 排序输出 Top 10
    result = sorted(canonical_aggregated.items(), key=lambda x: x[1], reverse=True)
    return result[:10]


def extract_keywords(title: str, summary: str = "", idf_map: dict = None) -> list:
    """
    提取单篇论文的高质量关键词（兼具准确性、语义归一化与极速 CPU 推理）。
    
    :param title: 论文标题
    :param summary: 论文摘要
    :param idf_map: 逆文档频率映射
    :return: 归一化后的 Top 关键词列表 [(canonical_keyword, score), ...]
    """
    title = (title or "")[:500]
    summary = (summary or "")[:3000]
    full_text = f"{title} {summary}"
    
    global idf_cache, idf_doc_count
    active_idf = idf_map if idf_map is not None else idf_cache
    default_idf = math.log((1 + idf_doc_count) / 2) + 1 if idf_doc_count > 0 else 1.0
    
    # 1. 抽取文内缩写对齐表
    acronym_map = extract_abbreviations_schwartz_hearst(full_text)
    
    active_nlp = get_nlp()
    candidates_scores = {}
    raw_map = {}
    
    if active_nlp is not None:
        try:
            candidates_scores, raw_map = extract_candidates_cvalue(title, summary, active_nlp=active_nlp)
        except Exception:
            candidates_scores, raw_map = {}, {}
            
    return _process_candidate_scores_to_keywords(
        candidates_scores=candidates_scores,
        raw_map=raw_map,
        full_text=full_text,
        acronym_map=acronym_map,
        active_idf=active_idf,
        default_idf=default_idf
    )


def extract_keywords_batch(
    papers: List[Dict[str, str]],
    idf_map: dict = None,
    batch_size: int = 50
) -> List[List[Tuple[str, float]]]:
    """
    高效批量提取多篇论文关键词。
    利用 spaCy 的 nlp.pipe 批处理进行向量化分词与句法解析，大幅降低 CPU 负载并防止内存暴涨。
    
    :param papers: 包含 [{'title': ..., 'summary': ...}, ...] 的论文列表
    :param idf_map: 逆文档频率映射
    :param batch_size: 批处理大小（默认 50）
    :return: 每篇论文对应的关键词列表
    """
    if not papers:
        return []
        
    global idf_cache, idf_doc_count
    active_idf = idf_map if idf_map is not None else idf_cache
    default_idf = math.log((1 + idf_doc_count) / 2) + 1 if idf_doc_count > 0 else 1.0
    
    active_nlp = get_nlp()
    
    # 收集需要进行 NLP 处理的文本
    # 每篇论文生成 2 个文本：title 和 summary
    flat_texts = []
    clean_papers = []
    for p in papers:
        t = (p.get("title", "") or "")[:500]
        s = (p.get("summary", "") or "")[:3000]
        clean_papers.append((t, s))
        flat_texts.append(t if t else "")
        flat_texts.append(s if s else "")
        
    docs_list = []
    if active_nlp is not None:
        try:
            # 使用 nlp.pipe 批量并行解析
            docs_list = list(active_nlp.pipe(flat_texts, batch_size=batch_size * 2, n_process=1))
        except Exception as e:
            print(f"Error during nlp.pipe in extract_keywords_batch: {e}")
            docs_list = [None] * len(flat_texts)
    else:
        docs_list = [None] * len(flat_texts)
        
    results = []
    for idx, (t, s) in enumerate(clean_papers):
        full_text = f"{t} {s}"
        acronym_map = extract_abbreviations_schwartz_hearst(full_text)
        
        doc_t = docs_list[idx * 2] if idx * 2 < len(docs_list) else None
        doc_s = docs_list[idx * 2 + 1] if idx * 2 + 1 < len(docs_list) else None
        
        try:
            candidates_scores, raw_map = extract_candidates_cvalue(
                title=t,
                summary=s,
                active_nlp=None,
                doc_title=doc_t,
                doc_summary=doc_s
            )
        except Exception:
            candidates_scores, raw_map = {}, {}
            
        kws = _process_candidate_scores_to_keywords(
            candidates_scores=candidates_scores,
            raw_map=raw_map,
            full_text=full_text,
            acronym_map=acronym_map,
            active_idf=active_idf,
            default_idf=default_idf
        )
        results.append(kws)
        
    return results

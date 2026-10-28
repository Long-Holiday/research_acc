def extract_keywords_batch(papers, batch_size=50):
    """读取 AI 增强结果的关键词，单篇归一去重后每个词计一次；不补提取历史数据。"""
    results = []
    for paper in papers:
        ai = paper.get("AI")
        values = ai.get("keywords", []) if isinstance(ai, dict) else []
        unique = {}
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str):
                    keyword = " ".join(value.split()).lower()
                    if keyword:
                        unique[keyword] = 1
        results.append(list(unique.items()))
    return results

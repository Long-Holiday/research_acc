import os
import sys
import json
import argparse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Ensure the current directory is in sys.path for local package resolution
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load local environment variables if .env exists
load_dotenv()

# 清理环境变量中的空白符和换行符，防止因 \r 导致解析报错
for k in os.environ:
    os.environ[k] = os.environ[k].strip()

from daily_journals import (
    JOURNALS,
    fetch_crossref_papers,
    fetch_openalex_details_by_dois,
    fetch_openalex_single_detail,
    fetch_openalex_papers,
    fetch_comprehensive_abstract,
    fetch_arxiv_abstract,
    find_arxiv_url,
    reconstruct_abstract
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None, help="The base date (today) in YYYY-MM-DD format. Yesterday of this date will be queried.")
    parser.add_argument("--from-date", type=str, default=None, help="Explicit start publication date in YYYY-MM-DD")
    parser.add_argument("--to-date", type=str, default=None, help="Explicit end publication date in YYYY-MM-DD")
    parser.add_argument("--output", type=str, required=True, help="Path to the output JSONL file to append")
    args = parser.parse_args()
    
    if args.from_date and args.to_date:
        from_date = args.from_date
        to_date = args.to_date
    else:
        if args.date:
            today_dt = datetime.strptime(args.date, "%Y-%m-%d")
        else:
            today_dt = datetime.now(ZoneInfo("UTC"))
        yesterday_dt = today_dt - timedelta(days=1)
        yesterday_str = yesterday_dt.strftime("%Y-%m-%d")
        from_dt = today_dt - timedelta(days=7)
        from_date = from_dt.strftime("%Y-%m-%d")
        to_date = yesterday_str
        
    print(f"Target publication date range: {from_date} to {to_date}", file=sys.stderr)

    api_key = os.environ.get("OPENALEX_API_KEY", "")
    if api_key:
        print(f"Using OpenAlex API key: {api_key[:4]}...{api_key[-4:]}", file=sys.stderr)
    else:
        print("No OPENALEX_API_KEY set, using anonymous access", file=sys.stderr)
    
    total_new_papers = 0
    formatted_papers = []
    
    for journal in JOURNALS:
        print(f"Processing journal {journal['name']} ({journal['category']})...", file=sys.stderr)
        
        # 1. 先用 Crossref 圈定目录
        crossref_list = fetch_crossref_papers(journal["issns"], from_date, to_date)
        print(f"Crossref found {len(crossref_list)} papers for {journal['name']}.", file=sys.stderr)
        
        raw_papers = []
        # 用作对 OpenAlex 返回详情进行匹配的临时变量
        oa_details = {}
        
        if crossref_list:
            # 2. 用 OpenAlex 批量获取详情
            dois_only = [item["doi"] for item in crossref_list]
            oa_details = fetch_openalex_details_by_dois(dois_only)
            print(f"OpenAlex batched details retrieved: {len(oa_details)}", file=sys.stderr)
        else:
            # 3. 兜底逻辑：如果 Crossref 查出来是 0 篇（可能 Crossref 挂了或查漏了），使用原先 OpenAlex ISSN 检索
            print(f"WARNING: Crossref returned 0 papers. Falling back to direct OpenAlex ISSN query...", file=sys.stderr)
            fallback_papers = fetch_openalex_papers(journal["issns"], from_date, to_date)
            print(f"Direct OpenAlex ISSN query found {len(fallback_papers)} papers.", file=sys.stderr)
            raw_papers = fallback_papers

        abstract_stats = {
            "openalex": 0,
            "semantic_scholar": 0,
            "arxiv_link": 0,
            "arxiv_title": 0,
            "crossref": 0,
            "europe_pmc": 0,
            "missing": 0
        }
        
        # 处理第一种情况：Crossref 有 DOI，使用 OpenAlex 补充详情，并实现多级补漏
        if crossref_list:
            for crossref_item in crossref_list:
                doi = crossref_item["doi"]
                paper_detail = oa_details.get(doi)
                
                # 补漏情况：批量没查到，尝试单篇 OpenAlex 查一下
                if not paper_detail:
                    print(f"DOI {doi} not found in OpenAlex batch, attempting single query...", file=sys.stderr)
                    paper_detail = fetch_openalex_single_detail(doi, api_key)
                    if paper_detail:
                        print(f"Successfully fetched DOI {doi} individually from OpenAlex.", file=sys.stderr)
                
                # 整合数据
                if paper_detail:
                    openalex_id = paper_detail.get("id", "").split("/")[-1] or doi.replace("/", "_")
                    title = paper_detail.get("title") or paper_detail.get("display_name") or crossref_item["title"]
                    
                    # 提取作者
                    authors = []
                    for authorship in paper_detail.get("authorships", []):
                        author_name = authorship.get("author", {}).get("display_name")
                        if author_name:
                            authors.append(author_name)
                    if not authors:
                        authors = crossref_item["authors"]
                        
                    # 尝试从 OpenAlex 关联的 arXiv 链接拉取
                    arxiv_link_summary = ""
                    arxiv_url = find_arxiv_url(paper_detail)
                    if arxiv_url:
                        arxiv_link_summary = fetch_arxiv_abstract(arxiv_url) or ""
                    
                    # 统一多级摘要获取
                    summary, source_tag = fetch_comprehensive_abstract(
                        doi=doi,
                        title=title,
                        openalex_inverted_index=paper_detail.get("abstract_inverted_index"),
                        crossref_abstract=crossref_item.get("abstract", ""),
                        arxiv_abstract=arxiv_link_summary
                    )
                    abstract_stats[source_tag] = abstract_stats.get(source_tag, 0) + 1
                        
                    abs_url = paper_detail.get("doi") or paper_detail.get("primary_location", {}).get("landing_page_url") or f"https://openalex.org/{openalex_id}"
                    pdf_url = paper_detail.get("primary_location", {}).get("pdf_url") or paper_detail.get("open_access", {}).get("oa_url") or abs_url
                    
                    concepts = []
                    for concept in paper_detail.get("concepts", []):
                        if concept.get("display_name") and concept.get("score", 0) > 0.3:
                            concepts.append(concept.get("display_name"))
                    cited_by_count = paper_detail.get("cited_by_count", 0)
                else:
                    # 彻底查不到 OpenAlex 详情：使用 Crossref 元数据 + S2/arXiv/EuropePMC 多级兜底
                    print(f"WARNING: DOI {doi} not found in OpenAlex. Using multi-source fallback.", file=sys.stderr)
                    openalex_id = doi.replace("/", "_")
                    title = crossref_item["title"]
                    authors = crossref_item["authors"]
                    
                    summary, source_tag = fetch_comprehensive_abstract(
                        doi=doi,
                        title=title,
                        crossref_abstract=crossref_item.get("abstract", "")
                    )
                    abstract_stats[source_tag] = abstract_stats.get(source_tag, 0) + 1
                        
                    abs_url = f"https://doi.org/{doi}"
                    pdf_url = abs_url
                    concepts = []
                    cited_by_count = 0
                
                item = {
                    "id": openalex_id,
                    "title": title,
                    "authors": authors,
                    "categories": [journal["category"]],
                    "comment": "",
                    "summary": summary,
                    "abs": abs_url,
                    "pdf": pdf_url,
                    "cited_by_count": cited_by_count,
                    "concepts": concepts[:5]
                }
                
                formatted_papers.append(item)
                total_new_papers += 1
        
        # 处理第二种情况（兜底）：Crossref 没抓到任何东西，退化为旧版的 OpenAlex 数据处理
        else:
            for paper in raw_papers:
                openalex_id = paper.get("id", "").split("/")[-1]
                if not openalex_id:
                    continue
                    
                title = paper.get("title") or paper.get("display_name") or "No Title"
                raw_doi = (paper.get("doi") or "").replace("https://doi.org/", "").strip()
                
                # 作者提取
                authors = []
                for authorship in paper.get("authorships", []):
                    author_name = authorship.get("author", {}).get("display_name")
                    if author_name:
                        authors.append(author_name)
                if not authors:
                    authors = ["Unknown Author"]
                    
                # 尝试从 OpenAlex 关联的 arXiv 链接拉取
                arxiv_link_summary = ""
                arxiv_url = find_arxiv_url(paper)
                if arxiv_url:
                    arxiv_link_summary = fetch_arxiv_abstract(arxiv_url) or ""
                
                summary, source_tag = fetch_comprehensive_abstract(
                    doi=raw_doi,
                    title=title,
                    openalex_inverted_index=paper.get("abstract_inverted_index"),
                    arxiv_abstract=arxiv_link_summary
                )
                abstract_stats[source_tag] = abstract_stats.get(source_tag, 0) + 1
                
                abs_url = paper.get("doi") or paper.get("primary_location", {}).get("landing_page_url") or f"https://openalex.org/{openalex_id}"
                pdf_url = paper.get("primary_location", {}).get("pdf_url") or paper.get("open_access", {}).get("oa_url") or abs_url
                
                concepts = []
                for concept in paper.get("concepts", []):
                    if concept.get("display_name") and concept.get("score", 0) > 0.3:
                        concepts.append(concept.get("display_name"))

                item = {
                    "id": openalex_id,
                    "title": title,
                    "authors": authors,
                    "categories": [journal["category"]],
                    "comment": "",
                    "summary": summary,
                    "abs": abs_url,
                    "pdf": pdf_url,
                    "cited_by_count": paper.get("cited_by_count", 0),
                    "concepts": concepts[:5]
                }
                
                formatted_papers.append(item)
                total_new_papers += 1
                
        print(f"  Abstract stats for {journal['name']}: {abstract_stats}", file=sys.stderr)

    # --- 跨日期全局去重：防止 7 天滑动窗口导致的不同日期重复 ---
    def _load_existing_ids_for_dedup(output_path: str) -> set:
        """加载已存在的论文标识（id + doi）用于去重，覆盖输出文件自身及历史所有 data/*.jsonl"""
        seen = set()
        # 1) 输出文件自身（同文件内去重，支持断点续写场景）
        if os.path.exists(output_path):
            try:
                with open(output_path, "r", encoding="utf-8") as rf:
                    for line in rf:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            j = json.loads(line)
                        except Exception:
                            continue
                        pid = str(j.get("id", "")).lower().strip()
                        if pid:
                            seen.add(pid)
                        abs_url = j.get("abs") or ""
                        if "doi.org/" in abs_url:
                            doi_part = abs_url.split("doi.org/")[-1].lower().strip().split("?")[0].split("#")[0].strip("/")
                            if doi_part:
                                seen.add(doi_part)
            except Exception as e:
                print(f"Warning: failed to load existing ids from {output_path}: {e}", file=sys.stderr)
        # 2) 历史所有原始文件（data/*.jsonl，排除 AI 增强文件）
        #    采用全局去重而非仅 7 天，避免 cron 偶发漏跑或手动补录导致的跨周重复
        data_dir = os.path.dirname(os.path.abspath(output_path)) if os.path.dirname(output_path) else "."
        # 若 output_path 形如 ../data/2026-08-12.jsonl，需解析为绝对目录
        if not os.path.isdir(data_dir):
            data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
            data_dir = os.path.normpath(data_dir)
        if os.path.isdir(data_dir):
            import glob as _glob
            for hist_path in _glob.glob(os.path.join(data_dir, "*.jsonl")):
                # 跳过 AI 增强文件与本次输出文件本身
                if "_AI_enhanced_" in hist_path:
                    continue
                if os.path.abspath(hist_path) == os.path.abspath(output_path):
                    continue
                try:
                    with open(hist_path, "r", encoding="utf-8") as rf:
                        for line in rf:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                j = json.loads(line)
                            except Exception:
                                continue
                            pid = str(j.get("id", "")).lower().strip()
                            if pid:
                                seen.add(pid)
                            abs_url = j.get("abs") or ""
                            if "doi.org/" in abs_url:
                                doi_part = abs_url.split("doi.org/")[-1].lower().strip().split("?")[0].split("#")[0].strip("/")
                                if doi_part:
                                    seen.add(doi_part)
                except Exception:
                    continue
        return seen

    # 执行去重过滤
    if formatted_papers:
        existing_ids = _load_existing_ids_for_dedup(args.output)
        deduped = []
        seen_in_batch = set()
        duplicate_in_batch = 0
        duplicate_cross_date = 0
        for p in formatted_papers:
            pid = str(p.get("id", "")).lower().strip()
            doi_part = ""
            abs_url = p.get("abs") or ""
            if "doi.org/" in abs_url:
                doi_part = abs_url.split("doi.org/")[-1].lower().strip().split("?")[0].split("#")[0].strip("/")
            # 同批次内去重
            if pid and pid in seen_in_batch:
                duplicate_in_batch += 1
                continue
            if doi_part and doi_part in seen_in_batch:
                duplicate_in_batch += 1
                continue
            # 跨日期/跨文件去重
            if (pid and pid in existing_ids) or (doi_part and doi_part in existing_ids):
                duplicate_cross_date += 1
                continue
            deduped.append(p)
            if pid:
                seen_in_batch.add(pid)
                existing_ids.add(pid)
            if doi_part:
                seen_in_batch.add(doi_part)
                existing_ids.add(doi_part)
        if duplicate_in_batch or duplicate_cross_date:
            print(f"Deduplication: filtered {duplicate_in_batch} intra-batch duplicates and {duplicate_cross_date} cross-date duplicates; {len(deduped)}/{len(formatted_papers)} papers remain.", file=sys.stderr)
        formatted_papers = deduped

    if formatted_papers:
        output_dir = os.path.dirname(args.output)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
            
        with open(args.output, "a", encoding="utf-8") as f:
            for paper in formatted_papers:
                f.write(json.dumps(paper, ensure_ascii=False) + "\n")
        print(f"Successfully appended {len(formatted_papers)} papers to {args.output}")
    else:
        print("No papers found to append.")

if __name__ == "__main__":
    main()

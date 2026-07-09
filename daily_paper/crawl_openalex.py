import os
import sys
import json
import argparse
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# Ensure the current directory is in sys.path for local package resolution
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load local environment variables if .env exists
load_dotenv()

from daily_journals import (
    JOURNALS,
    fetch_crossref_papers,
    fetch_openalex_details_by_dois,
    fetch_openalex_single_detail,
    fetch_openalex_papers,
    reconstruct_abstract,
    fetch_arxiv_abstract,
    find_arxiv_url
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
            today_dt = datetime.now(timezone.utc)
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

        abstract_ok = 0
        abstract_fallback_crossref = 0
        abstract_fallback_arxiv = 0
        abstract_missing = 0
        
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
                        
                    # 还原摘要
                    summary = reconstruct_abstract(paper_detail.get("abstract_inverted_index"))
                    
                    # 摘要多级补漏
                    if summary == "No abstract available in OpenAlex." or not summary:
                        # 1. 尝试从 arXiv 补充
                        arxiv_url = find_arxiv_url(paper_detail)
                        if arxiv_url:
                            arxiv_summary = fetch_arxiv_abstract(arxiv_url)
                            if arxiv_summary:
                                summary = arxiv_summary
                                abstract_fallback_arxiv += 1
                        
                        # 2. 如果 arXiv 依然没有，且 Crossref 有摘要，则使用 Crossref 摘要
                        if (summary == "No abstract available in OpenAlex." or not summary) and crossref_item["abstract"]:
                            summary = crossref_item["abstract"]
                            abstract_fallback_crossref += 1
                    else:
                        abstract_ok += 1
                        
                    abs_url = paper_detail.get("doi") or paper_detail.get("primary_location", {}).get("landing_page_url") or f"https://openalex.org/{openalex_id}"
                    pdf_url = paper_detail.get("primary_location", {}).get("pdf_url") or paper_detail.get("open_access", {}).get("oa_url") or abs_url
                else:
                    # 彻底查不到 OpenAlex 详情：使用 Crossref 元数据兜底补漏
                    print(f"WARNING: DOI {doi} not found in OpenAlex. Using Crossref metadata fallback.", file=sys.stderr)
                    openalex_id = doi.replace("/", "_")
                    title = crossref_item["title"]
                    authors = crossref_item["authors"]
                    summary = crossref_item["abstract"]
                    
                    if summary:
                        abstract_fallback_crossref += 1
                    else:
                        summary = "No abstract available."
                        abstract_missing += 1
                        
                    abs_url = f"https://doi.org/{doi}"
                    pdf_url = abs_url
                
                item = {
                    "id": openalex_id,
                    "title": title,
                    "authors": authors,
                    "categories": [journal["category"]],
                    "comment": "",
                    "summary": summary,
                    "abs": abs_url,
                    "pdf": pdf_url
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
                
                # 作者提取
                authors = []
                for authorship in paper.get("authorships", []):
                    author_name = authorship.get("author", {}).get("display_name")
                    if author_name:
                        authors.append(author_name)
                if not authors:
                    authors = ["Unknown Author"]
                    
                # 还原摘要
                summary = reconstruct_abstract(paper.get("abstract_inverted_index"))
                if summary == "No abstract available in OpenAlex.":
                    arxiv_url = find_arxiv_url(paper)
                    if arxiv_url:
                        arxiv_summary = fetch_arxiv_abstract(arxiv_url)
                        if arxiv_summary:
                            summary = arxiv_summary
                            abstract_fallback_arxiv += 1
                        else:
                            abstract_missing += 1
                    else:
                        abstract_missing += 1
                else:
                    abstract_ok += 1
                
                abs_url = paper.get("doi") or paper.get("primary_location", {}).get("landing_page_url") or f"https://openalex.org/{openalex_id}"
                pdf_url = paper.get("primary_location", {}).get("pdf_url") or paper.get("open_access", {}).get("oa_url") or abs_url
                
                item = {
                    "id": openalex_id,
                    "title": title,
                    "authors": authors,
                    "categories": [journal["category"]],
                    "comment": "",
                    "summary": summary,
                    "abs": abs_url,
                    "pdf": pdf_url
                }
                
                formatted_papers.append(item)
                total_new_papers += 1
                
        print(f"  Abstract stats: {abstract_ok} from OpenAlex, {abstract_fallback_crossref} from Crossref fallback, {abstract_fallback_arxiv} from arXiv fallback, {abstract_missing} missing", file=sys.stderr)

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

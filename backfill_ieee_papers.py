#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
独立补录脚本：补录本地数据中缺失的 IEEE 期刊论文（TGRS、JSTARS、GRSL）并执行 AI 增强处理。
Independent Backfill Script: Backfill missing IEEE journal papers (TGRS, JSTARS, GRSL) 
and perform AI enhancement for local JSONL files.
"""

import os
import sys
import glob
import json
import re
import argparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Set, Optional
from tqdm import tqdm

# 将项目根目录与 daily_paper 加入 sys.path 以复用模块
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "daily_paper"))

import dotenv
dotenv.load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# 清理环境变量空白符
for k in os.environ:
    os.environ[k] = os.environ[k].strip()

from daily_paper.daily_journals import (
    JOURNALS,
    fetch_crossref_papers,
    fetch_openalex_details_by_dois,
    fetch_openalex_single_detail,
    fetch_openalex_papers,
    reconstruct_abstract,
    fetch_arxiv_abstract,
    find_arxiv_url
)

# 目标补录期刊配置
IEEE_JOURNAL_TARGETS = [
    {
        "name": "TGRS",
        "issns": ["0196-2892", "1558-0644"],
        "category": "IEEE TGRS"
    },
    {
        "name": "JSTARS",
        "issns": ["1939-1404", "2151-1535"],
        "category": "IEEE JSTARS"
    },
    {
        "name": "GRSL",
        "issns": ["1545-598X", "1558-0571"],
        "category": "IEEE GRSL"
    }
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="补录本地数据中缺失的 IEEE 期刊（TGRS、JSTARS、GRSL）论文并进行 AI 增强"
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="指定补录的单个日期 (格式: YYYY-MM-DD)，若不指定则默认扫描 data/ 下所有已有日期"
    )
    parser.add_argument(
        "--from-date",
        type=str,
        default=None,
        help="显式指定查询开始日期 (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--to-date",
        type=str,
        default=None,
        help="显式指定查询结束日期 (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=2,
        help="AI 处理的最大并发线程数 (默认: 2)"
    )
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="AI 增强目标语言 (默认读取环境变量 LANGUAGE 或 Chinese)"
    )
    parser.add_argument(
        "--model-name",
        type=str,
        default=None,
        help="AI 模型名称 (默认读取环境变量 MODEL_NAME 或 deepseek-v4-flash)"
    )
    parser.add_argument(
        "--skip-ai",
        action="store_true",
        help="仅抓取补录原始论文，跳过 AI 增强处理"
    )
    parser.add_argument(
        "--all-journals",
        action="store_true",
        help="补录 constants.py 中的全部 15 本期刊，而不仅限于 IEEE 期刊"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行模式：仅统计缺失并打印，不实际写入文件"
    )
    return parser.parse_args()


def get_existing_dates() -> List[str]:
    """获取 data/ 目录下所有已存在的日期列表"""
    data_dir = os.path.join(PROJECT_ROOT, "data")
    if not os.path.exists(data_dir):
        return []
    
    dates = set()
    for fname in os.listdir(data_dir):
        # 匹配 YYYY-MM-DD.jsonl
        match = re.match(r"^(\d{4}-\d{2}-\d{2})\.jsonl$", fname)
        if match:
            dates.add(match.group(1))
    return sorted(list(dates))


def load_existing_ids(filepath: str) -> Set[str]:
    """读取已有 JSONL 文件中的所有论文 ID 和 DOI 标识"""
    seen_ids = set()
    if not os.path.exists(filepath):
        return seen_ids
        
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                pid = item.get("id")
                if pid:
                    seen_ids.add(str(pid).lower().strip())
                # 同时也记录 DOI 去重
                abs_url = item.get("abs") or ""
                if "doi.org/" in abs_url:
                    doi_part = abs_url.split("doi.org/")[-1].lower().strip()
                    seen_ids.add(doi_part)
            except Exception:
                continue
    return seen_ids


def fetch_and_format_journal_papers(
    journal: Dict,
    from_date: str,
    to_date: str,
    api_key: str = ""
) -> List[Dict]:
    """抓取并规范化指定期刊在指定时间范围内的论文元数据"""
    crossref_list = fetch_crossref_papers(journal["issns"], from_date, to_date)
    
    formatted_papers = []
    oa_details = {}
    
    if crossref_list:
        dois_only = [item["doi"] for item in crossref_list]
        oa_details = fetch_openalex_details_by_dois(dois_only)
    else:
        fallback_papers = fetch_openalex_papers(journal["issns"], from_date, to_date)
        for paper in fallback_papers:
            openalex_id = paper.get("id", "").split("/")[-1]
            if not openalex_id:
                continue
            title = paper.get("title") or paper.get("display_name") or "No Title"
            authors = []
            for authorship in paper.get("authorships", []):
                author_name = authorship.get("author", {}).get("display_name")
                if author_name:
                    authors.append(author_name)
            if not authors:
                authors = ["Unknown Author"]
                
            summary = reconstruct_abstract(paper.get("abstract_inverted_index"))
            if summary == "No abstract available in OpenAlex." or not summary:
                arxiv_url = find_arxiv_url(paper)
                if arxiv_url:
                    arxiv_summary = fetch_arxiv_abstract(arxiv_url)
                    if arxiv_summary:
                        summary = arxiv_summary
                        
            abs_url = paper.get("doi") or paper.get("primary_location", {}).get("landing_page_url") or f"https://openalex.org/{openalex_id}"
            pdf_url = paper.get("primary_location", {}).get("pdf_url") or paper.get("open_access", {}).get("oa_url") or abs_url
            
            concepts = []
            for concept in paper.get("concepts", []):
                if concept.get("display_name") and concept.get("score", 0) > 0.3:
                    concepts.append(concept.get("display_name"))
                    
            formatted_papers.append({
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
            })
        return formatted_papers

    # 对 Crossref 检索结果进一步装配
    for crossref_item in crossref_list:
        doi = crossref_item["doi"]
        paper_detail = oa_details.get(doi)
        
        if not paper_detail:
            paper_detail = fetch_openalex_single_detail(doi, api_key)
            
        if paper_detail:
            openalex_id = paper_detail.get("id", "").split("/")[-1] or doi.replace("/", "_")
            title = paper_detail.get("title") or paper_detail.get("display_name") or crossref_item["title"]
            authors = []
            for authorship in paper_detail.get("authorships", []):
                author_name = authorship.get("author", {}).get("display_name")
                if author_name:
                    authors.append(author_name)
            if not authors:
                authors = crossref_item["authors"]
                
            summary = reconstruct_abstract(paper_detail.get("abstract_inverted_index"))
            if summary == "No abstract available in OpenAlex." or not summary:
                arxiv_url = find_arxiv_url(paper_detail)
                if arxiv_url:
                    arxiv_summary = fetch_arxiv_abstract(arxiv_url)
                    if arxiv_summary:
                        summary = arxiv_summary
                        
                if (summary == "No abstract available in OpenAlex." or not summary) and crossref_item["abstract"]:
                    summary = crossref_item["abstract"]
                    
            abs_url = paper_detail.get("doi") or paper_detail.get("primary_location", {}).get("landing_page_url") or f"https://openalex.org/{openalex_id}"
            pdf_url = paper_detail.get("primary_location", {}).get("pdf_url") or paper_detail.get("open_access", {}).get("oa_url") or abs_url
            
            concepts = []
            for concept in paper_detail.get("concepts", []):
                if concept.get("display_name") and concept.get("score", 0) > 0.3:
                    concepts.append(concept.get("display_name"))
            cited_by_count = paper_detail.get("cited_by_count", 0)
        else:
            openalex_id = doi.replace("/", "_")
            title = crossref_item["title"]
            authors = crossref_item["authors"]
            summary = crossref_item["abstract"] or "No abstract available."
            abs_url = f"https://doi.org/{doi}"
            pdf_url = abs_url
            concepts = []
            cited_by_count = 0

        formatted_papers.append({
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
        })

    return formatted_papers


def enhance_papers_with_ai(
    papers: List[Dict],
    model_name: str,
    language: str,
    max_workers: int
) -> List[Dict]:
    """对论文列表进行并发 AI 结构化总结增强"""
    if not papers:
        return []
        
    try:
        from ai.enhance import build_chain, process_single_item
    except ImportError:
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "ai"))
        from enhance import build_chain, process_single_item

    chain = build_chain(model_name)
    enhanced_results = []
    
    print(f"正在对 {len(papers)} 篇补录论文进行 AI 增强处理 (模型: {model_name}, 线程数: {max_workers})...")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(process_single_item, chain, dict(paper), language)
            for paper in papers
        ]
        
        for future in tqdm(as_completed(futures), total=len(futures), desc="AI 增强进度"):
            try:
                res = future.result()
                if res is not None:
                    enhanced_results.append(res)
            except Exception as e:
                print(f"AI 处理单篇论文异常: {e}", file=sys.stderr)
                
    return enhanced_results


def sync_database_stats(modified_dates: List[str] = None):
    """补录完成后自动提取关键词并同步更新 statistics.db 本地数据库"""
    try:
        from server_modules.processor import reextract_all_keywords, scan_and_process_files
        print("正在为所有补录论文提取关键词并同步 statistics.db 数据库图谱与统计数据...")
        success = reextract_all_keywords()
        if success:
            print("✅ 关键词提取与本地统计数据库同步已全部完成！")
        else:
            print("⚠️ 重新提取关键词时遇到异常，正在尝试普通扫描同步...")
            scan_and_process_files()
    except Exception as e:
        print(f"⚠️ 同步统计数据库时跳过或遇到警告: {e}")



def main():
    args = parse_args()
    
    model_name = args.model_name or os.environ.get("MODEL_NAME", "deepseek-v4-flash")
    language = args.language or os.environ.get("LANGUAGE", "Chinese")
    api_key = os.environ.get("OPENALEX_API_KEY", "")
    
    journals_to_crawl = JOURNALS if args.all_journals else IEEE_JOURNAL_TARGETS
    journal_names = [j["name"] for j in journals_to_crawl]
    
    print("=" * 70)
    print("🚀 IEEE / 遥感期刊论文补录与 AI 增强工具")
    print(f"🎯 目标期刊: {', '.join(journal_names)}")
    print(f"🤖 AI 模型: {model_name} | 目标语言: {language} | 并发数: {args.max_workers}")
    print("=" * 70)

    # 确定待处理日期列表，并按时间升序排列（先处理早的日期，再处理晚的日期）
    if args.date:
        dates_to_process = [args.date]
    else:
        dates_to_process = sorted(get_existing_dates())

    if not dates_to_process:
        print("❌ 未发现可处理的日期文件。请检查 data/ 目录或通过 --date 指定。")
        return

    print(f"📅 发现待检查/补录的本地日期 ({len(dates_to_process)} 个，按时间顺序处理): {', '.join(dates_to_process)}")
    
    # 记录跨日期全局见过的 ID/DOI（包含本地历史数据所有已存在的论文），防止跨日期重复添加同一篇论文
    global_seen_ids = set()
    for d in dates_to_process:
        raw_path = os.path.join(PROJECT_ROOT, "data", f"{d}.jsonl")
        global_seen_ids.update(load_existing_ids(raw_path))

    total_backfilled_raw = 0
    total_backfilled_ai = 0

    for date_str in dates_to_process:
        print(f"\n" + "-" * 70)
        print(f"📂 正在处理日期文件: data/{date_str}.jsonl")
        
        raw_file = os.path.join(PROJECT_ROOT, "data", f"{date_str}.jsonl")
        enhanced_file = os.path.join(PROJECT_ROOT, "data", f"{date_str}_AI_enhanced_{language}.jsonl")
        
        current_file_ids = load_existing_ids(raw_file)
        
        # 计算该日期对应的时间查询窗口
        if args.from_date and args.to_date:
            from_date = args.from_date
            to_date = args.to_date
        else:
            target_dt = datetime.strptime(date_str, "%Y-%m-%d")
            yesterday_dt = target_dt - timedelta(days=1)
            from_dt = target_dt - timedelta(days=7)
            from_date = from_dt.strftime("%Y-%m-%d")
            to_date = yesterday_dt.strftime("%Y-%m-%d")
            
        print(f"🕒 检索时间区间: {from_date} 至 {to_date}")

        date_new_papers = []
        
        for journal in journals_to_crawl:
            jname = journal["name"]
            print(f"🔍 检查期刊 [{jname}] ({journal['category']})...")
            papers = fetch_and_format_journal_papers(journal, from_date, to_date, api_key)
            
            new_count = 0
            for p in papers:
                pid = str(p.get("id", "")).lower().strip()
                doi_part = p.get("abs", "").split("doi.org/")[-1].lower().strip() if "doi.org/" in p.get("abs", "") else ""
                
                # 严格跨日期比对去重：既不在当前文件，也不在历史已收录的任何日期文件中
                if pid and (pid in current_file_ids or pid in global_seen_ids):
                    continue
                if doi_part and (doi_part in current_file_ids or doi_part in global_seen_ids):
                    continue
                    
                date_new_papers.append(p)
                current_file_ids.add(pid)
                global_seen_ids.add(pid)
                if doi_part:
                    current_file_ids.add(doi_part)
                    global_seen_ids.add(doi_part)
                new_count += 1
                
            print(f"   -> 发现 {len(papers)} 篇，跨日期比对后新增待补录: {new_count} 篇")

        print(f"📊 日期 {date_str} 汇总: 共需补录 {len(date_new_papers)} 篇新论文")
        
        if not date_new_papers:
            print(f"✅ 日期 {date_str} 已包含所有相关论文，无需补录。")
            continue

        if args.dry_run:
            print(f"🔍 [试运行] 将追加 {len(date_new_papers)} 篇到 {raw_file}")
            total_backfilled_raw += len(date_new_papers)
            continue

        # 1. 执行 AI 增强
        if not args.skip_ai:
            enhanced_items = enhance_papers_with_ai(
                date_new_papers,
                model_name=model_name,
                language=language,
                max_workers=args.max_workers
            )
        else:
            enhanced_items = []

        # 2. 追加到原始 jsonl 文件
        with open(raw_file, "a", encoding="utf-8") as f:
            for p in date_new_papers:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
        print(f"💾 已成功将 {len(date_new_papers)} 篇论文追加至 {raw_file}")
        total_backfilled_raw += len(date_new_papers)

        # 3. 追加到 AI 增强 jsonl 文件
        if enhanced_items:
            # 建立已存在 enhanced ID 防止重复
            existing_enhanced_ids = load_existing_ids(enhanced_file)
            appended_ai_count = 0
            with open(enhanced_file, "a", encoding="utf-8") as f:
                for ep in enhanced_items:
                    epid = str(ep.get("id", "")).lower().strip()
                    if epid not in existing_enhanced_ids:
                        f.write(json.dumps(ep, ensure_ascii=False) + "\n")
                        existing_enhanced_ids.add(epid)
                        appended_ai_count += 1
            print(f"🤖 已成功将 {appended_ai_count} 篇 AI 增强论文追加至 {enhanced_file}")
            total_backfilled_ai += appended_ai_count

    # 4. 同步数据库
    if total_backfilled_raw > 0 and not args.dry_run:
        sync_database_stats()

    print("\n" + "=" * 70)
    print("🎉 补录任务完成！")
    print(f"📈 累计补录原始论文: {total_backfilled_raw} 篇")
    if not args.skip_ai:
        print(f"✨ 累计完成 AI 增强论文: {total_backfilled_ai} 篇")
    print("=" * 70)


if __name__ == "__main__":
    main()

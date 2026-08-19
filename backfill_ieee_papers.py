#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
期刊论文补录与 AI 增强工具：
补录本地数据中缺失的期刊论文（默认支持全部 15 本遥感与相关领域顶级期刊，亦可指定 IEEE 或特定期刊）并执行 AI 增强处理与数据库同步。
Journal Paper Backfill Tool: Backfill missing journal papers across all 15 journals 
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
    fetch_comprehensive_abstract,
    fetch_arxiv_abstract,
    find_arxiv_url,
    reconstruct_abstract
)

# IEEE 目标期刊配置（供 --ieee-only 模式使用）
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
        description="补录本地数据中缺失的期刊论文（默认全部 15 本期刊，支持自定义或仅 IEEE）并进行 AI 增强与数据库同步"
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="指定补录的单个日期 (格式: YYYY-MM-DD)"
    )
    parser.add_argument(
        "--days-range",
        nargs=2,
        type=int,
        metavar=("START_DAYS_AGO", "END_DAYS_AGO"),
        default=None,
        help="指定相对今天的前推天数区间 (例如: --days-range 15 30 表示检查过去 15 至 30 天的论文)"
    )
    parser.add_argument(
        "--from-date",
        type=str,
        default=None,
        help="显式指定扫描/查询开始日期 (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--to-date",
        type=str,
        default=None,
        help="显式指定扫描/查询结束日期 (YYYY-MM-DD)"
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
        default=True,
        help="补录 constants.py 中的全部 15 本期刊 (默认开启)"
    )
    parser.add_argument(
        "--ieee-only",
        action="store_true",
        help="仅补录 IEEE 目标期刊（TGRS、JSTARS、GRSL）"
    )
    parser.add_argument(
        "--journals",
        type=str,
        default=None,
        help="以逗号分隔指定补录的期刊名称 (例如: 'TGRS,ISPRS,RSE')"
    )
    parser.add_argument(
        "--skip-db-sync",
        action="store_true",
        help="跳过后续 statistics.db 数据库关键词与统计图谱的同步"
    )
    parser.add_argument(
        "--only-sync",
        action="store_true",
        help="仅重新提取关键词并同步本地 statistics.db 数据库，不重新抓取论文"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行模式：仅统计缺失并打印，不实际写入文件"
    )
    return parser.parse_args()


def get_target_journals(args) -> List[Dict]:
    """根据参数解析出待补录的目标期刊列表"""
    if args.journals:
        names = [n.strip().upper() for n in args.journals.split(",") if n.strip()]
        selected = [j for j in JOURNALS if j["name"].upper() in names or j["category"].upper() in names]
        if selected:
            return selected
        print(f"⚠️ 指定的期刊名称未匹配到预设期刊，将使用全部期刊。指定值: {args.journals}", file=sys.stderr)

    if args.ieee_only:
        return IEEE_JOURNAL_TARGETS

    return JOURNALS


def get_existing_dates() -> List[str]:
    """获取 data/ 目录下所有已存在的日期列表 (按升序排列)"""
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


def get_target_dates(args) -> List[str]:
    """根据命令行参数过滤出待处理的目标日期列表"""
    if args.date:
        return [args.date]

    all_dates = sorted(get_existing_dates())
    if not all_dates:
        return []

    if args.days_range:
        min_days, max_days = sorted(args.days_range)
        today_dt = datetime.now().date()
        from_dt = today_dt - timedelta(days=max_days)
        to_dt = today_dt - timedelta(days=min_days)
        return [d for d in all_dates if from_dt <= datetime.strptime(d, "%Y-%m-%d").date() <= to_dt]

    if args.from_date and args.to_date:
        from_dt = datetime.strptime(args.from_date, "%Y-%m-%d").date()
        to_dt = datetime.strptime(args.to_date, "%Y-%m-%d").date()
        return [d for d in all_dates if from_dt <= datetime.strptime(d, "%Y-%m-%d").date() <= to_dt]

    return all_dates


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
            raw_doi = (paper.get("doi") or "").replace("https://doi.org/", "").strip()
            authors = []
            for authorship in paper.get("authorships", []):
                author_name = authorship.get("author", {}).get("display_name")
                if author_name:
                    authors.append(author_name)
            if not authors:
                authors = ["Unknown Author"]
                
            arxiv_link_summary = ""
            arxiv_url = find_arxiv_url(paper)
            if arxiv_url:
                arxiv_link_summary = fetch_arxiv_abstract(arxiv_url) or ""
                
            summary, _ = fetch_comprehensive_abstract(
                doi=raw_doi,
                title=title,
                openalex_inverted_index=paper.get("abstract_inverted_index"),
                arxiv_abstract=arxiv_link_summary
            )
                
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
                
            arxiv_link_summary = ""
            arxiv_url = find_arxiv_url(paper_detail)
            if arxiv_url:
                arxiv_link_summary = fetch_arxiv_abstract(arxiv_url) or ""
                
            summary, _ = fetch_comprehensive_abstract(
                doi=doi,
                title=title,
                openalex_inverted_index=paper_detail.get("abstract_inverted_index"),
                crossref_abstract=crossref_item.get("abstract", ""),
                arxiv_abstract=arxiv_link_summary
            )
                
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
            summary, _ = fetch_comprehensive_abstract(
                doi=doi,
                title=title,
                crossref_abstract=crossref_item.get("abstract", "")
            )
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


def sync_database_stats(paper_groups=None):
    """补录完成后增量提取本轮论文关键词；未指定论文时执行手动全量同步。"""
    try:
        from server_modules.processor import (
            reextract_all_keywords,
            reextract_keywords_for_papers,
            scan_and_process_files,
        )
        if paper_groups:
            paper_count = sum(len(papers) for _, _, papers in paper_groups)
            print(f"正在为本次补录的 {paper_count} 篇论文提取关键词并增量同步 statistics.db...")
            success = reextract_keywords_for_papers(paper_groups)
        else:
            print("正在全量重新提取关键词并同步 statistics.db...")
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
    
    journals_to_crawl = get_target_journals(args)
    journal_names = [j["name"] for j in journals_to_crawl]
    
    print("=" * 70)
    print("🚀 期刊论文补录与 AI 增强工具 (All Journals Backfill)")
    print(f"🎯 目标期刊 ({len(journals_to_crawl)} 本): {', '.join(journal_names)}")
    print(f"🤖 AI 模型: {model_name} | 目标语言: {language} | 并发数: {args.max_workers}")
    if args.dry_run:
        print("🔍 运行模式: [试运行模式 (Dry Run)]")
    print("=" * 70)

    if args.only_sync:
        print("⚡ 已指定 --only-sync：跳过抓取与 AI 增强，直接执行关键词提取与数据库同步...")
        sync_database_stats()
        print("=" * 70)
        return

    # 确定待处理日期列表，并按时间升序排列
    dates_to_process = get_target_dates(args)

    if not dates_to_process:
        print("❌ 未发现符合条件的日期文件。请检查 data/ 目录或通过 --date / --days-range 指定。")
        return

    print(f"📅 发现待检查/补录的本地日期 ({len(dates_to_process)} 个，按时间顺序处理): {', '.join(dates_to_process)}")
    
    # 记录跨日期全局见过的 ID/DOI（包含本地历史数据所有已存在的论文），防止跨日期重复添加同一篇论文
    all_local_dates = get_existing_dates()
    global_seen_ids = set()
    for d in all_local_dates:
        raw_path = os.path.join(PROJECT_ROOT, "data", f"{d}.jsonl")
        global_seen_ids.update(load_existing_ids(raw_path))

    total_backfilled_raw = 0
    total_backfilled_ai = 0
    keyword_sync_groups = []

    for date_str in dates_to_process:
        print(f"\n" + "-" * 70)
        print(f"📂 正在处理日期文件: data/{date_str}.jsonl")
        
        raw_file = os.path.join(PROJECT_ROOT, "data", f"{date_str}.jsonl")
        enhanced_file = os.path.join(PROJECT_ROOT, "data", f"{date_str}_AI_enhanced_{language}.jsonl")
        
        current_file_ids = load_existing_ids(raw_file)
        
        # 计算该日期对应的时间查询窗口（默认以该日期为基准前推 7 天至前 1 天）
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
            existing_enhanced_ids = load_existing_ids(enhanced_file)
            appended_ai_count = 0
            appended_ai_items = []
            with open(enhanced_file, "a", encoding="utf-8") as f:
                for ep in enhanced_items:
                    epid = str(ep.get("id", "")).lower().strip()
                    if epid not in existing_enhanced_ids:
                        f.write(json.dumps(ep, ensure_ascii=False) + "\n")
                        existing_enhanced_ids.add(epid)
                        appended_ai_count += 1
                        appended_ai_items.append(ep)
            print(f"🤖 已成功将 {appended_ai_count} 篇 AI 增强论文追加至 {enhanced_file}")
            total_backfilled_ai += appended_ai_count
            if appended_ai_items:
                keyword_sync_groups.append((date_str, language, appended_ai_items))

    # 4. 同步数据库
    if keyword_sync_groups and not args.dry_run and not args.skip_db_sync:
        sync_database_stats(keyword_sync_groups)
    elif total_backfilled_raw > 0 and args.skip_ai and not args.skip_db_sync:
        print("ℹ️ 已跳过 AI 增强，本轮没有新增增强论文需要提取关键词。")

    print("\n" + "=" * 70)
    print("🎉 补录任务完成！")
    print(f"📈 累计补录原始论文: {total_backfilled_raw} 篇")
    if not args.skip_ai:
        print(f"✨ 累计完成 AI 增强论文: {total_backfilled_ai} 篇")
    print("=" * 70)


if __name__ == "__main__":
    main()

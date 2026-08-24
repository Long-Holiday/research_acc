#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
独立工具脚本：检测并修正本地所有 JSONL 数据文件中摘要缺失的论文，并自动执行 AI 增强与数据库同步。
Independent Tool Script: Detect and fix missing paper abstracts across all local JSONL files, 
perform AI enhancement, and synchronize the statistics database.
"""

import os
import sys
import glob
import json
import re
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple, Optional, Set
from tqdm import tqdm

# 项目根目录设置
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "daily_paper"))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "ai"))

import dotenv
dotenv.load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# 清理环境变量中的空白符和换行符
for k in list(os.environ.keys()):
    if isinstance(os.environ[k], str):
        os.environ[k] = os.environ[k].strip()

from daily_paper.daily_journals.abstract_fetcher import AbstractFetcher, clean_crossref_abstract
from daily_paper.daily_journals.openalex import fetch_openalex_single_detail, reconstruct_abstract


def parse_args():
    parser = argparse.ArgumentParser(
        description="检测并修正本地所有 JSONL 数据文件中缺失摘要的论文，并自动重新进行 AI 增强处理与数据库同步"
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="指定检查并修正的单个日期 (格式: YYYY-MM-DD)"
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
        help="显式指定扫描开始日期 (格式: YYYY-MM-DD)"
    )
    parser.add_argument(
        "--to-date",
        type=str,
        default=None,
        help="显式指定扫描结束日期 (格式: YYYY-MM-DD)"
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=2,
        help="AI 增强处理的最大并发线程数 (默认: 2)"
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
        help="仅修复原始 JSONL 文件中的摘要，跳过 AI 增强处理"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行模式：仅检测、统计缺失并尝试检索，不实际修改本地文件或调用 AI"
    )
    parser.add_argument(
        "--skip-db-sync",
        action="store_true",
        help="跳过后续 statistics.db 数据库关键词与统计图谱的同步"
    )
    return parser.parse_args()


def is_abstract_missing(summary: Optional[str]) -> bool:
    """判断论文摘要是否缺失或为无效占位符"""
    if not summary or not isinstance(summary, str):
        return True
    s = summary.strip()
    if len(s) < 25:
        return True
    lower = s.lower()
    invalid_phrases = [
        "no abstract available in openalex.",
        "no abstract available in openalex",
        "no abstract available.",
        "no abstract available",
        "abstract not available.",
        "abstract not available",
        "no abstract",
        "none"
    ]
    if lower in invalid_phrases:
        return True
    return False


def extract_identifiers(item: Dict) -> Tuple[str, str, str]:
    """从论文对象中提取 (doi, openalex_id, arxiv_url)"""
    doi = ""
    oa_id = ""
    arxiv_url = ""

    # 1. 检查 abs 字段
    abs_url = str(item.get("abs") or "").strip()
    if "doi.org/" in abs_url:
        doi = abs_url.split("doi.org/")[-1].strip().lower()
    elif "openalex.org/" in abs_url:
        oa_id = abs_url.split("openalex.org/")[-1].strip()
    elif "arxiv.org/" in abs_url:
        arxiv_url = abs_url

    # 2. 检查 id 字段
    raw_id = str(item.get("id") or "").strip()
    if raw_id.startswith("10."):
        doi = raw_id.lower().replace("_", "/")
    elif raw_id.startswith("W") or (raw_id.isdigit() and len(raw_id) >= 8):
        oa_id = raw_id
    elif "/" in raw_id and "10." in raw_id:
        doi = raw_id.lower()

    # 3. 检查 pdf 字段
    pdf_url = str(item.get("pdf") or "").strip()
    if not arxiv_url and "arxiv.org/" in pdf_url:
        arxiv_url = pdf_url

    return doi, oa_id, arxiv_url


def fetch_openalex_detail_by_id_or_doi(identifier: str, api_key: str = "") -> Optional[Dict]:
    """通过 OpenAlex Work ID 或 DOI 获取最新详情"""
    if not identifier:
        return None
    
    headers = {
        "User-Agent": "daily-arXiv-ai-enhanced/1.0 (mailto:dw-dengwei@users.noreply.github.com)"
    }
    
    clean_id = identifier.strip().replace("https://openalex.org/", "").replace("https://doi.org/", "")
    if clean_id.startswith("10."):
        url = f"https://api.openalex.org/works/doi:{clean_id}"
    else:
        url = f"https://api.openalex.org/works/{clean_id}"

    params = {}
    if api_key:
        params["api_key"] = api_key

    try:
        import requests
        resp = requests.get(url, params=params, headers=headers, timeout=12)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def rescue_paper_abstract(
    item: Dict,
    fetcher: AbstractFetcher,
    api_key: str = ""
) -> Tuple[Optional[str], str]:
    """
    多级尝试检索并挽救缺失的摘要：
      1. 若有 OpenAlex ID，先向 OpenAlex 请求最新详情（很多历史未解析的论文现已解析完成倒排索引）
      2. 提取 DOI 并使用 AbstractFetcher（Semantic Scholar / Europe PMC / arXiv Title / Crossref）
    返回: (rescued_abstract or None, source_tag)
    """
    title = str(item.get("title") or "").strip()
    doi, oa_id, arxiv_url = extract_identifiers(item)

    oa_inv = None
    # 步骤 1: 尝试通过 OpenAlex 获取最新详情
    if oa_id or doi:
        detail = fetch_openalex_detail_by_id_or_doi(oa_id or doi, api_key=api_key)
        if detail:
            oa_inv = detail.get("abstract_inverted_index")
            if not doi and detail.get("doi"):
                doi = detail.get("doi", "").replace("https://doi.org/", "").strip().lower()

    # 步骤 2: 调用多级摘要获取器
    summary, src = fetcher.get_abstract(
        doi=doi,
        title=title,
        openalex_inverted_index=oa_inv
    )

    if summary and not is_abstract_missing(summary):
        return summary.strip(), src

    return None, "missing"


def atomic_write_jsonl(filepath: str, items: List[Dict]):
    """原子化写入 JSONL 文件，确保写入安全性"""
    tmp_path = filepath + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    os.replace(tmp_path, filepath)


def enhance_papers_with_ai(
    papers: List[Dict],
    model_name: str,
    language: str,
    max_workers: int
) -> List[Dict]:
    """并发执行 AI 增强结构化分析"""
    if not papers:
        return []
    
    try:
        from ai.enhance import build_chain, process_single_item
    except ImportError:
        from enhance import build_chain, process_single_item

    chain = build_chain(model_name)
    enhanced_results = []
    
    print(f"🤖 正在为 {len(papers)} 篇修正摘要的论文生成 AI 结构化总结 (模型: {model_name}, 并发: {max_workers})...")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_single_item, chain, dict(p), language): p
            for p in papers
        }
        
        for future in tqdm(as_completed(futures), total=len(futures), desc="AI 增强进度"):
            try:
                res = future.result()
                if res is not None:
                    enhanced_results.append(res)
            except Exception as e:
                p = futures[future]
                print(f"AI 处理论文 [{p.get('id', '')}] 异常: {e}", file=sys.stderr)
                
    return enhanced_results


def update_enhanced_file(enhanced_filepath: str, newly_enhanced_papers: List[Dict]):
    """将新生成的 AI 增强条目精确就地替换或追加到已有的 AI 增强文件中"""
    if not newly_enhanced_papers:
        return
    
    # 建立新条目的索引字典（以 ID 和 DOI 为 key）
    new_by_id = {}
    new_by_doi = {}
    for p in newly_enhanced_papers:
        pid = str(p.get("id", "")).strip().lower()
        if pid:
            new_by_id[pid] = p
        abs_url = str(p.get("abs") or "").strip().lower()
        if "doi.org/" in abs_url:
            doi = abs_url.split("doi.org/")[-1].strip()
            if doi:
                new_by_doi[doi] = p

    existing_items = []
    replaced_count = 0
    handled_new_ids = set()

    if os.path.exists(enhanced_filepath):
        with open(enhanced_filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    pid = str(item.get("id", "")).strip().lower()
                    abs_url = str(item.get("abs") or "").strip().lower()
                    doi = abs_url.split("doi.org/")[-1].strip() if "doi.org/" in abs_url else ""
                    
                    matched_new = None
                    if pid in new_by_id:
                        matched_new = new_by_id[pid]
                        handled_new_ids.add(pid)
                    elif doi and doi in new_by_doi:
                        matched_new = new_by_doi[doi]
                        handled_new_ids.add(str(matched_new.get("id", "")).strip().lower())

                    if matched_new:
                        # 摘要修复可以先于 AI 增强完成，合并字段以免覆盖已有的 AI 结果。
                        existing_items.append({**item, **matched_new})
                        replaced_count += 1
                    else:
                        existing_items.append(item)
                except Exception:
                    continue

    # 追加尚未在旧增强文件中存在的其余条目
    appended_count = 0
    for p in newly_enhanced_papers:
        pid = str(p.get("id", "")).strip().lower()
        if pid not in handled_new_ids:
            existing_items.append(p)
            handled_new_ids.add(pid)
            appended_count += 1

    atomic_write_jsonl(enhanced_filepath, existing_items)
    print(f"💾 已更新 AI 增强文件 {enhanced_filepath}: 替换旧条目 {replaced_count} 篇，追加新条目 {appended_count} 篇。")


def sync_database(paper_groups: List[Tuple[str, str, List[Dict]]]):
    """仅为本轮成功修复并重新增强的论文增量更新关键词与统计。"""
    try:
        from server_modules.processor import reextract_keywords_for_papers, scan_and_process_files
        paper_count = sum(len(papers) for _, _, papers in paper_groups)
        print(f"📊 正在为本次修复的 {paper_count} 篇论文重新提取关键词并增量同步 statistics.db...")
        success = reextract_keywords_for_papers(paper_groups)
        if success:
            print("✅ statistics.db 统计数据库与关键词网络已成功同步！")
        else:
            print("⚠️ 关键词提取过程报告警告，正在尝试常规扫描同步...")
            scan_and_process_files()
    except Exception as e:
        print(f"⚠️ 同步数据库时遇到警告或跳过: {e}")


def main():
    args = parse_args()

    model_name = args.model_name or os.environ.get("MODEL_NAME", "deepseek-v4-flash")
    language = args.language or os.environ.get("LANGUAGE", "Chinese")
    api_key = os.environ.get("OPENALEX_API_KEY", "")
    data_dir = os.path.join(PROJECT_ROOT, "data")

    print("=" * 75)
    print("🛠️  论文摘要自动检测、多源修复与 AI 增强处理工具")
    print(f"📂 数据目录: {data_dir}")
    print(f"🤖 AI 模型: {model_name} | 语言: {language} | 并发数: {args.max_workers}")
    if args.dry_run:
        print("🔍 运行模式: [试运行模式 (Dry Run) - 不修改文件]")
    print("=" * 75)

    from datetime import timedelta
    
    # 确定待检查的文件列表
    if args.date:
        target_files = [os.path.join(data_dir, f"{args.date}.jsonl")]
        print(f"🎯 目标指定日期: {args.date}")
    elif args.days_range or (args.from_date and args.to_date):
        if args.days_range:
            min_days, max_days = sorted(args.days_range)
            today_dt = datetime.now().date()
            from_dt = today_dt - timedelta(days=max_days)
            to_dt = today_dt - timedelta(days=min_days)
            print(f"🕒 相对天数范围: 过去 {min_days} 至 {max_days} 天 ({from_dt.strftime('%Y-%m-%d')} 至 {to_dt.strftime('%Y-%m-%d')})")
        else:
            from_dt = datetime.strptime(args.from_date, "%Y-%m-%d").date()
            to_dt = datetime.strptime(args.to_date, "%Y-%m-%d").date()
            print(f"🕒 显式指定日期区间: {from_dt.strftime('%Y-%m-%d')} 至 {to_dt.strftime('%Y-%m-%d')}")

        pattern = os.path.join(data_dir, "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].jsonl")
        all_files = sorted(glob.glob(pattern))
        target_files = []
        for fpath in all_files:
            fname = os.path.basename(fpath)
            m = re.match(r"^(\d{4}-\d{2}-\d{2})\.jsonl$", fname)
            if m:
                f_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
                if from_dt <= f_date <= to_dt:
                    target_files.append(fpath)
    else:
        pattern = os.path.join(data_dir, "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].jsonl")
        target_files = sorted(glob.glob(pattern))
        print("🎯 扫描模式: 全量历史文件")

    if not target_files:
        print("❌ 未找到任何符合 YYYY-MM-DD.jsonl 命名规范的数据文件。")
        return

    fetcher = AbstractFetcher()
    
    total_scanned_papers = 0
    total_missing_found = 0
    total_rescued_papers = 0
    total_ai_processed = 0
    source_distribution = {}
    keyword_sync_groups = []

    for raw_filepath in target_files:
        if not os.path.exists(raw_filepath):
            print(f"⚠️ 文件不存在: {raw_filepath}")
            continue

        base_name = os.path.basename(raw_filepath)
        date_str = base_name.replace(".jsonl", "")
        enhanced_filepath = os.path.join(data_dir, f"{date_str}_AI_enhanced_{language}.jsonl")

        print(f"\n📂 正在扫描文件: {base_name} ...")

        file_items = []
        missing_indices = []

        with open(raw_filepath, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    file_items.append(item)
                    total_scanned_papers += 1
                    if is_abstract_missing(item.get("summary")):
                        missing_indices.append(idx)
                        total_missing_found += 1
                except Exception as e:
                    print(f"解析文件 {base_name} 第 {idx+1} 行异常: {e}")

        print(f"   -> 该文件共有 {len(file_items)} 篇论文，检测到缺失摘要: {len(missing_indices)} 篇")

        if not missing_indices:
            print("   ✅ 所有论文摘要完整，无需修复。")
            continue

        file_rescued_items = []
        file_source_stats = {}

        print(f"   🔍 正在对 {len(missing_indices)} 篇缺失论文尝试多源检索修复...")
        for idx in tqdm(missing_indices, desc=f"修复 {base_name}"):
            item = file_items[idx]
            rescued_abs, src = rescue_paper_abstract(item, fetcher, api_key=api_key)
            
            if rescued_abs:
                item["summary"] = rescued_abs
                file_rescued_items.append(item)
                file_source_stats[src] = file_source_stats.get(src, 0) + 1
                source_distribution[src] = source_distribution.get(src, 0) + 1
                total_rescued_papers += 1
            else:
                file_source_stats["still_missing"] = file_source_stats.get("still_missing", 0) + 1
                source_distribution["still_missing"] = source_distribution.get("still_missing", 0) + 1

        print(f"   ✨ 修复结果: 成功恢复 {len(file_rescued_items)} / {len(missing_indices)} 篇 | 来源分布: {file_source_stats}")

        if not file_rescued_items:
            print("   ℹ️ 本次检索未能从开放数据源检索到新摘要（可能刚出版暂未索引）。")
            continue

        if args.dry_run:
            print(f"   🔍 [试运行] 成功测试修复 {len(file_rescued_items)} 篇，跳过写入文件与 AI 增强。")
            continue

        # 1. 保存修复后的原始数据文件
        atomic_write_jsonl(raw_filepath, file_items)
        print(f"   💾 已更新原始文件: {raw_filepath}")

        # 2. 先同步增强文件中的原始摘要。即使后续 AI 增强失败，前端也能立即看到英文摘要。
        update_enhanced_file(enhanced_filepath, file_rescued_items)

        # 3. 对成功恢复摘要的论文执行 AI 增强
        if not args.skip_ai:
            enhanced_papers = enhance_papers_with_ai(
                file_rescued_items,
                model_name=model_name,
                language=language,
                max_workers=args.max_workers
            )
            total_ai_processed += len(enhanced_papers)

            # 4. 用 AI 结果覆盖增强文件中的对应条目
            update_enhanced_file(enhanced_filepath, enhanced_papers)

            # 数据库同步使用摘要修复后的完整集合，AI 结果存在时优先使用 AI 结果。
            enhanced_by_id = {
                str(p.get("id", "")).strip().lower(): p
                for p in enhanced_papers
                if p.get("id")
            }
            sync_papers = [
                enhanced_by_id.get(str(p.get("id", "")).strip().lower(), p)
                for p in file_rescued_items
                if p.get("id")
            ]
            if sync_papers:
                keyword_sync_groups.append((date_str, language, sync_papers))
        else:
            keyword_sync_groups.append((date_str, language, file_rescued_items))

    # 4. 统计数据库同步
    if keyword_sync_groups and not args.dry_run and not args.skip_db_sync:
        print("\n" + "-" * 75)
        sync_database(keyword_sync_groups)

    print("\n" + "=" * 75)
    print("🎉 全流程处理完成！")
    print(f"📊 累计扫描论文总数: {total_scanned_papers} 篇")
    print(f"⚠️  累计发现摘要缺失: {total_missing_found} 篇")
    print(f"✨ 累计成功修复摘要: {total_rescued_papers} 篇")
    if not args.skip_ai and not args.dry_run:
        print(f"🤖 累计完成 AI 增强处理: {total_ai_processed} 篇")
    print(f"📈 摘要来源渠道分布: {source_distribution}")
    print("=" * 75)


if __name__ == "__main__":
    main()

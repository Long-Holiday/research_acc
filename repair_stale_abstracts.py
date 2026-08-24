#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复本地存量增强数据中的过期/缺失英文摘要。

用法：
  python repair_stale_abstracts.py
  python repair_stale_abstracts.py --dry-run
  python repair_stale_abstracts.py --date 2026-08-19 --backup
  python repair_stale_abstracts.py --skip-db-sync
"""

import argparse
import glob
import json
import os
import re
import shutil
import sys
from datetime import datetime
from typing import Dict, List, Tuple, Set, Optional

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from fix_missing_abstracts import atomic_write_jsonl, is_abstract_missing


def parse_args():
    parser = argparse.ArgumentParser(
        description="修复原始 JSONL 与 AI 增强 JSONL 不一致的存量英文摘要"
    )
    parser.add_argument("--data-dir", default="data", help="数据目录，默认: data")
    parser.add_argument("--db-path", default="data/statistics.db", help="数据库路径")
    parser.add_argument("--date", help="只处理指定日期，格式: YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写入文件或数据库")
    parser.add_argument("--backup", action="store_true", help="写入前备份被修改的增强文件")
    parser.add_argument("--skip-db-sync", action="store_true", help="跳过数据库同步")
    return parser.parse_args()


def _read_jsonl(path: str) -> List[Dict]:
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                items.append(item)
    return items


def _normalize_title(title: Optional[str]) -> str:
    if not title:
        return ""
    clean = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fa5]", "", str(title).lower())
    return clean


def _extract_doi_clean(item: Dict) -> str:
    """从多个字段提取归一化的 DOI (小写、去除 https://doi.org/、将 _ 转换为 /)"""
    for key in ("doi", "abs", "url", "pdf", "id"):
        val = str(item.get(key) or "").strip().lower()
        if not val:
            continue
        if "doi.org/" in val:
            doi_part = val.split("doi.org/")[-1].split("?")[0].split("#")[0].strip("/")
            if doi_part.startswith("10."):
                return doi_part
        if val.startswith("10."):
            doi_part = val.split("?")[0].split("#")[0].strip("/")
            return doi_part.replace("_", "/")
        if "10." in val and "/" in val:
            idx = val.find("10.")
            doi_part = val[idx:].split("?")[0].split("#")[0].strip("/")
            return doi_part.replace("_", "/")
    return ""


def _extract_id_variants(item: Dict) -> Set[str]:
    """提取归一化 ID 及其常见变体"""
    variants = set()
    raw_id = str(item.get("id") or "").strip().lower()
    if not raw_id:
        return variants
    
    clean_id = raw_id.replace("https://openalex.org/", "").replace("https://doi.org/", "").strip("/")
    variants.add(clean_id)
    if "_" in clean_id:
        variants.add(clean_id.replace("_", "/"))
    if "/" in clean_id:
        variants.add(clean_id.replace("/", "_"))
    return variants


def _build_item_index(items: List[Dict]) -> Tuple[Dict[str, Dict], Dict[str, Dict], Dict[str, Dict]]:
    """构建多级索引: (id_map, doi_map, title_map)"""
    id_map = {}
    doi_map = {}
    title_map = {}
    
    for item in items:
        for v in _extract_id_variants(item):
            id_map[v] = item
        doi = _extract_doi_clean(item)
        if doi:
            doi_map[doi] = item
        title_norm = _normalize_title(item.get("title"))
        if title_norm and len(title_norm) >= 10:
            title_map[title_norm] = item
            
    return id_map, doi_map, title_map


def _find_matching_item(item: Dict, id_map: Dict, doi_map: Dict, title_map: Dict) -> Optional[Dict]:
    """通过 ID -> DOI -> Title 多级查找匹配条目"""
    for v in _extract_id_variants(item):
        if v in id_map:
            return id_map[v]
    
    doi = _extract_doi_clean(item)
    if doi and doi in doi_map:
        return doi_map[doi]
        
    title_norm = _normalize_title(item.get("title"))
    if title_norm and len(title_norm) >= 10 and title_norm in title_map:
        return title_map[title_norm]
        
    return None


def _backup(path: str):
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = f"{path}.bak.{timestamp}"
    shutil.copy2(path, backup_path)
    print(f"  已备份: {backup_path}")


def repair_file(raw_path: str, enhanced_path: str, dry_run=False, backup=False) -> List[Dict]:
    """把原始文件中的有效摘要智能合并到增强文件，同时将增强文件有效摘要回填原始文件，返回实际修复的增强论文。"""
    raw_items = _read_jsonl(raw_path) if os.path.exists(raw_path) else []
    if not os.path.exists(enhanced_path):
        return []

    enhanced_items = _read_jsonl(enhanced_path)
    
    # 建立原始文件中的有效摘要条目索引
    valid_raw_items = [it for it in raw_items if not is_abstract_missing(it.get("summary"))]
    raw_id_map, raw_doi_map, raw_title_map = _build_item_index(valid_raw_items)
    
    # 建立增强文件中的有效摘要条目索引 (供反向回填)
    valid_enh_items = [it for it in enhanced_items if not is_abstract_missing(it.get("summary"))]
    enh_id_map, enh_doi_map, enh_title_map = _build_item_index(valid_enh_items)

    repaired_enhanced = []
    enhanced_changed = False
    
    # 1. 原始文件 -> 增强文件修复
    for item in enhanced_items:
        if not is_abstract_missing(item.get("summary")):
            continue
        matched_raw = _find_matching_item(item, raw_id_map, raw_doi_map, raw_title_map)
        if matched_raw and not is_abstract_missing(matched_raw.get("summary")):
            item["summary"] = matched_raw["summary"]
            repaired_enhanced.append(item)
            enhanced_changed = True

    if enhanced_changed and not dry_run:
        if backup:
            _backup(enhanced_path)
        atomic_write_jsonl(enhanced_path, enhanced_items)

    # 2. 反向回填: 增强文件 -> 原始文件修复
    raw_changed = False
    if raw_items and valid_enh_items:
        for item in raw_items:
            if not is_abstract_missing(item.get("summary")):
                continue
            matched_enh = _find_matching_item(item, enh_id_map, enh_doi_map, enh_title_map)
            if matched_enh and not is_abstract_missing(matched_enh.get("summary")):
                item["summary"] = matched_enh["summary"]
                raw_changed = True
        if raw_changed and not dry_run:
            atomic_write_jsonl(raw_path, raw_items)

    return repaired_enhanced


def sync_database(groups: List[Tuple[str, str, List[Dict]]], db_path: str):
    if not groups:
        return
    from server_modules import processor

    old_db_path = getattr(processor, "DB_PATH", db_path)
    processor.DB_PATH = db_path
    try:
        # 清除内存文件缓存，强制让数据库重新更新
        if hasattr(processor, "processed_files_cache"):
            processor.processed_files_cache.clear()
        if hasattr(processor, "cache_initialized"):
            processor.cache_initialized = False

        if not processor.reextract_keywords_for_papers(groups):
            raise RuntimeError("增量数据库同步失败")
    finally:
        processor.DB_PATH = old_db_path


def main():
    args = parse_args()
    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            raise SystemExit("--date 必须是 YYYY-MM-DD 格式")

    raw_pattern = os.path.join(args.data_dir, "????-??-??.jsonl")
    raw_paths = sorted(glob.glob(raw_pattern))
    groups = []
    total_repaired = 0

    total_enhanced_missing = 0
    total_copyable = 0
    for raw_path in raw_paths:
        date = os.path.basename(raw_path)[:-6]
        if args.date and date != args.date:
            continue
        enhanced_paths = sorted(glob.glob(os.path.join(args.data_dir, f"{date}_AI_enhanced_*.jsonl")))
        for enhanced_path in enhanced_paths:
            language = os.path.basename(enhanced_path).split("_AI_enhanced_", 1)[1][:-6]
            try:
                enhanced_items = _read_jsonl(enhanced_path)
                missing_in_file = sum(1 for it in enhanced_items if is_abstract_missing(it.get("summary")))
                total_enhanced_missing += missing_in_file
            except Exception:
                missing_in_file = 0
            repaired = repair_file(
                raw_path,
                enhanced_path,
                dry_run=args.dry_run,
                backup=args.backup,
            )
            if missing_in_file:
                copyable = len(repaired)
                total_copyable += copyable
                if copyable:
                    print(f"{date} / {language}: 增强文件缺失 {missing_in_file} 篇，其中可从原始文件复制修复 {copyable} 篇")
                else:
                    print(f"{date} / {language}: 增强文件缺失 {missing_in_file} 篇，原始文件亦无有效摘要（需远程拉取）")
            if repaired:
                total_repaired += len(repaired)
                groups.append((date, language, repaired))
                if not args.dry_run:
                    print(f"{date} / {language}: 修复 {len(repaired)} 篇")

    if not total_repaired:
        if total_enhanced_missing:
            print(f"诊断完成：增强文件中共 {total_enhanced_missing} 篇缺失英文摘要，但原始文件同样缺失，无法通过本地复制修复。")
            print("→ 请运行 `python fix_missing_abstracts.py`（或带 --dry-run 预览）从 OpenAlex / Crossref / arXiv 等远程拉取摘要，")
            print("  成功后会自动同步到增强文件并刷新前端显示。")
        else:
            print("未发现需要修复的存量摘要数据。")
        return 0

    if args.dry_run:
        print(f"[dry-run] 共发现可复制修复 {total_repaired} 篇（增强缺失总数 {total_enhanced_missing}，其中可复制 {total_copyable} 篇），不写入文件或数据库。")
        if total_enhanced_missing > total_copyable:
            print(f"另有 {total_enhanced_missing - total_copyable} 篇原始与增强文件均缺失，需运行 fix_missing_abstracts.py 远程修复。")
        return 0

    if not args.skip_db_sync:
        sync_database(groups, args.db_path)
        print("数据库同步完成。")
    print(f"共修复 {total_repaired} 篇存量论文摘要。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

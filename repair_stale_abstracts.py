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
import shutil
import sys
from datetime import datetime
from typing import Dict, List, Tuple

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


def _item_key(item: Dict) -> str:
    return str(item.get("id") or "").strip().lower()


def _backup(path: str):
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = f"{path}.bak.{timestamp}"
    shutil.copy2(path, backup_path)
    print(f"  已备份: {backup_path}")


def repair_file(raw_path: str, enhanced_path: str, dry_run=False, backup=False) -> List[Dict]:
    """把原始文件中的有效摘要合并到增强文件，返回实际修复的论文。"""
    raw_items = {
        _item_key(item): item
        for item in _read_jsonl(raw_path)
        if _item_key(item) and not is_abstract_missing(item.get("summary"))
    }
    if not raw_items or not os.path.exists(enhanced_path):
        return []

    enhanced_items = _read_jsonl(enhanced_path)
    repaired = []
    changed = False
    for item in enhanced_items:
        key = _item_key(item)
        raw_item = raw_items.get(key)
        if not raw_item or not is_abstract_missing(item.get("summary")):
            continue
        item["summary"] = raw_item["summary"]
        repaired.append(item)
        changed = True

    if changed and not dry_run:
        if backup:
            _backup(enhanced_path)
        atomic_write_jsonl(enhanced_path, enhanced_items)
    return repaired


def sync_database(groups: List[Tuple[str, str, List[Dict]]], db_path: str):
    if not groups:
        return
    from server_modules import processor

    old_db_path = processor.DB_PATH
    processor.DB_PATH = db_path
    try:
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
            # 统计增强文件中缺失英文摘要的总数，用于 dry-run 诊断
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
                    # 原始文件同样缺失，无法通过复制修复
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

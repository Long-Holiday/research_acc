#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
存量数据去重清理 CLI：
一键清理 data/*.jsonl 跨日期重复与 statistics.db 重复入库，数据源于之前 7 天滑动窗口导致的期刊重复。

用法：
  python clean_dedup.py                 # 默认清理 data 目录与 data/statistics.db
  python clean_dedup.py --dry-run        # 预览不写入
  python clean_dedup.py --only-files     # 仅清理 JSONL
  python clean_dedup.py --only-db        # 仅清理 DB
  python clean_dedup.py --data-dir data --db-path data/statistics.db --backup
"""

import os
import sys
import re
import json
import glob
import shutil
import sqlite3
import argparse
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

def parse_args():
    parser = argparse.ArgumentParser(
        description="存量去重清理：清理 data/*.jsonl 跨日期/同文件重复及 statistics.db 重复入库",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
示例：
  python clean_dedup.py --dry-run
  python clean_dedup.py --backup --verbose
  python clean_dedup.py --only-files --data-dir data
  python clean_dedup.py --only-db --db-path data/statistics.db
        """,
    )
    parser.add_argument("--data-dir", type=str, default="data", help="JSONL 数据目录 (默认: data)")
    parser.add_argument("--db-path", type=str, default="data/statistics.db", help="SQLite DB 路径 (默认: data/statistics.db)")
    parser.add_argument("--dry-run", action="store_true", help="仅预览统计，不实际写入/删除")
    parser.add_argument("--backup", action="store_true", help="写入前备份原文件/DB 到 .bak.<timestamp>")
    parser.add_argument("--verbose", action="store_true", help="输出详细每文件变更")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--only-files", action="store_true", help="仅清理 JSONL 文件")
    group.add_argument("--only-db", action="store_true", help="仅清理数据库")
    parser.add_argument("--no-ai", action="store_true", help="跳过 *_AI_enhanced_*.jsonl，仅清理原始文件")
    return parser.parse_args()


def _extract_pid_doi(item: dict):
    pid = str(item.get("id", "")).lower().strip()
    doi = ""
    abs_url = item.get("abs") or ""
    if "doi.org/" in abs_url:
        doi = abs_url.split("doi.org/")[-1].lower().strip().split("?")[0].split("#")[0].strip("/")
        doi = doi.strip()
    return pid, doi


def clean_group(files, label, dry_run=False, backup=False, verbose=False):
    """按日期升序清理一组同类文件（RAW 或 ENH），返回统计"""
    seen_pid = {}
    seen_doi = {}
    total_removed = 0
    total_kept = 0
    per_file_stats = []

    for f in sorted(files):
        if not os.path.exists(f):
            continue
        with open(f, "r", encoding="utf-8") as rf:
            lines = rf.readlines()

        kept_lines = []
        removed = 0
        seen_in_file = set()

        m = re.match(r".*?(\d{4}-\d{2}-\d{2})", os.path.basename(f))
        date = m.group(1) if m else f

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                j = json.loads(stripped)
            except Exception:
                kept_lines.append(line)
                continue
            pid, doi = _extract_pid_doi(j)

            # 同文件内去重
            if pid and pid in seen_in_file:
                removed += 1
                continue
            if doi and doi in seen_in_file:
                removed += 1
                continue
            # 跨日期去重（最早日期优先保留）
            if pid and pid in seen_pid:
                removed += 1
                continue
            if doi and doi in seen_doi:
                removed += 1
                continue

            kept_lines.append(line)
            if pid:
                seen_pid[pid] = date
                seen_in_file.add(pid)
            if doi:
                seen_doi[doi] = date
                seen_in_file.add(doi)

        kept = len(kept_lines)
        per_file_stats.append((f, len(lines), kept, removed))
        total_removed += removed
        total_kept += kept

        if removed > 0:
            if verbose or not dry_run:
                print(f"  {label} {os.path.basename(f)}: {len(lines)} -> {kept} 移除 {removed}")
            if not dry_run:
                if backup:
                    ts = datetime.now().strftime("%Y%m%d%H%M%S")
                    shutil.copy2(f, f"{f}.bak.{ts}")
                with open(f, "w", encoding="utf-8") as wf:
                    wf.writelines(kept_lines)
        else:
            if verbose:
                print(f"  {label} {os.path.basename(f)}: 无重复 {len(lines)}")

    return total_removed, total_kept, per_file_stats


def clean_jsonl_files(data_dir, dry_run=False, backup=False, verbose=False, no_ai=False):
    print(f"\n=== 清理 JSONL 文件 (目录: {data_dir}) ===")
    if not os.path.isdir(data_dir):
        print(f"目录不存在: {data_dir}")
        return 0

    all_files = sorted(glob.glob(os.path.join(data_dir, "*.jsonl")))
    raw_files = [f for f in all_files if "_AI_enhanced_" not in f]
    enh_files = [f for f in all_files if "_AI_enhanced_" in f]

    if no_ai:
        enh_files = []

    print(f"发现原始文件 {len(raw_files)} 个，AI 增强文件 {len(enh_files)} 个")
    if dry_run:
        print("[dry-run] 仅预览，不写入")

    total_removed = 0
    if raw_files:
        print("--- RAW 原始文件去重（按日期升序，保留最早）---")
        removed, kept, _ = clean_group(raw_files, "RAW", dry_run=dry_run, backup=backup, verbose=verbose)
        total_removed += removed
        print(f"RAW 总计移除 {removed}")

    if enh_files:
        print("--- ENH AI增强文件去重（按日期升序，保留最早）---")
        removed, kept, _ = clean_group(enh_files, "ENH", dry_run=dry_run, backup=backup, verbose=verbose)
        total_removed += removed
        print(f"ENH 总计移除 {removed}")

    print(f"JSONL 总计移除 {total_removed} 条重复")
    return total_removed


def clean_db(db_path, dry_run=False, backup=False, verbose=False):
    print(f"\n=== 清理数据库 (路径: {db_path}) ===")
    if not os.path.exists(db_path):
        print(f"DB 不存在: {db_path}，跳过")
        return 0

    if dry_run:
        print("[dry-run] 预览统计，不执行 DELETE")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 预览统计
    cur.execute("SELECT COUNT(*) FROM papers")
    total_papers = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT paper_id || '|' || language) FROM papers")
    distinct = cur.fetchone()[0]
    dup_papers = total_papers - distinct
    print(f"papers 总数 {total_papers}，去重后 DISTINCT {distinct}，待删除重复 {dup_papers}")

    cur.execute("SELECT COUNT(*) FROM paper_keywords")
    pk_total = cur.fetchone()[0]
    print(f"paper_keywords 总数 {pk_total}")

    if dry_run:
        conn.close()
        return dup_papers

    if backup:
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path = f"{db_path}.bak.{ts}"
        shutil.copy2(db_path, backup_path)
        print(f"已备份 DB 到 {backup_path}")

    # 1. papers 去重：同 (paper_id, language) 保留最早 rowid
    cur.execute("""
        DELETE FROM papers WHERE rowid NOT IN (
            SELECT MIN(rowid) FROM papers GROUP BY paper_id, language
        )
    """)
    del_papers = cur.rowcount
    print(f"删除 papers 重复行 {del_papers}")

    # 2. paper_keywords 清理：仅保留与 papers 白名单一致的记录
    cur.execute("""
        DELETE FROM paper_keywords WHERE (paper_id, language, paper_date) NOT IN (
            SELECT paper_id, language, paper_date FROM papers
        )
    """)
    del_pk = cur.rowcount
    print(f"清理 paper_keywords 孤儿/重复 {del_pk}")

    # 3. 重建聚合统计
    cur.execute("DELETE FROM keyword_stats")
    cur.execute("DELETE FROM agg_daily_papers")
    cur.execute("DELETE FROM agg_daily_keywords")

    cur.execute("""
        INSERT INTO keyword_stats (paper_date, language, category, keyword, frequency)
        SELECT paper_date, language, category, keyword, COUNT(*) as frequency
        FROM paper_keywords
        GROUP BY paper_date, language, category, keyword
    """)
    print(f"重建 keyword_stats 插入 {cur.rowcount}")

    cur.execute("""
        INSERT INTO agg_daily_papers (paper_date, language, category, total_papers)
        SELECT paper_date, language, category, COUNT(DISTINCT paper_id)
        FROM paper_keywords
        GROUP BY paper_date, language, category
    """)
    print(f"重建 agg_daily_papers {cur.rowcount}")

    cur.execute("""
        INSERT INTO agg_daily_keywords (paper_date, language, category, keyword, distinct_paper_count)
        SELECT paper_date, language, category, keyword, COUNT(DISTINCT paper_id)
        FROM paper_keywords
        GROUP BY paper_date, language, category, keyword
    """)
    print(f"重建 agg_daily_keywords {cur.rowcount}")

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM papers")
    print(f"最终 papers {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM paper_keywords")
    print(f"最终 paper_keywords {cur.fetchone()[0]}")

    # 同步 test db 若存在
    test_db = os.path.join(os.path.dirname(db_path), "test_statistics.db")
    if os.path.exists(test_db) and os.path.abspath(test_db) != os.path.abspath(db_path):
        try:
            shutil.copy2(db_path, test_db)
            print(f"已同步到 {test_db}")
        except Exception as e:
            print(f"同步 test db 失败: {e}")

    conn.close()
    return del_papers + del_pk


def main():
    args = parse_args()
    print("=" * 60)
    print("存量去重清理 CLI")
    print(f"数据目录: {args.data_dir} | DB: {args.db_path} | dry-run: {args.dry_run} | backup: {args.backup}")
    print("=" * 60)

    total = 0
    if not args.only_db:
        total += clean_jsonl_files(args.data_dir, dry_run=args.dry_run, backup=args.backup, verbose=args.verbose, no_ai=args.no_ai)
    if not args.only_files:
        total += clean_db(args.db_path, dry_run=args.dry_run, backup=args.backup, verbose=args.verbose)

    print("\n" + "=" * 60)
    if args.dry_run:
        print(f"[dry-run] 预览完成，共发现待清理 {total} 条重复（未写入）")
    else:
        print(f"清理完成，共处理 {total} 条重复")
    print("=" * 60)


if __name__ == "__main__":
    main()

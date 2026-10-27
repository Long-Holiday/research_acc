"""Independent maintenance tasks formerly triggered by the web pages."""

import argparse
from contextlib import redirect_stdout
from datetime import date
import json
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parent


def valid_date(value):
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("日期必须为有效的 YYYY-MM-DD")
    return value


def positive_int(value):
    try:
        number = int(value)
        if number > 0:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("必须为正整数")


def add_paths(parser, data=False):
    if data:
        parser.add_argument("--data-dir", default=str(PROJECT_DIR / "data"))
    parser.add_argument("--db-path", default=str(PROJECT_DIR / "data/statistics.db"))


def build_parser():
    parser = argparse.ArgumentParser(description="独立维护任务（不使用 Web API）")
    commands = parser.add_subparsers(dest="command", required=True)

    processing = commands.add_parser("data-process", help="统一增量入库完整增强文件与关键词统计")
    add_paths(processing, data=True)

    backfill = commands.add_parser("advisor-backfill", help="只补齐历史研报")
    add_paths(backfill, data=True)
    backfill.add_argument("--topic")
    backfill.add_argument("--force", action="store_true", help="强制重做全部历史日期")

    regenerate = commands.add_parser("advisor-regenerate", help="重新生成指定日期研报")
    add_paths(regenerate, data=True)
    regenerate.add_argument("--date", type=valid_date, required=True)
    regenerate.add_argument("--topic")

    reextract = commands.add_parser("keywords-reextract", help="全量重建关键词索引与统计")
    add_paths(reextract, data=True)

    filtering = commands.add_parser("keywords-filter", help="输出 AI/规则建议排除的关键词 JSON")
    add_paths(filtering)
    filtering.add_argument("--start-date", type=valid_date)
    filtering.add_argument("--end-date", type=valid_date)
    filtering.add_argument("--lang", default="Chinese")
    filtering.add_argument("--category", default="All", help="All 或逗号分隔的分类")
    filtering.add_argument("--model-name")
    filtering.add_argument("--keywords-file", help="JSON 字符串数组文件；- 表示 stdin")
    logs = commands.add_parser("logs-cleanup", help="清理过期日志并限制单个日志大小")
    logs.add_argument("--max-size-mb", type=positive_int, default=10, help="单个日志保留末尾 MiB（默认 10）")
    logs.add_argument("--dry-run", action="store_true", help="仅预览，不修改日志")
    return parser


def initialize_database(db_path):
    from server_modules.database import connect_db
    from server_modules.processor import _init_tables

    Path(db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
    conn = connect_db(db_path)
    try:
        _init_tables(conn.cursor())
        conn.commit()
    finally:
        conn.close()


def run_advisor(args):
    from ai.advisor import (
        backfill_historical_reports,
        generate_advisor_report,
        get_unprocessed_dates,
    )

    if not Path(args.data_dir).is_dir():
        raise FileNotFoundError(f"数据目录不存在: {args.data_dir}")
    initialize_database(args.db_path)
    if args.command == "advisor-regenerate":
        report = generate_advisor_report(
            date_str=args.date, topic=args.topic, force=True, backfill=False,
            data_dir=args.data_dir, db_path=args.db_path,
        )
        print(f"完成：{report['report_date']}", file=sys.stderr)
        return 0

    expected = get_unprocessed_dates(
        data_dir=args.data_dir, db_path=args.db_path, force=args.force,
    )
    processed = backfill_historical_reports(
        data_dir=args.data_dir, db_path=args.db_path, topic=args.topic, force=args.force,
    )
    failed = sorted(set(expected) - set(processed))
    if failed:
        print(f"历史补漏部分失败：{', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


def run_filter(args):
    from ai.keyword_filter import filter_meaningless_keywords

    if args.keywords_file:
        if args.keywords_file == "-":
            candidates = json.load(sys.stdin)
        else:
            with open(args.keywords_file, encoding="utf-8") as source:
                candidates = json.load(source)
        if not isinstance(candidates, list) or any(not isinstance(kw, str) for kw in candidates):
            raise ValueError("关键词输入必须是 JSON 字符串数组")
    else:
        if not Path(args.db_path).is_file():
            raise FileNotFoundError(f"数据库不存在: {args.db_path}")
        from server_modules.stats import query_keyword_stats

        result = query_keyword_stats(
            args.db_path, args.start_date, args.end_date, args.lang, args.category,
        )
        candidates = [item["keyword"] for item in result["keywords"]]

    candidates = list(dict.fromkeys(kw.strip() for kw in candidates if kw.strip()))[:150]
    excluded = filter_meaningless_keywords(candidates, args.category, args.model_name)
    return {
        "status": "success",
        "excluded_keywords": excluded,
        "total_checked": len(candidates),
        "excluded_count": len(excluded),
    }


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "keywords-filter":
        if args.keywords_file:
            if args.start_date or args.end_date:
                parser.error("--keywords-file 不可与日期范围同时使用")
        elif not args.start_date or not args.end_date:
            parser.error("数据库过滤需要 --start-date 和 --end-date")
        elif args.start_date > args.end_date:
            parser.error("开始日期不能晚于结束日期")

    if args.command == "logs-cleanup":
        from server_modules.log_cleanup import cleanup_logs

        try:
            results = cleanup_logs(
                PROJECT_DIR, max_bytes=args.max_size_mb * 1024 * 1024, dry_run=args.dry_run,
            )
        except OSError as exc:
            print(f"日志清理失败：{exc}", file=sys.stderr)
            return 1
        print(json.dumps({"dry_run": args.dry_run, "files": results}, ensure_ascii=False))
        return 1 if any(item["action"] == "error" for item in results) else 0

    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")

    try:
        # 业务模块的日志不能污染可重定向的 JSON 输出。
        with redirect_stdout(sys.stderr):
            if args.command == "data-process":
                from server_modules.processor import scan_and_process_files

                count = scan_and_process_files(data_dir=args.data_dir, db_path=args.db_path)
                print(f"数据处理完成：更新 {count} 个增强文件", file=sys.stderr)
                return 0
            if args.command.startswith("advisor-"):
                return run_advisor(args)
            if args.command == "keywords-reextract":
                from server_modules.processor import get_reextract_status, reextract_all_keywords

                success = reextract_all_keywords(data_dir=args.data_dir, db_path=args.db_path)
                status = get_reextract_status()
                print(status.get("error") or status.get("message") or "任务未启动", file=sys.stderr)
                return 0 if success else 1
            result = run_filter(args)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"维护任务失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

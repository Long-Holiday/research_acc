import os
import sys
import json
import tempfile
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backfill_ieee_papers import (
    parse_args,
    get_target_journals,
    get_target_dates,
    load_existing_ids,
    IEEE_JOURNAL_TARGETS,
)
from daily_paper.daily_journals import JOURNALS


def test_parse_args_defaults():
    with patch.object(sys, "argv", ["backfill_ieee_papers.py"]):
        args = parse_args()
        assert args.all_journals is True
        assert args.days_range is None
        assert args.skip_db_sync is False
        assert args.skip_ai is False
        assert args.ieee_only is False
        assert args.journals is None


def test_parse_args_custom():
    with patch.object(
        sys,
        "argv",
        [
            "backfill_ieee_papers.py",
            "--days-range", "15", "30",
            "--max-workers", "4",
            "--skip-db-sync",
            "--ieee-only"
        ]
    ):
        args = parse_args()
        assert args.days_range == [15, 30]
        assert args.max_workers == 4
        assert args.skip_db_sync is True
        assert args.ieee_only is True


def test_get_target_journals():
    # 1. 默认全部期刊
    with patch.object(sys, "argv", ["backfill_ieee_papers.py"]):
        args = parse_args()
        journals = get_target_journals(args)
        assert len(journals) == len(JOURNALS)
        assert len(journals) == 15

    # 2. 仅 IEEE 期刊
    with patch.object(sys, "argv", ["backfill_ieee_papers.py", "--ieee-only"]):
        args = parse_args()
        journals = get_target_journals(args)
        assert len(journals) == 3
        names = [j["name"] for j in journals]
        assert "TGRS" in names
        assert "JSTARS" in names
        assert "GRSL" in names

    # 3. 指定特定期刊
    with patch.object(sys, "argv", ["backfill_ieee_papers.py", "--journals", "TGRS,RSE"]):
        args = parse_args()
        journals = get_target_journals(args)
        assert len(journals) == 2
        names = [j["name"] for j in journals]
        assert "TGRS" in names
        assert "RSE" in names


def test_load_existing_ids():
    with tempfile.TemporaryDirectory() as tmpdir:
        jsonl_path = os.path.join(tmpdir, "test.jsonl")
        items = [
            {"id": "W123456", "abs": "https://doi.org/10.1109/TGRS.2026.0001", "title": "Paper 1"},
            {"id": "10.1109_jstars.2026.0002", "abs": "https://doi.org/10.1109/jstars.2026.0002", "title": "Paper 2"},
            {"id": "2607.99999", "abs": "https://arxiv.org/abs/2607.99999", "title": "arXiv Paper"}
        ]
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it) + "\n")

        seen_ids = load_existing_ids(jsonl_path)
        assert "w123456" in seen_ids
        assert "10.1109/tgrs.2026.0001" in seen_ids
        assert "10.1109_jstars.2026.0002" in seen_ids
        assert "10.1109/jstars.2026.0002" in seen_ids
        assert "2607.99999" in seen_ids


def test_get_target_dates():
    # 测试日期筛选过滤
    today_dt = datetime.now().date()
    d1 = (today_dt - timedelta(days=20)).strftime("%Y-%m-%d")
    d2 = (today_dt - timedelta(days=5)).strftime("%Y-%m-%d")
    d3 = (today_dt - timedelta(days=40)).strftime("%Y-%m-%d")

    mock_dates = [d3, d1, d2]

    with patch("backfill_ieee_papers.get_existing_dates", return_value=mock_dates):
        # 1. 过去 15-30 天
        with patch.object(sys, "argv", ["backfill_ieee_papers.py", "--days-range", "15", "30"]):
            args = parse_args()
            targets = get_target_dates(args)
            assert targets == [d1]

        # 2. 单个指定日期
        with patch.object(sys, "argv", ["backfill_ieee_papers.py", "--date", d2]):
            args = parse_args()
            targets = get_target_dates(args)
            assert targets == [d2]

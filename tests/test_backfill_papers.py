import argparse
import gc
import io
import os
import sys
import json
import tempfile
import time
import threading
import weakref
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import patch
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backfill_ieee_papers
from backfill_ieee_papers import (
    parse_args,
    get_target_journals,
    get_target_dates,
    load_existing_ids,
    upsert_jsonl_file,
    IEEE_JOURNAL_TARGETS,
)
from daily_paper.daily_journals import JOURNALS


class TrackingExecutor:
    """记录同时在途 Future 数量的 ThreadPoolExecutor 包装。"""

    instances = []

    def __init__(self, max_workers=None, **kwargs):
        self._inner = RealThreadPoolExecutor(max_workers=max_workers, **kwargs)
        self._lock = threading.Lock()
        self.outstanding = 0
        self.max_outstanding = 0
        self.submitted = 0
        TrackingExecutor.instances.append(self)

    def submit(self, fn, *args, **kwargs):
        with self._lock:
            self.submitted += 1
            self.outstanding += 1
            self.max_outstanding = max(self.max_outstanding, self.outstanding)
        future = self._inner.submit(fn, *args, **kwargs)
        future.add_done_callback(self._on_done)
        return future

    def _on_done(self, _future):
        with self._lock:
            self.outstanding -= 1

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *args):
        return self._inner.__exit__(*args)


class FakeTqdm:
    instances = []

    def __init__(self, total=None, desc=None, **kwargs):
        self.total = total
        self.desc = desc
        self.updates = 0
        FakeTqdm.instances.append(self)

    def update(self, n=1):
        self.updates += n

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


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


def test_upsert_jsonl_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        jsonl_path = os.path.join(tmpdir, "test_upsert.jsonl")

        # 初始条目
        initial_items = [
            {
                "id": "10.1109_tgrs.2026.0001",
                "doi": "10.1109/tgrs.2026.0001",
                "title": "Old Paper 1",
                "summary": "No abstract available"
            },
            {
                "id": "10.1109_tgrs.2026.0002",
                "doi": "10.1109/tgrs.2026.0002",
                "title": "Paper 2",
                "summary": "Existing summary 2"
            }
        ]
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for it in initial_items:
                f.write(json.dumps(it) + "\n")

        # 准备更新条目：p1 用标准斜杠 DOI 格式更新，p3 是新增
        new_items = [
            {
                "id": "https://openalex.org/W111111",
                "abs": "https://doi.org/10.1109/tgrs.2026.0001",
                "title": "Old Paper 1",
                "summary": "This is a newly backfilled and complete abstract for Paper 1."
            },
            {
                "id": "https://openalex.org/W333333",
                "abs": "https://doi.org/10.1109/tgrs.2026.0003",
                "title": "Brand New Paper 3",
                "summary": "Summary for paper 3"
            }
        ]

        rep_count, app_count = upsert_jsonl_file(jsonl_path, new_items)
        assert rep_count == 1
        assert app_count == 1

        results = []
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                results.append(json.loads(line))

        assert len(results) == 3
        # 确认 p1 的 summary 被正确更新覆盖
        assert results[0]["summary"] == "This is a newly backfilled and complete abstract for Paper 1."
        assert results[1]["summary"] == "Existing summary 2"
        assert results[2]["title"] == "Brand New Paper 3"


def _backfill_args(skip_db_sync):
    return argparse.Namespace(
        date=None,
        days_range=None,
        from_date=None,
        to_date=None,
        max_workers=2,
        language="Chinese",
        model_name="test-model",
        skip_ai=False,
        all_journals=True,
        ieee_only=False,
        journals=None,
        skip_db_sync=skip_db_sync,
        only_sync=False,
        dry_run=False,
    )


class TrackedDict(dict):
    """支持弱引用的 dict 子类，用于验证对象是否被同步列表持有。"""


def _run_backfill_main(skip_db_sync, dates):
    """运行 main()，返回 (增强结果弱引用记录, 同步调用参数)。

    全部使用普通函数替换，避免 Mock 记录调用参数而额外持有对象。
    """
    states = []
    sync_groups = []

    def fake_fetch(journal, from_date, to_date, api_key=""):
        return [{
            "id": f"W-{from_date}",
            "title": f"Paper {from_date}",
            "summary": "A valid abstract long enough to be processed by the pipeline.",
        }]

    def fake_enhance(papers, model_name, language, max_workers):
        results = [TrackedDict({**p, "AI": {"tldr": "summary"}}) for p in papers]
        states.append([weakref.ref(item) for item in results])
        if len(states) == len(dates):
            gc.collect()
            states[0] = [ref() is not None for ref in states[0]]
        return results

    def fake_upsert(filepath, items):
        return 0, 1

    def fake_sync(groups=None):
        sync_groups.append(groups)

    args = _backfill_args(skip_db_sync)
    replacements = [
        ("parse_args", lambda: args),
        ("get_target_journals", lambda _args: [{"name": "TGRS", "category": "IEEE TGRS", "issns": []}]),
        ("get_target_dates", lambda _args: dates),
        ("get_existing_dates", lambda: []),
        ("fetch_and_format_journal_papers", fake_fetch),
        ("enhance_papers_with_ai", fake_enhance),
        ("upsert_jsonl_file", fake_upsert),
        ("sync_database_stats", fake_sync),
    ]
    patchers = [patch.object(backfill_ieee_papers, name, new=value) for name, value in replacements]
    for patcher in patchers:
        patcher.start()
    try:
        with redirect_stdout(io.StringIO()):
            backfill_ieee_papers.main()
    finally:
        for patcher in reversed(patchers):
            patcher.stop()
    gc.collect()

    return states, sync_groups


def test_skip_db_sync_releases_previous_date_objects():
    dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
    states, sync_groups = _run_backfill_main(skip_db_sync=True, dates=dates)

    # 处理到第三个日期时，第一个日期的增强结果已不在同步列表中，可被回收
    assert states[0] == [False]
    assert sync_groups == []


def test_db_sync_keeps_all_dates_and_triggers_once():
    dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
    states, sync_groups = _run_backfill_main(skip_db_sync=False, dates=dates)

    # 同步模式下第一天的结果由待同步列表持有，仍需保留
    assert states[0] == [True]
    assert len(sync_groups) == 1
    assert [group[0] for group in sync_groups[0]] == dates


def test_enhance_uses_bounded_sliding_window():
    papers = [{"id": f"p{i}", "title": f"Paper {i}"} for i in range(6)]

    def fake_process(chain, item, language):
        time.sleep(0.02)
        if item["id"] == "p2":
            raise RuntimeError("boom")
        if item["id"] == "p4":
            return None
        assert item is not papers[int(item["id"][1:])]  # 传入的是副本
        item["mutated"] = True
        return {"id": item["id"], "AI": {"tldr": "ok"}}

    FakeTqdm.instances = []
    TrackingExecutor.instances = []
    with patch("ai.enhance.build_chain", return_value="chain"), \
            patch("ai.enhance.process_single_item", side_effect=fake_process), \
            patch.object(backfill_ieee_papers, "ThreadPoolExecutor", TrackingExecutor), \
            patch.object(backfill_ieee_papers, "tqdm", FakeTqdm):
        results = backfill_ieee_papers.enhance_papers_with_ai(
            papers, model_name="test", language="Chinese", max_workers=2
        )

    executor = TrackingExecutor.instances[0]

    # 异常与 None 被隔离，其余结果按完成顺序收集
    assert sorted(r["id"] for r in results) == ["p0", "p1", "p3", "p5"]
    assert executor.submitted == len(papers)
    assert executor.max_outstanding <= 2
    assert len(FakeTqdm.instances) == 1
    assert FakeTqdm.instances[0].total == len(papers)
    assert FakeTqdm.instances[0].updates == len(papers)
    assert all("mutated" not in paper for paper in papers)


def test_enhance_empty_input_skips_executor():
    with patch.object(backfill_ieee_papers, "ThreadPoolExecutor", side_effect=AssertionError("不应创建线程池")):
        assert backfill_ieee_papers.enhance_papers_with_ai(
            [], model_name="test", language="Chinese", max_workers=2
        ) == []


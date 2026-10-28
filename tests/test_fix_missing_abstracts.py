import argparse
import gc
import os
import sys
import json
import tempfile
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fix_missing_abstracts
from fix_missing_abstracts import (
    is_abstract_missing,
    extract_identifiers,
    atomic_write_jsonl,
    update_enhanced_file
)


class TrackedDict(dict):
    """支持弱引用的 dict 子类，用于验证对象是否被同步列表持有。"""


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


def test_is_abstract_missing():
    assert is_abstract_missing("") is True
    assert is_abstract_missing(None) is True
    assert is_abstract_missing("   ") is True
    assert is_abstract_missing("Too short") is True
    assert is_abstract_missing("A" * 49) is True
    assert is_abstract_missing("A" * 50) is False
    assert is_abstract_missing("No abstract available in OpenAlex.") is True
    assert is_abstract_missing("no abstract available.") is True
    assert is_abstract_missing("No abstract available") is True
    assert is_abstract_missing("None") is True

    valid_abstract = "This is a comprehensive study exploring remote sensing and deep learning methods in detail."
    assert is_abstract_missing(valid_abstract) is False


def test_extract_identifiers():
    # Case 1: DOI in abs url
    item1 = {
        "id": "10.1109_tgrs.2026.12345",
        "abs": "https://doi.org/10.1109/tgrs.2026.12345",
        "pdf": ""
    }
    doi, oa_id, arxiv_url = extract_identifiers(item1)
    assert doi == "10.1109/tgrs.2026.12345"
    assert oa_id == ""
    assert arxiv_url == ""

    # Case 2: OpenAlex ID in id and abs
    item2 = {
        "id": "W7167662976",
        "abs": "https://openalex.org/W7167662976",
        "pdf": ""
    }
    doi, oa_id, arxiv_url = extract_identifiers(item2)
    assert doi == ""
    assert oa_id == "W7167662976"

    # Case 3: arXiv paper
    item3 = {
        "id": "2607.12345",
        "abs": "https://arxiv.org/abs/2607.12345",
        "pdf": "https://arxiv.org/pdf/2607.12345"
    }
    doi, oa_id, arxiv_url = extract_identifiers(item3)
    assert arxiv_url == "https://arxiv.org/abs/2607.12345"


def test_update_enhanced_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        enhanced_file = os.path.join(tmpdir, "test_enhanced.jsonl")
        
        # 写入两条初始条目（一条是旧摘要的条目，一条是其他条目）
        initial_items = [
            {
                "id": "p1",
                "abs": "https://doi.org/10.1109/tgrs.2026.0001",
                "title": "Paper 1",
                "summary": "Old summary",
                "AI": {"tldr": "Old TLDR"}
            },
            {
                "id": "p2",
                "abs": "https://doi.org/10.1109/tgrs.2026.0002",
                "title": "Paper 2",
                "summary": "Existing summary",
                "AI": {"tldr": "Keep this"}
            }
        ]
        atomic_write_jsonl(enhanced_file, initial_items)

        # 准备新生成的更新条目（更新 p1，并新增 p3）
        new_items = [
            {
                "id": "p1",
                "abs": "https://doi.org/10.1109/tgrs.2026.0001",
                "title": "Paper 1",
                "summary": "New repaired detailed summary",
                "AI": {"tldr": "Brand New Improved TLDR"}
            },
            {
                "id": "p3",
                "abs": "https://doi.org/10.1109/tgrs.2026.0003",
                "title": "Paper 3",
                "summary": "Appended paper summary",
                "AI": {"tldr": "Appended TLDR"}
            }
        ]

        update_enhanced_file(enhanced_file, new_items)

        # 读取并验证
        results = []
        with open(enhanced_file, "r", encoding="utf-8") as f:
            for line in f:
                results.append(json.loads(line))

        assert len(results) == 3
        # 验证 p1 被就地替换
        p1_res = [r for r in results if r["id"] == "p1"][0]
        assert p1_res["AI"]["tldr"] == "Brand New Improved TLDR"
        assert p1_res["summary"] == "New repaired detailed summary"

        # 验证 p2 保持不变
        p2_res = [r for r in results if r["id"] == "p2"][0]
        assert p2_res["AI"]["tldr"] == "Keep this"

        # 验证 p3 被追加
        p3_res = [r for r in results if r["id"] == "p3"][0]
        assert p3_res["AI"]["tldr"] == "Appended TLDR"


def test_update_enhanced_file_preserves_existing_ai_when_only_summary_is_repaired():
    with tempfile.TemporaryDirectory() as tmpdir:
        enhanced_file = os.path.join(tmpdir, "test_enhanced.jsonl")
        atomic_write_jsonl(enhanced_file, [{
            "id": "p1",
            "title": "Paper 1",
            "summary": "No abstract available",
            "AI": {"tldr": "Existing TLDR"}
        }])

        update_enhanced_file(enhanced_file, [{
            "id": "p1",
            "title": "Paper 1",
            "summary": "A repaired English abstract with enough detail."
        }])

        with open(enhanced_file, "r", encoding="utf-8") as f:
            result = json.loads(f.readline())

        assert result["summary"] == "A repaired English abstract with enough detail."
        assert result["AI"]["tldr"] == "Existing TLDR"


def test_parse_args_days_range():
    import sys
    from unittest.mock import patch
    from fix_missing_abstracts import parse_args

    with patch.object(sys, "argv", ["fix_missing_abstracts.py", "--days-range", "15", "30"]):
        args = parse_args()
        assert args.days_range == [15, 30]

    with patch.object(sys, "argv", ["fix_missing_abstracts.py", "--from-date", "2026-07-01", "--to-date", "2026-07-15"]):
        args = parse_args()
        assert args.from_date == "2026-07-01"
        assert args.to_date == "2026-07-15"


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
            patch.object(fix_missing_abstracts, "ThreadPoolExecutor", TrackingExecutor), \
            patch.object(fix_missing_abstracts, "tqdm", FakeTqdm):
        results = fix_missing_abstracts.enhance_papers_with_ai(
            papers, model_name="test", language="Chinese", max_workers=2
        )

    executor = TrackingExecutor.instances[0]
    assert sorted(r["id"] for r in results) == ["p0", "p1", "p3", "p5"]
    assert executor.submitted == len(papers)
    assert executor.max_outstanding <= 2
    assert len(FakeTqdm.instances) == 1
    assert FakeTqdm.instances[0].updates == len(papers)
    assert all("mutated" not in paper for paper in papers)


def test_enhance_empty_input_skips_executor():
    with patch.object(fix_missing_abstracts, "ThreadPoolExecutor", side_effect=AssertionError("不应创建线程池")):
        assert fix_missing_abstracts.enhance_papers_with_ai(
            [], model_name="test", language="Chinese", max_workers=2
        ) == []


def _run_fix_main(skip_db_sync):
    """运行 main()，返回 (每日增强结果存活情况, 同步调用参数)。"""
    dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
    states = []
    sync_calls = []

    def fake_rescue(item, fetcher, api_key=""):
        return "A recovered abstract long enough to pass the missing check.", "mock"

    def fake_enhance(papers, model_name, language, max_workers):
        results = [TrackedDict({**p, "AI": {"tldr": "summary"}}) for p in papers]
        states.append([weakref.ref(item) for item in results])
        return results

    def fake_sync(groups=None):
        sync_calls.append(groups)

    args = argparse.Namespace(
        date=None,
        days_range=None,
        from_date=dates[0],
        to_date=dates[-1],
        max_workers=2,
        language="Chinese",
        model_name="test-model",
        skip_ai=False,
        dry_run=False,
        skip_db_sync=skip_db_sync,
    )

    replacements = [
        ("parse_args", lambda: args),
        ("rescue_paper_abstract", fake_rescue),
        ("enhance_papers_with_ai", fake_enhance),
        ("update_enhanced_file", lambda *args: None),
        ("atomic_write_jsonl", lambda *args: None),
        ("sync_database", fake_sync),
    ]
    patchers = [patch.object(fix_missing_abstracts, name, new=value) for name, value in replacements]
    for patcher in patchers:
        patcher.start()
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = os.path.join(tmpdir, "data")
            os.makedirs(data_dir)
            for idx, date in enumerate(dates):
                # 第三个文件摘要完整，不产生同步分组
                summary = "A complete abstract long enough to skip the repair path." if idx == 2 else "No abstract"
                with open(os.path.join(data_dir, f"{date}.jsonl"), "w", encoding="utf-8") as f:
                    f.write(json.dumps({"id": f"W-{date}", "title": "Paper", "date": date, "summary": summary}) + "\n")
            with patch.object(fix_missing_abstracts, "PROJECT_ROOT", tmpdir):
                fix_missing_abstracts.main()
    finally:
        for patcher in reversed(patchers):
            patcher.stop()
    gc.collect()

    states = [[ref() is not None for ref in refs] for refs in states]
    return states, sync_calls


def test_skip_db_sync_releases_previous_date_objects():
    states, sync_calls = _run_fix_main(skip_db_sync=True)

    # 处理后续日期时，前一日的同步副本不再被收集列表引用
    assert states[0] == [False]
    assert sync_calls == []


def test_db_sync_keeps_all_dates_and_triggers_once():
    states, sync_calls = _run_fix_main(skip_db_sync=False)

    assert states[0] == [True]
    assert len(sync_calls) == 1
    assert [group[0] for group in sync_calls[0]] == ["2026-01-01", "2026-01-02"]

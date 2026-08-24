import os
import sys
import json
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fix_missing_abstracts import (
    is_abstract_missing,
    extract_identifiers,
    atomic_write_jsonl,
    update_enhanced_file
)

def test_is_abstract_missing():
    assert is_abstract_missing("") is True
    assert is_abstract_missing(None) is True
    assert is_abstract_missing("   ") is True
    assert is_abstract_missing("Too short") is True
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

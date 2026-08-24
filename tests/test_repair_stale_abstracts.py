import json

from repair_stale_abstracts import repair_file


def test_repair_file_copies_raw_abstract_and_preserves_ai(tmp_path):
    raw_path = tmp_path / "2026-08-19.jsonl"
    enhanced_path = tmp_path / "2026-08-19_AI_enhanced_Chinese.jsonl"
    raw_path.write_text(json.dumps({
        "id": "paper-1",
        "summary": "A valid repaired English abstract with sufficient detail."
    }) + "\n", encoding="utf-8")
    enhanced_path.write_text(json.dumps({
        "id": "paper-1",
        "summary": "No abstract available",
        "AI": {"tldr": "Keep this result"}
    }) + "\n", encoding="utf-8")

    repaired = repair_file(str(raw_path), str(enhanced_path))

    assert len(repaired) == 1
    result = json.loads(enhanced_path.read_text(encoding="utf-8"))
    assert result["summary"] == "A valid repaired English abstract with sufficient detail."
    assert result["AI"]["tldr"] == "Keep this result"


def test_repair_file_dry_run_does_not_write(tmp_path):
    raw_path = tmp_path / "2026-08-19.jsonl"
    enhanced_path = tmp_path / "2026-08-19_AI_enhanced_Chinese.jsonl"
    raw_path.write_text(json.dumps({"id": "p1", "summary": "A valid repaired English abstract."}) + "\n", encoding="utf-8")
    original = json.dumps({"id": "p1", "summary": "No abstract available"}) + "\n"
    enhanced_path.write_text(original, encoding="utf-8")

    assert len(repair_file(str(raw_path), str(enhanced_path), dry_run=True)) == 1
    assert enhanced_path.read_text(encoding="utf-8") == original


def test_repair_file_matches_cross_format_doi_and_ids(tmp_path):
    raw_path = tmp_path / "2026-08-19.jsonl"
    enhanced_path = tmp_path / "2026-08-19_AI_enhanced_Chinese.jsonl"
    
    # 原始文件使用 raw id 下划线形式
    raw_path.write_text(json.dumps({
        "id": "10.1109_tgrs.2026.0001",
        "doi": "10.1109/TGRS.2026.0001",
        "title": "Deep Learning for SAR Image Segmentation",
        "summary": "This is a comprehensive deep learning study on SAR images."
    }) + "\n", encoding="utf-8")

    # 增强文件使用 OpenAlex ID，且 abs 含有 standard DOI
    enhanced_path.write_text(json.dumps({
        "id": "https://openalex.org/W999999",
        "abs": "https://doi.org/10.1109/tgrs.2026.0001",
        "title": "Deep Learning for SAR Image Segmentation",
        "summary": "No abstract available in OpenAlex",
        "AI": {"tldr": "SAR deep learning model"}
    }) + "\n", encoding="utf-8")

    repaired = repair_file(str(raw_path), str(enhanced_path))
    assert len(repaired) == 1

    result = json.loads(enhanced_path.read_text(encoding="utf-8"))
    assert result["summary"] == "This is a comprehensive deep learning study on SAR images."
    assert result["AI"]["tldr"] == "SAR deep learning model"


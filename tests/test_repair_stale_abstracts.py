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

import json
import sqlite3
from unittest.mock import patch

from server_modules import processor


def test_reextract_keywords_for_papers_only_updates_target_papers(tmp_path):
    db_path = str(tmp_path / "statistics.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    processor._init_tables(cursor)

    old_target = {
        "id": "target",
        "title": "Target",
        "summary": "old abstract",
        "categories": ["cs.CV"],
    }
    untouched = {
        "id": "untouched",
        "title": "Untouched",
        "summary": "stable abstract",
        "categories": ["cs.CV"],
    }
    cursor.executemany(
        "INSERT INTO papers (paper_id, paper_date, language, paper_json) VALUES (?, ?, ?, ?)",
        [
            ("target", "2026-08-19", "Chinese", json.dumps(old_target)),
            ("untouched", "2026-08-19", "Chinese", json.dumps(untouched)),
        ],
    )
    cursor.executemany(
        "INSERT INTO paper_keywords (paper_id, paper_date, language, category, keyword) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("target", "2026-08-19", "Chinese", "cs.CV", "old-keyword"),
            ("untouched", "2026-08-19", "Chinese", "cs.CV", "keep-keyword"),
        ],
    )
    cursor.executemany(
        "INSERT INTO keyword_stats (paper_date, language, category, keyword, frequency) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("2026-08-19", "Chinese", "cs.CV", "old-keyword", 3),
            ("2026-08-19", "Chinese", "cs.CV", "keep-keyword", 2),
        ],
    )
    conn.commit()
    conn.close()

    repaired = {**old_target, "summary": "repaired abstract"}

    def fake_extract(papers, batch_size=50):
        result = []
        for paper in papers:
            if paper["summary"] == "old abstract":
                result.append([("old-keyword", 3)])
            elif paper["summary"] == "repaired abstract":
                result.append([("new-keyword", 4)])
            else:
                raise AssertionError("不应重新提取未列入本轮变更集的论文")
        return result

    with patch.object(processor, "get_db_path", return_value=db_path), patch.object(
        processor.keywords, "extract_keywords_batch", side_effect=fake_extract
    ):
        assert processor.reextract_keywords_for_papers(
            [("2026-08-19", "Chinese", [repaired])]
        ) is True

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT paper_id, keyword FROM paper_keywords ORDER BY paper_id")
    assert cursor.fetchall() == [("target", "new-keyword"), ("untouched", "keep-keyword")]
    cursor.execute("SELECT keyword, frequency FROM keyword_stats ORDER BY keyword")
    assert cursor.fetchall() == [("keep-keyword", 2), ("new-keyword", 4)]
    cursor.execute("SELECT paper_json FROM papers WHERE paper_id = 'target'")
    assert json.loads(cursor.fetchone()[0])["summary"] == "repaired abstract"
    conn.close()

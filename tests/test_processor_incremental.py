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
        "AI": {"keywords": ["old-keyword"]},
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
            ("2026-08-19", "Chinese", "cs.CV", "old-keyword", 1),
            ("2026-08-19", "Chinese", "cs.CV", "keep-keyword", 2),
        ],
    )
    conn.commit()
    conn.close()

    repaired = {**old_target, "summary": "repaired abstract", "AI": {"keywords": ["new-keyword"]}}

    with patch.object(processor, "get_db_path", return_value=db_path):
        assert processor.reextract_keywords_for_papers(
            [("2026-08-19", "Chinese", [repaired])]
        ) is True

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT paper_id, keyword FROM paper_keywords ORDER BY paper_id")
    assert cursor.fetchall() == [("target", "new-keyword"), ("untouched", "keep-keyword")]
    cursor.execute("SELECT keyword, frequency FROM keyword_stats ORDER BY keyword")
    assert cursor.fetchall() == [("keep-keyword", 2), ("new-keyword", 1)]
    cursor.execute("SELECT paper_json FROM papers WHERE paper_id = 'target'")
    assert json.loads(cursor.fetchone()[0])["summary"] == "repaired abstract"
    conn.close()

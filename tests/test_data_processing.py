import json
import sqlite3
from unittest.mock import patch

import pytest

import maintenance
from server_modules import processor


@pytest.fixture
def processing_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "completed"
    data_dir.mkdir()
    db_path = tmp_path / "db/statistics.db"
    processor.processed_files_cache.clear()
    monkeypatch.setattr(processor.keywords, "idf_cache", {})
    monkeypatch.setattr(processor.keywords, "idf_doc_count", 0)

    def extract(items, batch_size=50):
        return [[(paper["title"].lower(), 3)] for paper in items]

    monkeypatch.setattr(processor.keywords, "extract_keywords_batch", extract)
    return data_dir, db_path


def write_papers(data_dir, date="2026-10-12", title="Old", ids=("paper",)):
    path = data_dir / f"{date}_AI_enhanced_Chinese.jsonl"
    path.write_text("".join(json.dumps({
        "id": paper_id, "title": title, "summary": "Summary", "categories": ["cs.CV"],
    }) + "\n" for paper_id in ids))
    return path


def rows(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        conn.close()


def test_new_unchanged_and_repaired_files(processing_env):
    data_dir, db_path = processing_env
    path = write_papers(data_dir)
    assert processor.scan_and_process_files(data_dir, db_path) == 1
    before = rows(db_path, "keyword_stats")
    with patch.object(processor.keywords, "extract_keywords_batch", side_effect=AssertionError("unchanged file")):
        assert processor.scan_and_process_files(data_dir, db_path) == 0
    assert rows(db_path, "keyword_stats") == before

    write_papers(data_dir, title="Repaired", ids=("paper", "new-paper"))
    assert processor.scan_and_process_files(data_dir, db_path) == 1
    assert all(json.loads(row[3])["title"] == "Repaired" for row in rows(db_path, "papers"))
    assert [(row[4], row[5]) for row in rows(db_path, "keyword_stats")] == [("repaired", 6)]
    assert rows(db_path, "agg_daily_papers") == [("2026-10-12", "Chinese", "cs.CV", 2)]
    assert rows(db_path, "agg_daily_keywords") == [("2026-10-12", "Chinese", "cs.CV", "repaired", 2)]
    marker = rows(db_path, "processed_files")[0]
    assert marker[2:] == (path.stat().st_mtime_ns, path.stat().st_size)


def test_legacy_database_migration_and_no_double_count(processing_env):
    data_dir, db_path = processing_env
    write_papers(data_dir)
    db_path.parent.mkdir()
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE processed_files (filename TEXT PRIMARY KEY, processed_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("INSERT INTO processed_files (filename) VALUES ('2026-10-12_AI_enhanced_Chinese.jsonl')")
    conn.commit()
    conn.close()
    assert processor.scan_and_process_files(data_dir, db_path) == 1
    assert len(rows(db_path, "processed_files")[0]) == 4
    assert processor.scan_and_process_files(data_dir, db_path) == 0
    assert rows(db_path, "keyword_stats")[0][5] == 3


@pytest.mark.parametrize("failure", ["nlp", "json", "changed"])
def test_failed_processing_keeps_previous_snapshot(processing_env, failure):
    data_dir, db_path = processing_env
    path = write_papers(data_dir)
    processor.scan_and_process_files(data_dir, db_path)
    tables = ("papers", "processed_files", "keyword_stats", "paper_keywords", "agg_daily_keywords", "agg_daily_papers")
    snapshot = {table: rows(db_path, table) for table in tables}
    write_papers(data_dir, title="New")
    if failure == "json":
        path.write_text('{"id":"truncated"')
        context = patch.object(processor.keywords, "extract_keywords_batch", return_value=[])
    elif failure == "changed":
        def change(items, batch_size=50):
            path.write_text(path.read_text() + "\n")
            return [[("new", 3)]]
        context = patch.object(processor.keywords, "extract_keywords_batch", side_effect=change)
    else:
        context = patch.object(processor.keywords, "extract_keywords_batch", side_effect=RuntimeError("NLP failed"))
    with context, pytest.raises((RuntimeError, ValueError)):
        processor.scan_and_process_files(data_dir, db_path)
    assert {table: rows(db_path, table) for table in tables} == snapshot
    write_papers(data_dir, title="Recovered")
    assert processor.scan_and_process_files(data_dir, db_path) == 1
    assert rows(db_path, "keyword_stats")[0][4:] == ("recovered", 3)


def test_empty_file_replaces_previous_date(processing_env):
    data_dir, db_path = processing_env
    path = write_papers(data_dir)
    processor.scan_and_process_files(data_dir, db_path)
    path.write_text("")
    assert processor.scan_and_process_files(data_dir, db_path) == 1
    for table in ("papers", "keyword_stats", "paper_keywords", "agg_daily_papers", "agg_daily_keywords"):
        assert rows(db_path, table) == []
    assert len(rows(db_path, "processed_files")) == 1


def test_cross_date_dedup_and_new_history(processing_env):
    data_dir, db_path = processing_env
    write_papers(data_dir, date="2026-10-11", ids=("shared",))
    write_papers(data_dir, ids=("shared", "unique"))
    assert processor.scan_and_process_files(data_dir, db_path) == 2
    assert {(row[0], row[1]) for row in rows(db_path, "papers")} == {
        ("shared", "2026-10-11"), ("unique", "2026-10-12"),
    }
    write_papers(data_dir, date="2026-10-11", title="Weekend repair", ids=("shared", "backfilled"))
    assert processor.scan_and_process_files(data_dir, db_path) == 1
    assert len(rows(db_path, "papers")) == 3
    assert rows(db_path, "agg_daily_papers") == [
        ("2026-10-12", "Chinese", "cs.CV", 1), ("2026-10-11", "Chinese", "cs.CV", 2),
    ]


def test_duplicates_are_skipped_before_nlp_with_bounded_history_queries(processing_env):
    data_dir, db_path = processing_env
    write_papers(data_dir, date="2026-10-11", ids=("shared",))
    processor.scan_and_process_files(data_dir, db_path)
    write_papers(data_dir, ids=("shared", "unique", "unique", *[f"new-{i}" for i in range(60)], "unique"))
    extracted_ids = []
    statements = []
    conn = sqlite3.connect(db_path)
    conn.set_trace_callback(statements.append)

    def extract(items, batch_size=50):
        extracted_ids.extend(str(paper["id"]) for paper in items)
        return [[("keyword", 1)] for _ in items]

    with patch.object(processor, "connect_db", return_value=conn), patch.object(
        processor.keywords, "extract_keywords_batch", side_effect=extract,
    ):
        assert processor.scan_and_process_files(data_dir, db_path) == 1

    assert len(extracted_ids) == 61
    assert len(set(extracted_ids)) == 61
    assert "shared" not in extracted_ids
    history_queries = [sql for sql in statements if "SELECT DISTINCT paper_id FROM papers" in sql]
    assert len(history_queries) == 2
    assert all("paper_id IN (" in sql for sql in history_queries)
    assert not any("SELECT paper_id FROM papers WHERE language" in sql for sql in statements)
    assert len(rows(db_path, "papers")) == 62


def test_data_process_cli_uses_custom_paths(processing_env, capsys):
    data_dir, db_path = processing_env
    write_papers(data_dir)
    args = ["data-process", "--data-dir", str(data_dir), "--db-path", str(db_path)]
    assert maintenance.main(args) == 0
    assert "更新 1 个" in capsys.readouterr().err
    assert maintenance.main(args) == 0
    assert "更新 0 个" in capsys.readouterr().err
    assert maintenance.main(["data-process", "--data-dir", str(data_dir / "missing"), "--db-path", str(db_path)]) == 1


def test_data_process_cli_propagates_failure(processing_env):
    data_dir, db_path = processing_env
    write_papers(data_dir)
    with patch.object(processor.keywords, "extract_keywords_batch", side_effect=RuntimeError("NLP failed")):
        assert maintenance.main(["data-process", "--data-dir", str(data_dir), "--db-path", str(db_path)]) == 1

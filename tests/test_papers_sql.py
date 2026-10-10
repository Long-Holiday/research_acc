import json
import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import server
from server_modules import papers, processor


@pytest.fixture
def paper_db(tmp_path, monkeypatch):
    path = str(tmp_path / "statistics.db")
    conn = sqlite3.connect(path)
    processor._init_tables(conn.cursor())
    records = [
        {"id": "z", "title": "ÄBC 100%_literal", "summary": "first", "categories": ["cs.CV", "cs.AI"], "authors": ["Ålice", "Bob"]},
        {"id": "a", "title": "Other", "summary": "Vision in summary", "categories": "cs.AI", "authors": "Carol", "AI": {"tldr": "TLDR needle"}},
        {"id": "b", "title": "Empty cats", "summary": "last", "categories": [], "authors": ["David"]},
    ]
    conn.executemany("INSERT INTO papers VALUES (?, ?, ?, ?)", [
        (record["id"], "2026-10-12", "Chinese", json.dumps(record)) for record in records
    ])
    conn.execute("INSERT INTO processed_files (filename) VALUES ('2026-10-12_AI_enhanced_Chinese.jsonl')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(server, "DB_PATH", path, raising=False)
    monkeypatch.setattr(server, "ACCESS_PASSWORD", "")
    return path


@pytest.mark.parametrize("range_mode", [False, True])
@pytest.mark.parametrize("filters,ids", [
    ({"category": "cs.AI"}, ["z", "a"]),
    ({"keyword": "äbc"}, ["z"]),
    ({"keyword": "100%_literal"}, ["z"]),
    ({"keyword": "%"}, ["z"]),
    ({"keyword": "_"}, ["z"]),
    ({"keyword": "VISION"}, ["a"]),
    ({"keyword": "needle"}, ["a"]),
    ({"author": "åLICE BOB"}, ["z"]),
    ({"author": "carol"}, ["a"]),
    ({"category": "cs.CV", "author": "Carol"}, []),
    ({"category": "all"}, ["z", "a", "b"]),
])
def test_sql_filters_keep_existing_semantics(paper_db, range_mode, filters, ids):
    client = TestClient(server.app)
    if range_mode:
        url = "/api/papers/range"
        scope = {"start_date": "2026-10-12", "end_date": "2026-10-12"}
        ids = sorted(ids)
    else:
        url = "/api/papers"
        scope = {"date": "2026-10-12"}
    response = client.get(url, params={**scope, "lang": "Chinese", "page": 1, "page_size": 10, **filters})
    assert response.status_code == 200, response.text
    data = response.json()
    assert [item["id"] for item in data["items"]] == ids
    assert data["total"] == len(ids)
    assert data["category_counts"] == {"cs.CV": 1, "cs.AI": 1, "unknown": 1}
    assert data["total_all"] == 3


def test_date_and_range_order_and_compatibility(paper_db):
    conn = sqlite3.connect(paper_db)
    conn.execute("INSERT INTO papers VALUES (?, ?, ?, ?)", (
        "older", "2026-10-11", "Chinese", json.dumps({"id": "older", "categories": ["cs.CV"]}),
    ))
    conn.commit()
    conn.close()
    result = papers.query_papers("2026-10-11", "2026-10-12", "Chinese", 2, 2, None, None, None)
    assert [item["id"] for item in result["items"]] == ["z", "older"]
    assert result["items"][0]["date"] == "2026-10-12"
    assert result["total_pages"] == 2
    full = papers.query_papers("2026-10-12", "2026-10-12", "Chinese", None, 2, None, None, None, True)
    assert [item["id"] for item in full] == ["z", "a", "b"]
    assert all("date" not in item for item in full)
    empty = papers.query_papers("2026-10-12", "2026-10-12", "Chinese", 10, 2, None, None, None, True)
    assert empty["items"] == []
    assert empty["total"] == 3


def test_empty_processed_date_and_unprocessed_file(paper_db, tmp_path, monkeypatch):
    conn = sqlite3.connect(paper_db)
    conn.execute("INSERT INTO processed_files (filename) VALUES ('2026-10-13_AI_enhanced_Chinese.jsonl')")
    conn.commit()
    conn.close()
    client = TestClient(server.app)
    response = client.get("/api/papers?date=2026-10-13&lang=Chinese&page=1")
    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert client.get("/api/papers?date=2026-10-13&lang=Chinese").json() == []
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data/2026-10-14_AI_enhanced_Chinese.jsonl").write_text('{"id":"not-indexed"}\n')
    assert client.get("/api/papers?date=2026-10-14&lang=Chinese&page=1").status_code == 404
    dates = client.get("/api/dates").json()
    assert dates["dates"] == ["2026-10-13", "2026-10-12"]


@pytest.mark.parametrize("single_date", [False, True])
def test_sql_limit_and_only_page_json_is_decoded(paper_db, single_date):
    conn = sqlite3.connect(paper_db)
    conn.executemany("INSERT INTO papers VALUES (?, ?, ?, ?)", (
        (f"bulk-{i:05}", "2026-10-12", "Chinese", json.dumps({
            "id": f"bulk-{i:05}", "title": "bulk", "summary": "x" * 1024,
            "categories": ["cs.CV"],
        })) for i in range(2000)
    ))
    conn.commit()
    statements = []
    conn.set_trace_callback(statements.append)
    with patch.object(papers, "connect_db", return_value=conn), patch.object(papers.json, "loads", wraps=json.loads) as decode:
        result = papers.query_papers("2026-10-12", "2026-10-12", "Chinese", 2, 21, None, None, None, single_date)
        assert decode.call_count == 21
    assert len(result["items"]) == 21
    assert result["total"] == 2003
    assert any("LIMIT 21 OFFSET 21" in sql for sql in statements)
    assert any("COUNT(*)" in sql for sql in statements)
    assert any("GROUP BY" in sql for sql in statements)


def test_missing_database_returns_no_data_without_scanning(tmp_path, monkeypatch):
    path = tmp_path / "missing.db"
    monkeypatch.setattr(server, "DB_PATH", str(path), raising=False)
    monkeypatch.setattr(server, "ACCESS_PASSWORD", "")
    client = TestClient(server.app)
    with patch.object(processor, "scan_and_process_files", side_effect=AssertionError("must not scan")):
        assert client.get("/api/dates").json() == {"dates": [], "languages": {}}
        assert client.get("/api/papers?date=2026-10-12&lang=Chinese&page=1").status_code == 404
        response = client.get("/api/papers/range?start_date=2026-10-12&end_date=2026-10-12&lang=Chinese&page=1")
        assert response.status_code == 200
        assert response.json()["items"] == []
    assert not path.exists()


def test_invalid_legacy_json_is_skipped(paper_db):
    conn = sqlite3.connect(paper_db)
    conn.execute("INSERT INTO papers VALUES ('broken', '2026-10-12', 'Chinese', 'bad-json')")
    conn.commit()
    conn.close()
    result = papers.query_papers("2026-10-12", "2026-10-12", "Chinese", 1, 10, None, "vision", None)
    assert result["total"] == 1
    assert result["total_all"] == 3


def test_range_order_uses_composite_index(paper_db):
    conn = sqlite3.connect(paper_db)
    try:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT paper_json FROM papers "
            "WHERE paper_date BETWEEN ? AND ? AND language = ? "
            "ORDER BY paper_date DESC, paper_id ASC LIMIT ? OFFSET ?",
            ("2026-10-11", "2026-10-12", "Chinese", 21, 21),
        ).fetchall()
    finally:
        conn.close()
    details = " ".join(row[3] for row in plan)
    assert "idx_papers_lang_date_id" in details
    assert "TEMP B-TREE" not in details

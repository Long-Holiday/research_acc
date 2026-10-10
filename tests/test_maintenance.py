import io
import json
import subprocess
import sys
from unittest.mock import patch

import pytest

import maintenance
from server_modules import processor
from server_modules.database import connect_db
from server_modules.stats import query_keyword_stats


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "statistics.db")
    maintenance.initialize_database(path)
    return path


def test_help_has_no_business_imports():
    result = subprocess.run(
        [sys.executable, "maintenance.py", "--help"], capture_output=True, text=True,
    )
    assert result.returncode == 0
    for command in ("data-process", "advisor-backfill", "advisor-regenerate", "keywords-reextract", "keywords-filter"):
        assert command in result.stdout
        command_help = subprocess.run(
            ["bash", "run_maintenance.sh", command, "--help"], capture_output=True, text=True,
        )
        assert command_help.returncode == 0, command_help.stderr


@pytest.mark.parametrize("arguments", [
    ["advisor-regenerate", "--date", "2026-02-30"],
    ["advisor-regenerate", "--date", "20260812"],
    ["advisor-regenerate"],
    ["keywords-filter"],
    ["keywords-filter", "--start-date", "2026-08-12", "--end-date", "2026-08-01"],
    ["keywords-filter", "--keywords-file", "-", "--start-date", "2026-08-01"],
])
def test_invalid_parameters(arguments):
    with pytest.raises(SystemExit) as exc:
        maintenance.main(arguments)
    assert exc.value.code == 2


def test_regenerate_is_forced(tmp_path, db_path):
    with patch("ai.advisor.generate_advisor_report", return_value={"report_date": "2026-08-12"}) as generate:
        assert maintenance.main([
            "advisor-regenerate", "--date", "2026-08-12", "--topic", "topic",
            "--data-dir", str(tmp_path), "--db-path", db_path,
        ]) == 0
    generate.assert_called_once_with(
        date_str="2026-08-12", topic="topic", force=True, backfill=False,
        data_dir=str(tmp_path), db_path=db_path,
    )


@pytest.mark.parametrize("processed,code", [(["2026-08-01", "2026-08-02"], 0), (["2026-08-01"], 1), ([], 1)])
def test_backfill_only_and_partial_failure(tmp_path, db_path, processed, code):
    with patch("ai.advisor.get_unprocessed_dates", return_value=["2026-08-01", "2026-08-02"]), patch(
        "ai.advisor.backfill_historical_reports", return_value=processed,
    ) as backfill, patch("ai.advisor.generate_advisor_report") as generate:
        assert maintenance.main([
            "advisor-backfill", "--data-dir", str(tmp_path), "--db-path", db_path, "--force",
        ]) == code
    backfill.assert_called_once_with(data_dir=str(tmp_path), db_path=db_path, topic=None, force=True)
    generate.assert_not_called()


def test_reextract_dispatch_and_failure(tmp_path, db_path):
    with patch.object(processor, "reextract_all_keywords", return_value=False) as reextract:
        assert maintenance.main([
            "keywords-reextract", "--data-dir", str(tmp_path), "--db-path", db_path,
        ]) == 1
    reextract.assert_called_once_with(data_dir=str(tmp_path), db_path=db_path)


def test_filter_stdin_json_and_clean_stdout(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO('[" proposed method ", "CLIP", "", "CLIP"]'))

    def fake_filter(keywords, category, model):
        print("business log")
        assert keywords == ["proposed method", "CLIP"]
        assert category == "cs.CV"
        assert model == "test-model"
        return ["proposed method"]

    with patch("ai.keyword_filter.filter_meaningless_keywords", side_effect=fake_filter):
        assert maintenance.main([
            "keywords-filter", "--keywords-file", "-", "--category", "cs.CV", "--model-name", "test-model",
        ]) == 0
    captured = capsys.readouterr()
    assert "business log" in captured.err
    assert json.loads(captured.out) == {
        "status": "success", "excluded_keywords": ["proposed method"], "total_checked": 2, "excluded_count": 1,
    }


@pytest.mark.parametrize("content", ['{}', '[1]', 'not json'])
def test_filter_rejects_invalid_json(tmp_path, content):
    source = tmp_path / "keywords with spaces.json"
    source.write_text(content)
    assert maintenance.main(["keywords-filter", "--keywords-file", str(source)]) == 1


def test_filter_missing_database_does_not_create_it(tmp_path):
    path = tmp_path / "missing.db"
    assert maintenance.main([
        "keywords-filter", "--db-path", str(path), "--start-date", "2026-08-01", "--end-date", "2026-08-12",
    ]) == 1
    assert not path.exists()


def test_filter_database_scope_and_top_100(db_path, capsys):
    conn = connect_db(db_path)
    rows = [("2026-08-01", "Chinese", "cs.CV", f"keyword-{i}", i + 1) for i in range(110)]
    rows.extend([
        ("2026-08-02", "Chinese", "eess.IV", "other-category", 999),
        ("2026-08-01", "en", "cs.CV", "other-language", 9999),
        ("2026-07-01", "Chinese", "cs.CV", "other-date", 9999),
        ("2026-08-01", "Chinese", "cs.AI", "excluded-category", 9999),
    ])
    conn.executemany("INSERT INTO agg_daily_keywords VALUES (?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    expected = query_keyword_stats(db_path, "2026-08-01", "2026-08-02", "Chinese", "cs.CV,eess.IV")
    assert len(expected["keywords"]) == 100
    assert expected["keywords"][0]["keyword"] == "other-category"
    with patch("ai.keyword_filter.filter_meaningless_keywords", return_value=[]) as filtering:
        assert maintenance.main([
            "keywords-filter", "--db-path", db_path, "--start-date", "2026-08-01",
            "--end-date", "2026-08-02", "--lang", "Chinese", "--category", "cs.CV,eess.IV",
        ]) == 0
    filtering.assert_called_once_with(
        [item["keyword"] for item in expected["keywords"]], "cs.CV,eess.IV", None,
    )
    assert json.loads(capsys.readouterr().out)["total_checked"] == 100


def test_reextract_explicit_paths_preserve_advisor(tmp_path, db_path):
    source_dir = tmp_path / "data with spaces"
    source_dir.mkdir()
    source = source_dir / "2026-08-01_AI_enhanced_Chinese.jsonl"
    source.write_text(json.dumps({"id": "paper", "title": "Title", "summary": "Summary", "categories": ["cs.CV"]}) + "\n")
    conn = connect_db(db_path)
    conn.execute("INSERT INTO advisor_reports (report_date, topic, report_markdown) VALUES (?, ?, ?)",
                 ("2026-08-01", "topic", "report"))
    conn.commit()
    conn.close()
    with patch.object(processor.keywords, "extract_keywords_batch", return_value=[[("new keyword", 3)]]):
        assert processor.reextract_all_keywords(data_dir=str(source_dir), db_path=db_path)
    conn = connect_db(db_path)
    assert conn.execute("SELECT keyword FROM paper_keywords").fetchall() == [("new keyword",)]
    assert conn.execute("SELECT distinct_paper_count FROM agg_daily_keywords").fetchone() == (1,)
    assert conn.execute("SELECT filename FROM processed_files").fetchone() == (source.name,)
    assert conn.execute("SELECT report_markdown FROM advisor_reports").fetchone() == ("report",)
    conn.close()


def test_reextract_empty_source_preserves_existing_index(tmp_path, db_path):
    conn = connect_db(db_path)
    conn.execute("INSERT INTO keyword_stats (keyword) VALUES ('keep')")
    conn.commit()
    conn.close()
    assert not processor.reextract_all_keywords(data_dir=str(tmp_path), db_path=db_path)
    conn = connect_db(db_path)
    assert conn.execute("SELECT keyword FROM keyword_stats").fetchone() == ("keep",)
    conn.close()


def test_removed_ui_keeps_reading_and_manual_exclusion():
    root = maintenance.PROJECT_DIR
    advisor = (root / "advisor.html").read_text() + (root / "js/advisor.js").read_text()
    statistics = (root / "statistic.html").read_text() + (root / "js/statistic.js").read_text()
    for identifier in ("backfillBtn", "regenerateBtn", "emptyGenerateBtn", "/api/advisor/generate", "/api/advisor/backfill"):
        assert identifier not in advisor
    for identifier in ("reextractKeywordsBtn", "aiFilterKeywordsBtn", "/api/stats/reextract", "/api/stats/keywords/ai-filter"):
        assert identifier not in statistics
    assert "editTopicBtn" in advisor and "/api/advisor/report" in advisor
    assert "clearExcludeKeywords" in statistics and "updateExcludeKeywords" in statistics
    assert "excludeStatusNotice" in statistics

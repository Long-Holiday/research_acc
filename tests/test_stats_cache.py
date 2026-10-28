import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import server_modules.stats as stats_module


def test_stats_cache_is_bounded():
    stats_module.clear_stats_cache()
    for i in range(stats_module.STATS_CACHE_MAX_ENTRIES + 20):
        stats_module._set_cached_stats(f"key-{i}", {"value": i})

    assert len(stats_module.stats_cache) == stats_module.STATS_CACHE_MAX_ENTRIES
    assert stats_module._get_cached_stats("key-0") is None
    assert stats_module._get_cached_stats(
        f"key-{stats_module.STATS_CACHE_MAX_ENTRIES + 19}"
    ) == {"value": stats_module.STATS_CACHE_MAX_ENTRIES + 19}


def test_stats_cache_purges_expired_entries_on_read_and_write():
    stats_module.clear_stats_cache()
    now = stats_module.time.time()
    stats_module.stats_cache["expired-on-read"] = ({"value": 1}, now - 1)
    stats_module.stats_cache["live"] = ({"value": 2}, now + stats_module.STATS_CACHE_TTL)

    assert stats_module._get_cached_stats("expired-on-read") is None
    assert "expired-on-read" not in stats_module.stats_cache

    stats_module.stats_cache["expired-on-write"] = ({"value": 3}, now - 1)
    stats_module._set_cached_stats("new", {"value": 4})

    assert "expired-on-write" not in stats_module.stats_cache
    assert stats_module._get_cached_stats("live") == {"value": 2}
    assert stats_module._get_cached_stats("new") == {"value": 4}


def test_stats_cache_expires_after_ttl():
    stats_module.clear_stats_cache()
    stats_module._set_cached_stats("key", {"value": 1})
    expires_at = stats_module.stats_cache["key"][1]

    assert expires_at == pytest.approx(
        stats_module.time.time() + stats_module.STATS_CACHE_TTL, abs=1
    )

    stats_module.stats_cache["key"] = ({"value": 1}, expires_at - stats_module.STATS_CACHE_TTL)
    assert stats_module._get_cached_stats("key") is None


def test_clear_stats_cache_removes_all_entries():
    for i in range(5):
        stats_module._set_cached_stats(f"key-{i}", {"value": i})

    stats_module.clear_stats_cache()

    assert stats_module.stats_cache == {}
    assert stats_module._get_cached_stats("key-0") is None


def test_stats_cache_concurrent_access_stays_bounded():
    stats_module.clear_stats_cache()

    def worker(start):
        for i in range(start, start + 40):
            stats_module._set_cached_stats(f"key-{i}", {"value": i})
            stats_module._get_cached_stats(f"key-{i}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(0, 400, 40)))

    assert len(stats_module.stats_cache) <= stats_module.STATS_CACHE_MAX_ENTRIES


def test_clear_stats_cache_is_safe_during_concurrent_access():
    stats_module.clear_stats_cache()

    def writer(start):
        for i in range(start, start + 50):
            stats_module._set_cached_stats(f"key-{i}", {"value": i})

    def reader():
        for i in range(200):
            stats_module._get_cached_stats(f"key-{i}")

    def clearer():
        for _ in range(50):
            stats_module.clear_stats_cache()

    threads = [
        threading.Thread(target=writer, args=(0,)),
        threading.Thread(target=writer, args=(100,)),
        threading.Thread(target=reader),
        threading.Thread(target=clearer),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert len(stats_module.stats_cache) <= stats_module.STATS_CACHE_MAX_ENTRIES


def test_query_keyword_stats_only_calculates_trends_for_returned_top_100(tmp_path, monkeypatch):
    db_path = tmp_path / "stats.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE agg_daily_papers (
            paper_date TEXT, language TEXT, category TEXT, total_papers INTEGER
        );
        CREATE TABLE agg_daily_keywords (
            paper_date TEXT, language TEXT, category TEXT,
            keyword TEXT, distinct_paper_count INTEGER
        );
        """
    )
    conn.executemany(
        "INSERT INTO agg_daily_papers VALUES (?, ?, ?, ?)",
        [("2024-01-01", "en", "cat1", 1000), ("2024-01-02", "en", "cat1", 1000)],
    )
    rows = []
    for i in range(150):
        rows.append(("2024-01-01", "en", "cat1", f"kw-{i:03d}", i + 1))
        rows.append(("2024-01-02", "en", "cat1", f"kw-{i:03d}", i + 1))
    conn.executemany("INSERT INTO agg_daily_keywords VALUES (?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()

    calls = []
    original = stats_module._calculate_keyword_trend

    def counting_trend(date_distribution, total_papers_map):
        calls.append(date_distribution)
        return original(date_distribution, total_papers_map)

    monkeypatch.setattr(stats_module, "_calculate_keyword_trend", counting_trend)

    result = stats_module.query_keyword_stats(str(db_path), "2024-01-01", "2024-01-02")

    assert len(result["keywords"]) == 100
    assert len(calls) == 100
    assert [item["keyword"] for item in result["keywords"]] == [
        f"kw-{i:03d}" for i in range(149, 49, -1)
    ]
    assert all("trend_score" in item for item in result["keywords"])

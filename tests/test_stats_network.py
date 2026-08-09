import sqlite3

import server
import server_modules.stats as stats_module
from server_modules.stats import _filter_network_links


def test_filter_network_links_removes_weak_edges_and_limits_degree():
    links = [
        {"source": "a", "target": "b", "value": 5},
        {"source": "a", "target": "c", "value": 4},
        {"source": "a", "target": "d", "value": 1},
    ]

    result = _filter_network_links(links, min_value=2, max_links=180, max_degree=1)

    assert result == [{"source": "a", "target": "b", "value": 5}]


def test_filter_network_links_has_deterministic_tie_breaking_and_global_limit():
    links = [
        {"source": "b", "target": "c", "value": 2},
        {"source": "a", "target": "d", "value": 2},
        {"source": "a", "target": "c", "value": 3},
    ]

    result = _filter_network_links(links, min_value=2, max_links=2, max_degree=12)

    assert result == [
        {"source": "a", "target": "c", "value": 3},
        {"source": "a", "target": "d", "value": 2},
    ]


def test_network_stats_keeps_cooccurrence_inside_the_requested_scope(tmp_path, monkeypatch):
    db_path = tmp_path / "network.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE keyword_stats (
            paper_date TEXT, language TEXT, category TEXT,
            keyword TEXT, frequency INTEGER
        );
        CREATE TABLE paper_keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id TEXT, paper_date TEXT, language TEXT,
            category TEXT, keyword TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO keyword_stats VALUES (?, ?, ?, ?, ?)",
        [
            ("2024-01-01", "en", "cat1", "a", 3),
            ("2024-01-01", "en", "cat1", "b", 2),
            ("2024-01-02", "en", "cat1", "a", 2),
            ("2024-01-02", "en", "cat1", "c", 2),
            ("2024-01-01", "en", "cat1", "d", 1),
        ],
    )
    conn.executemany(
        """
        INSERT INTO paper_keywords
            (paper_id, paper_date, language, category, keyword)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            ("same-id", "2024-01-01", "en", "cat1", "a"),
            ("same-id", "2024-01-01", "en", "cat1", "b"),
            ("same-id", "2024-01-02", "en", "cat1", "a"),
            ("same-id", "2024-01-02", "en", "cat1", "c"),
            ("same-id", "2024-01-01", "en", "cat2", "a"),
            ("same-id", "2024-01-01", "en", "cat2", "c"),
            ("second-paper", "2024-01-01", "en", "cat1", "a"),
            ("second-paper", "2024-01-01", "en", "cat1", "b"),
            ("weak-paper", "2024-01-01", "en", "cat1", "a"),
            ("weak-paper", "2024-01-01", "en", "cat1", "d"),
        ],
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(server, "DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(stats_module.config, "DB_PATH", str(db_path))

    result = stats_module.get_network_stats(
        start_date="2024-01-01",
        end_date="2024-01-02",
        lang="en",
        category="cat1",
        token="test",
    )

    assert result["links"] == [{"source": "a", "target": "b", "value": 2}]
    assert {node["id"] for node in result["nodes"]} == {"a", "b"}

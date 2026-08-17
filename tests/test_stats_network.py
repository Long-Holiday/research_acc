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


def test_extract_keywords_batch():
    import server_modules.keywords as keywords
    
    # 1. 空输入测试
    assert keywords.extract_keywords_batch([]) == []
    
    # 2. 批量输入测试
    papers = [
        {
            "title": "Vision Transformer in Remote Sensing",
            "summary": "This paper presents a Vision Transformer (ViT) approach for change detection in high resolution remote sensing imagery."
        },
        {
            "title": "Diffusion Models for Super-Resolution",
            "summary": "We propose a diffusion model framework for optical satellite super-resolution."
        }
    ]
    results = keywords.extract_keywords_batch(papers, batch_size=2)
    assert len(results) == 2
    
    # 验证第一篇提取了 ViT / Vision Transformer 或 remote sensing 关键词
    first_kws = [kw for kw, score in results[0]]
    assert any("vision transformer" in k or "transformer" in k or "remote sensing" in k or "vit" in k or "change detection" in k for k in first_kws)
    
    # 验证第二篇提取了 diffusion model 或 super-resolution 关键词
    second_kws = [kw for kw, score in results[1]]
    assert any("diffusion" in k or "super-resolution" in k or "satellite" in k for k in second_kws)
    
    # 3. 超长文本截断健壮性测试
    long_paper = [{
        "title": "A " * 2000,
        "summary": "Diffusion Model " * 2000
    }]
    long_res = keywords.extract_keywords_batch(long_paper, batch_size=1)
    assert len(long_res) == 1

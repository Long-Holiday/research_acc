import json

import pytest

from daily_paper import crawl_openalex


@pytest.mark.parametrize("source", ["openalex", "crossref", "openalex_empty", "openalex_fallback"])
def test_crawler_preserves_affiliations(tmp_path, monkeypatch, source):
    output = tmp_path / "papers.jsonl"
    crossref_paper = {
        "doi": "10.1234/example",
        "title": "Example paper",
        "authors": ["Alice", "Bob"],
        "affiliations": ["Crossref University"],
    }
    openalex_paper = {
        "id": "https://openalex.org/W123",
        "title": "Example paper",
        "doi": "https://doi.org/10.1234/example",
        "authorships": [
            {"author": {"display_name": "Alice"}, "institutions": [
                {"display_name": " University A "}, {"display_name": "University A"},
                {"display_name": None},
            ]},
            {"author": {"display_name": "Bob"}, "institutions": [],
             "raw_affiliation_strings": ["Institute B", ""]},
        ],
    }
    if source == "openalex_empty":
        openalex_paper["authorships"] = []
    monkeypatch.setattr(crawl_openalex, "JOURNALS", [
        {"name": "Example", "category": "Test", "issns": ["1234-5678"]},
    ])
    monkeypatch.setattr(crawl_openalex, "fetch_crossref_papers", lambda *args:
                        [] if source == "openalex_fallback" else [crossref_paper])
    monkeypatch.setattr(crawl_openalex, "fetch_openalex_details_by_dois", lambda *args:
                        {} if source == "crossref" else {"10.1234/example": openalex_paper})
    monkeypatch.setattr(crawl_openalex, "fetch_openalex_single_detail", lambda *args: None)
    monkeypatch.setattr(crawl_openalex, "fetch_openalex_papers", lambda *args: [openalex_paper])
    monkeypatch.setattr(crawl_openalex, "find_arxiv_url", lambda *args: None)
    monkeypatch.setattr(crawl_openalex, "fetch_comprehensive_abstract", lambda **kwargs:
                        ("Example abstract", "crossref"))
    monkeypatch.setattr("sys.argv", ["crawl_openalex.py", "--date", "2026-10-27",
                                     "--output", str(output)])

    crawl_openalex.main()

    paper = json.loads(output.read_text())
    expected = (["Crossref University"] if source in ("crossref", "openalex_empty")
                else ["University A", "Institute B"])
    assert paper["affiliations"] == expected
    assert paper["authors"] == ["Alice", "Bob"]

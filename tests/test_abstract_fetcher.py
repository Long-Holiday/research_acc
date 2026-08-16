import pytest
from unittest.mock import MagicMock, patch
from daily_paper.daily_journals.abstract_fetcher import (
    AbstractFetcher,
    reconstruct_abstract,
    clean_crossref_abstract,
    fetch_comprehensive_abstract
)

def test_reconstruct_abstract():
    inverted_index = {
        "This": [0],
        "is": [1],
        "a": [2],
        "test": [3],
        "abstract.": [4]
    }
    result = reconstruct_abstract(inverted_index)
    assert result == "This is a test abstract."

    assert reconstruct_abstract(None) == ""
    assert reconstruct_abstract({}) == ""
    assert reconstruct_abstract("invalid") == ""


def test_clean_crossref_abstract():
    xml_text = "<jats:p>This is a <b>formatted</b> abstract with <jats:italic>special</jats:italic> XML tags.</jats:p>"
    clean = clean_crossref_abstract(xml_text)
    assert clean == "This is a formatted abstract with special XML tags."

    assert clean_crossref_abstract(None) == ""
    assert clean_crossref_abstract("") == ""


def test_fetch_from_semantic_scholar_success():
    fetcher = AbstractFetcher()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "abstract": "This is a detailed abstract returned by Semantic Scholar Academic Graph API for testing."
    }

    with patch.object(fetcher.session, "get", return_value=mock_resp) as mock_get:
        abs_text = fetcher.fetch_from_semantic_scholar("10.1109/tgrs.2026.1234567")
        assert abs_text == "This is a detailed abstract returned by Semantic Scholar Academic Graph API for testing."
        mock_get.assert_called_once()
        
        # 测试缓存机制
        cached_abs = fetcher.fetch_from_semantic_scholar("10.1109/tgrs.2026.1234567")
        assert cached_abs == abs_text
        assert mock_get.call_count == 1


def test_fetch_from_arxiv_by_title_success():
    fetcher = AbstractFetcher()
    mock_atom_xml = """<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>ADMamba: A Spatio-Temporal Mamba-Inspired Model for Anomaly Detection in Satellite Videos</title>
        <summary>This is the arXiv preprint abstract describing the Mamba architecture in detail.</summary>
      </entry>
    </feed>"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = mock_atom_xml

    with patch.object(fetcher.session, "get", return_value=mock_resp):
        abs_text = fetcher.fetch_from_arxiv_by_title("ADMamba: A Spatio-Temporal Mamba-Inspired Model for Anomaly Detection in Satellite Videos")
        assert abs_text == "This is the arXiv preprint abstract describing the Mamba architecture in detail."


def test_get_abstract_tier_priority():
    fetcher = AbstractFetcher()
    
    # 1. 优先使用 OpenAlex 倒排索引
    oa_inv = {"Remote": [0], "Sensing": [1], "Study": [2], "Details": [3], "Extended": [4]}
    text, src = fetcher.get_abstract(
        doi="10.1109/tgrs.2026.9999999",
        title="Test Title",
        openalex_inverted_index=oa_inv,
        crossref_abstract="<p>Crossref fallback abstract that should not be used if OA exists.</p>"
    )
    assert text == "Remote Sensing Study Details Extended"
    assert src == "openalex"

    # 2. 如果 OpenAlex 没有，使用 arXiv link
    text, src = fetcher.get_abstract(
        doi="10.1109/tgrs.2026.9999999",
        title="Test Title",
        openalex_inverted_index=None,
        arxiv_abstract="Direct arXiv link abstract text with sufficient length for test."
    )
    assert "Direct arXiv link" in text
    assert src == "arxiv_link"

    # 3. 如果 arXiv link 没有，使用 Crossref
    text, src = fetcher.get_abstract(
        doi="10.1109/tgrs.2026.9999999",
        title="Test Title",
        crossref_abstract="<jats:p>Clean Crossref abstract text with sufficient length.</jats:p>"
    )
    assert text == "Clean Crossref abstract text with sufficient length."
    assert src == "crossref"

    # 4. 如果 Crossref 没有，降级到 Semantic Scholar
    mock_s2_resp = MagicMock()
    mock_s2_resp.status_code = 200
    mock_s2_resp.json.return_value = {"abstract": "Semantic Scholar abstract text with sufficient length."}
    with patch.object(fetcher.session, "get", return_value=mock_s2_resp):
        text, src = fetcher.get_abstract(
            doi="10.1109/tgrs.2026.8888888",
            title="Test Title",
            crossref_abstract=""
        )
        assert text == "Semantic Scholar abstract text with sufficient length."
        assert src == "semantic_scholar"


def test_fetch_comprehensive_abstract_function():
    # 测试快捷入口函数
    oa_inv = {"Global": [0], "Observations": [1], "Validation": [2], "Experiments": [3], "Analysis": [4]}
    text, src = fetch_comprehensive_abstract(openalex_inverted_index=oa_inv)
    assert text == "Global Observations Validation Experiments Analysis"
    assert src == "openalex"

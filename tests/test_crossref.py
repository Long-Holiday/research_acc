import os
import sys
import pytest
from unittest.mock import MagicMock, patch
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daily_paper.daily_journals.crossref import (
    clean_crossref_abstract,
    fetch_crossref_papers
)


def test_clean_crossref_abstract():
    # 正常清理 XML / JATS 标签
    raw_xml = "<jats:p>This is a <jats:bold>test</jats:bold> abstract with multiple <jats:italic>tags</jats:italic>.</jats:p>"
    cleaned = clean_crossref_abstract(raw_xml)
    assert cleaned == "This is a test abstract with multiple tags."

    # 空值与 None 处理
    assert clean_crossref_abstract(None) == ""
    assert clean_crossref_abstract("") == ""
    assert clean_crossref_abstract("   ") == ""


def test_fetch_crossref_papers_success():
    mock_items = [
        {
            "DOI": "10.1109/TGRS.2026.1234567",
            "title": ["Deep Learning for InSAR Deformation Mapping"],
            "author": [
                {"given": "San", "family": "Zhang"},
                {"given": "Si", "family": "Li"}
            ],
            "abstract": "<jats:p>Sample abstract text here.</jats:p>"
        }
    ]
    
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "message": {
            "items": mock_items,
            "next-cursor": None
        }
    }

    with patch("requests.Session.get", return_value=mock_resp) as mock_get:
        papers = fetch_crossref_papers(
            issn_list=["0196-2892"],
            from_date="2026-08-10",
            to_date="2026-08-16",
            request_delay=0.0
        )
        
        assert len(papers) == 1
        paper = papers[0]
        assert paper["doi"] == "10.1109/tgrs.2026.1234567"
        assert paper["title"] == "Deep Learning for InSAR Deformation Mapping"
        assert paper["authors"] == ["San Zhang", "Si Li"]
        assert paper["abstract"] == "Sample abstract text here."


def test_fetch_crossref_papers_429_retry_and_recovery():
    # 模拟第一次返回 429，第二次重试返回 200
    mock_429 = MagicMock()
    mock_429.status_code = 429
    mock_429.headers = {"Retry-After": "0"}

    mock_200 = MagicMock()
    mock_200.status_code = 200
    mock_200.json.return_value = {
        "message": {
            "items": [
                {
                    "DOI": "10.1109/TGRS.2026.9999999",
                    "title": ["Recovered Paper After 429"],
                    "author": [{"given": "Alice", "family": "Smith"}],
                    "abstract": "Abstract after backoff."
                }
            ],
            "next-cursor": None
        }
    }

    # 一共 2 个 filter_schemes (pub-date 和 created-date)
    # 第一次 scheme: 429 后恢复 200；第二次 scheme: 直接 200 返回空
    mock_empty_200 = MagicMock()
    mock_empty_200.status_code = 200
    mock_empty_200.json.return_value = {"message": {"items": [], "next-cursor": None}}

    with patch("requests.Session.get", side_effect=[mock_429, mock_200, mock_empty_200]), \
         patch("time.sleep") as mock_sleep:
        
        papers = fetch_crossref_papers(
            issn_list=["0196-2892"],
            from_date="2026-08-10",
            to_date="2026-08-16",
            request_delay=0.0,
            max_retries=2
        )

        assert len(papers) == 1
        assert papers[0]["doi"] == "10.1109/tgrs.2026.9999999"
        # 验证触发了 sleep 退避
        assert mock_sleep.called


def test_fetch_crossref_papers_custom_mailto():
    with patch.dict(os.environ, {"CROSSREF_MAILTO": "custom@example.com"}):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": {"items": [], "next-cursor": None}}

        with patch("requests.Session.get", return_value=mock_resp) as mock_get:
            fetch_crossref_papers(
                issn_list=["0196-2892"],
                from_date="2026-08-10",
                to_date="2026-08-16",
                request_delay=0.0
            )

            # 验证请求参数和 headers 中带入了 custom@example.com
            call_kwargs = mock_get.call_args[1]
            assert call_kwargs["params"]["mailto"] == "custom@example.com"
            assert "custom@example.com" in call_kwargs["headers"]["User-Agent"]

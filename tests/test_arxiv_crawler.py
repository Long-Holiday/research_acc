import pytest
from unittest.mock import MagicMock, patch
from scrapy.http import HtmlResponse, Request
from daily_paper.daily_arxiv.spiders.arxiv import ArxivSpider
from daily_paper.daily_arxiv.pipelines import DailyArxivPipeline


SAMPLE_ARXIV_HTML = """
<!DOCTYPE html>
<html>
<body>
<div id="dlpage">
  <ul>
    <li><a href="#item0">New submissions</a></li>
    <li><a href="#item2">Cross-lists</a></li>
    <li><a href="#item3">Replacements</a></li>
  </ul>
  <dl id="articles">
    <dt>
      <a name="item1">[1]</a>
      <a href="/abs/2609.12078" title="Abstract">arXiv:2609.12078</a>
      [<a href="/pdf/2609.12078" title="Download PDF">pdf</a>]
    </dt>
    <dd>
      <div class="meta">
        <div class="list-title mathjax"><span class="descriptor">Title:</span> Deep Learning for Vision Tasks</div>
        <div class="list-authors"><a href="/search?author=Alice">Alice Smith</a>, <a href="/search?author=Bob">Bob Jones</a></div>
        <div class="list-comments mathjax"><span class="descriptor">Comments:</span> 12 pages, 5 figures</div>
        <div class="list-subjects"><span class="descriptor">Subjects:</span> <span class="primary-subject">Computer Vision and Pattern Recognition (cs.CV)</span>; Artificial Intelligence (cs.AI)</div>
        <p class="mathjax">This is an extensive abstract describing vision tasks.</p>
      </div>
    </dd>
    <dt>
      <a name="item2">[2]</a>
      <a href="/abs/2609.12079" title="Abstract">arXiv:2609.12079</a>
      [<a href="/pdf/2609.12079" title="Download PDF">pdf</a>]
    </dt>
    <dd>
      <div class="meta">
        <div class="list-title mathjax"><span class="descriptor">Title:</span> Robotics and Edge Systems</div>
        <div class="list-authors"><a href="/search?author=Carol">Carol Danvers</a></div>
        <div class="list-subjects"><span class="descriptor">Subjects:</span> <span class="primary-subject">Robotics (cs.RO)</span>; Computer Vision and Pattern Recognition (cs.CV)</div>
        <p class="mathjax">Cross-list paper abstract covering robotics and vision.</p>
      </div>
    </dd>
    <dt>
      <a name="item3">[3]</a>
      <a href="/abs/2609.11000" title="Abstract">arXiv:2609.11000</a>
      [<a href="/pdf/2609.11000" title="Download PDF">pdf</a>]
    </dt>
    <dd>
      <div class="meta">
        <div class="list-title mathjax"><span class="descriptor">Title:</span> Replacement Paper Old</div>
        <div class="list-authors"><a href="/search?author=Dave">Dave Miller</a></div>
        <div class="list-subjects"><span class="descriptor">Subjects:</span> <span class="primary-subject">Computer Vision and Pattern Recognition (cs.CV)</span></div>
        <p class="mathjax">Should be skipped because it is in Replacements section.</p>
      </div>
    </dd>
  </dl>
</div>
</body>
</html>
"""


def test_arxiv_spider_parse_fields():
    spider = ArxivSpider()
    spider.target_categories = {"cs.CV"}
    
    request = Request(url="https://arxiv.org/list/cs.CV/new")
    response = HtmlResponse(
        url="https://arxiv.org/list/cs.CV/new",
        body=SAMPLE_ARXIV_HTML.encode("utf-8"),
        request=request
    )
    
    items = list(spider.parse(response))
    # item1 (new) 和 item2 (cross-list 包含 cs.CV) 应该被解析出来，item3 (replacements) 应该被跳过
    assert len(items) == 2
    
    item1 = items[0]
    assert item1["id"] == "2609.12078"
    assert item1["title"] == "Deep Learning for Vision Tasks"
    assert item1["authors"] == ["Alice Smith", "Bob Jones"]
    assert item1["comment"] == "12 pages, 5 figures"
    assert set(item1["categories"]) == {"cs.CV", "cs.AI"}
    assert item1["summary"] == "This is an extensive abstract describing vision tasks."
    assert item1["pdf"] == "https://arxiv.org/pdf/2609.12078"
    assert item1["abs"] == "https://arxiv.org/abs/2609.12078"
    
    item2 = items[1]
    assert item2["id"] == "2609.12079"
    assert item2["title"] == "Robotics and Edge Systems"
    assert item2["authors"] == ["Carol Danvers"]
    assert item2["comment"] is None
    assert set(item2["categories"]) == {"cs.RO", "cs.CV"}
    assert item2["summary"] == "Cross-list paper abstract covering robotics and vision."


def test_arxiv_pipeline_no_api_when_complete():
    pipeline = DailyArxivPipeline()
    spider = ArxivSpider()
    
    item = {
        "id": "2609.12078",
        "title": "Existing Title",
        "summary": "Existing Summary",
        "authors": ["Author One"],
        "categories": ["cs.CV"],
        "comment": "10 pages",
        "pdf": "https://arxiv.org/pdf/2609.12078",
        "abs": "https://arxiv.org/abs/2609.12078"
    }
    
    # 模拟 client.results，若被调用则抛异常以验证零 API 调用
    with patch.object(pipeline.client, "results", side_effect=AssertionError("Should not call arXiv API")):
        result = pipeline.process_item(item, spider)
        assert result["title"] == "Existing Title"
        assert result["summary"] == "Existing Summary"


def test_arxiv_pipeline_fallback_handles_429():
    pipeline = DailyArxivPipeline()
    spider = ArxivSpider()
    
    item = {
        "id": "2609.99999",
        "title": "",  # 缺少标题和摘要
        "summary": "",
    }
    
    # 模拟 API 抛出异常（如 429）
    with patch.object(pipeline.client, "results", side_effect=Exception("HTTP 429 Too Many Requests")):
        result = pipeline.process_item(item, spider)
        # 应被捕获并赋予默认安全值，而不是直接崩溃
        assert result["id"] == "2609.99999"
        assert result["pdf"] == "https://arxiv.org/pdf/2609.99999"
        assert result["abs"] == "https://arxiv.org/abs/2609.99999"
        assert isinstance(result["authors"], list)
        assert isinstance(result["summary"], str)

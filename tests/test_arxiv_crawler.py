import pytest
from scrapy.exceptions import DropItem
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
    
    assert pipeline.process_item(item, spider) is item


@pytest.mark.parametrize("field", ["title", "summary"])
@pytest.mark.parametrize("value", [None, ""])
def test_arxiv_pipeline_drops_incomplete_papers(field, value):
    item = {"id": "2609.99999", "title": "Title", "summary": "Summary"}
    if value is None:
        del item[field]
    else:
        item[field] = value

    with pytest.raises(DropItem, match=field):
        DailyArxivPipeline().process_item(item, ArxivSpider())


def test_arxiv_spider_categories_from_environment(monkeypatch):
    monkeypatch.setenv("CATEGORIES", " cs.CV, ,cs.CL,cs.CV ")
    spider = ArxivSpider()
    assert spider.target_categories == {"cs.CV", "cs.CL"}
    assert spider.start_urls == [
        "https://arxiv.org/list/cs.CL/new",
        "https://arxiv.org/list/cs.CV/new",
    ]


def test_arxiv_spider_without_replacements():
    html = SAMPLE_ARXIV_HTML.replace(
        '<li><a href="#item3">Replacements</a></li>', ""
    )
    response = HtmlResponse(url="https://arxiv.org/list/cs.CV/new", body=html.encode())
    spider = ArxivSpider()
    spider.target_categories = {"cs.CV"}
    assert len(list(spider.parse(response))) == 3


def test_arxiv_spider_filters_other_categories():
    response = HtmlResponse(
        url="https://arxiv.org/list/cs.CL/new", body=SAMPLE_ARXIV_HTML.encode()
    )
    spider = ArxivSpider()
    spider.target_categories = {"cs.CL"}
    assert list(spider.parse(response)) == []


def test_arxiv_spider_keeps_nested_abstract_text():
    html = SAMPLE_ARXIV_HTML.replace(
        "This is an extensive abstract describing vision tasks.",
        "Abstract: This is an <em>extensive</em> abstract describing vision tasks.",
    )
    response = HtmlResponse(url="https://arxiv.org/list/cs.CV/new", body=html.encode())
    spider = ArxivSpider()
    spider.target_categories = {"cs.CV"}
    assert list(spider.parse(response))[0]["summary"] == (
        "This is an extensive abstract describing vision tasks."
    )

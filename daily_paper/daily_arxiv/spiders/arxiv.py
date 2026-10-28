import os
import re

import scrapy


class ArxivSpider(scrapy.Spider):
    name = "arxiv"
    allowed_domains = ["arxiv.org"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        categories = os.environ.get("CATEGORIES", "cs.CV")
        self.target_categories = {cat.strip() for cat in categories.split(",") if cat.strip()}
        self.start_urls = [
            f"https://arxiv.org/list/{cat}/new" for cat in sorted(self.target_categories)
        ]

    def parse(self, response):
        # 列表页依次包含新投稿、交叉投稿和替换更新，只保留前两部分。
        replacements_anchor = None
        for link in response.css("#dlpage ul a"):
            if "replacements" in link.xpath("string(.)").get().lower():
                replacements_anchor = link.attrib["href"].removeprefix("#")
                break

        for paper in response.css("dl dt"):
            paper_anchor = paper.css("a[name^='item']::attr(name)").get()
            if replacements_anchor and paper_anchor == replacements_anchor:
                break

            abstract_link = paper.css("a[title='Abstract']::attr(href)").get()
            if not abstract_link:
                continue
            arxiv_id = abstract_link.removeprefix("/abs/")
            paper_dd = paper.xpath("following-sibling::dd[1]")

            subjects_text = "".join(paper_dd.css("div.list-subjects ::text").getall())
            paper_categories = re.findall(r"\(([^)]+)\)", subjects_text)
            if not self.target_categories.intersection(paper_categories):
                continue

            title_text = " ".join("".join(paper_dd.css("div.list-title ::text").getall()).split())
            title = re.sub(r"^Title:\s*", "", title_text, flags=re.IGNORECASE)
            authors = [a.strip() for a in paper_dd.css("div.list-authors a::text").getall() if a.strip()]
            comment_text = " ".join("".join(paper_dd.css("div.list-comments ::text").getall()).split())
            comment = re.sub(r"^Comments?:\s*", "", comment_text, flags=re.IGNORECASE) or None
            summary_text = " ".join("".join(paper_dd.css("p.mathjax ::text").getall()).split())
            summary = re.sub(r"^Abstract:\s*", "", summary_text, flags=re.IGNORECASE)

            yield {
                "id": arxiv_id,
                "categories": paper_categories,
                "pdf": f"https://arxiv.org/pdf/{arxiv_id}",
                "abs": f"https://arxiv.org/abs/{arxiv_id}",
                "authors": authors,
                "title": title,
                "comment": comment,
                "summary": summary,
            }

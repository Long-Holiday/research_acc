import scrapy
import os
import re


class ArxivSpider(scrapy.Spider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        categories = os.environ.get("CATEGORIES", "cs.CV")
        categories = categories.split(",")
        # 保存目标分类列表，用于后续验证
        self.target_categories = set(map(str.strip, categories))
        self.start_urls = [
            f"https://arxiv.org/list/{cat}/new" for cat in self.target_categories
        ]  # 起始URL（计算机科学领域的最新论文）

    name = "arxiv"  # 爬虫名称
    allowed_domains = ["arxiv.org"]  # 允许爬取的域名

    def parse(self, response):
        # 查找 replacements（替换更新）的起始锚点编号，避免爬取历史更新的旧论文
        replacements_anchor = None
        for li in response.css("div[id=dlpage] ul li"):
            text = li.css("::text").get() or ""
            href = li.css("a::attr(href)").get() or ""
            if "replacements" in text.lower() and "item" in href:
                try:
                    replacements_anchor = int(href.split("item")[-1])
                except ValueError:
                    pass

        # 遍历每篇论文的详细信息 (<dt> 与 <dd> 成对出现)
        for paper in response.css("dl dt"):
            paper_anchor = paper.css("a[name^='item']::attr(name)").get()
            if not paper_anchor:
                continue
                
            try:
                paper_id_num = int(paper_anchor.split("item")[-1])
            except ValueError:
                paper_id_num = None

            # 排除 Replacements 区域中的论文
            if replacements_anchor is not None and paper_id_num is not None and paper_id_num >= replacements_anchor:
                continue

            # 获取论文ID
            abstract_link = paper.css("a[title='Abstract']::attr(href)").get()
            if not abstract_link:
                continue
                
            arxiv_id = abstract_link.split("/")[-1].strip()
            
            # 获取对应的论文描述部分 (dd元素)
            paper_dd = paper.xpath("following-sibling::dd[1]")
            if not paper_dd:
                continue
            
            # 1. 提取标题 (Title) - 去除开头的 "Title:" 标签
            title_text = "".join(paper_dd.css("div.list-title ::text").getall()).strip()
            title = re.sub(r"^Title:\s*", "", title_text, flags=re.IGNORECASE).strip()
            title = " ".join(title.split())
            
            # 2. 提取作者列表 (Authors)
            authors = [a.strip() for a in paper_dd.css("div.list-authors a::text").getall() if a.strip()]
            
            # 3. 提取备注信息 (Comments)
            comments_elem = paper_dd.css("div.list-comments")
            comment = None
            if comments_elem:
                c_text = "".join(comments_elem.css("::text").getall()).strip()
                c_text = re.sub(r"^Comments?:\s*", "", c_text, flags=re.IGNORECASE).strip()
                comment = " ".join(c_text.split()) if c_text else None
            
            # 4. 提取论文分类 (Subjects & Categories)
            subjects_elem = paper_dd.css("div.list-subjects")
            subjects_text = "".join(subjects_elem.css("::text").getall()) if subjects_elem else ""
            categories_in_paper = re.findall(r"\(([^)]+)\)", subjects_text)
            paper_categories = [c.strip() for c in categories_in_paper if c.strip()]
            
            # 5. 提取完整摘要 (Summary / Abstract)
            summary_elem = paper_dd.css("p.mathjax, p")
            summary = ""
            if summary_elem:
                s_text = "".join(summary_elem[0].css("::text").getall()).strip()
                s_text = re.sub(r"^Abstract:\s*", "", s_text, flags=re.IGNORECASE).strip()
                summary = " ".join(s_text.split())

            # 检查论文分类是否与目标分类有交集
            categories_set = set(paper_categories)
            if self.target_categories and not categories_set.intersection(self.target_categories):
                self.logger.debug(f"Skipped paper {arxiv_id} with categories {categories_set} (not in target {self.target_categories})")
                continue

            item = {
                "id": arxiv_id,
                "categories": paper_categories,
                "pdf": f"https://arxiv.org/pdf/{arxiv_id}",
                "abs": f"https://arxiv.org/abs/{arxiv_id}",
                "authors": authors,
                "title": title,
                "comment": comment,
                "summary": summary,
            }
            self.logger.info(f"Parsed paper {arxiv_id}: {title[:50]}... ({len(summary)} chars abstract)")
            yield item

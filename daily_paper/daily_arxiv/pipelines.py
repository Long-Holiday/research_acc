# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: https://docs.scrapy.org/en/latest/topics/item-pipeline.html


# useful for handling different item types with a single interface
import arxiv
import json
import os
import sys
from datetime import datetime, timedelta


class DailyArxivPipeline:
    def __init__(self):
        self.page_size = 100
        # arxiv 4.x Client with built-in rate-limiting and retries
        self.client = arxiv.Client(
            page_size=self.page_size,
            delay_seconds=3.0,
            num_retries=3
        )

    def process_item(self, item: dict, spider):
        if not item.get("pdf"):
            item["pdf"] = f"https://arxiv.org/pdf/{item['id']}"
        if not item.get("abs"):
            item["abs"] = f"https://arxiv.org/abs/{item['id']}"

        # 如果 Spider 已经完整提取了标题与摘要，直接返回，避免触发 arXiv API 429 限流
        if item.get("title") and item.get("summary"):
            return item

        # 降级备用：仅在爬虫未提取到关键信息时通过 arXiv API 补全
        try:
            search = arxiv.Search(
                id_list=[item["id"]],
            )
            paper = next(self.client.results(search))
            if not item.get("authors"):
                item["authors"] = [a.name for a in paper.authors]
            if not item.get("title"):
                item["title"] = paper.title
            if not item.get("categories"):
                item["categories"] = paper.categories
            if item.get("comment") is None:
                item["comment"] = paper.comment
            if not item.get("summary"):
                item["summary"] = paper.summary
        except Exception as e:
            spider.logger.warning(f"Failed to fetch paper {item.get('id')} from arXiv API: {e}")
            item.setdefault("authors", [])
            item.setdefault("title", "")
            item.setdefault("categories", [])
            item.setdefault("comment", None)
            item.setdefault("summary", "")

        return item
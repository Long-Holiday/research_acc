from scrapy.exceptions import DropItem


class DailyArxivPipeline:
    def process_item(self, item: dict, spider):
        missing = [field for field in ("title", "summary") if not item.get(field)]
        if missing:
            raise DropItem(f"Paper {item['id']} missing required fields: {', '.join(missing)}")
        return item

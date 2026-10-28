BOT_NAME = "daily_arxiv"

SPIDER_MODULES = ["daily_arxiv.spiders"]
NEWSPIDER_MODULE = "daily_arxiv.spiders"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 (compatible; daily_arxiv/1.0)"
ROBOTSTXT_OBEY = True
CONCURRENT_REQUESTS = 4
DOWNLOAD_DELAY = 1.0
CONCURRENT_REQUESTS_PER_DOMAIN = 2
CONCURRENT_REQUESTS_PER_IP = 2
DOWNLOAD_TIMEOUT = 30
RETRY_TIMES = 1

ITEM_PIPELINES = {
    "daily_arxiv.pipelines.DailyArxivPipeline": 300,
}

TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
FEED_EXPORT_ENCODING = "utf-8"

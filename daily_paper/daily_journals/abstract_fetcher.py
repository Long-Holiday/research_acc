import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Tuple
import requests

def reconstruct_abstract(inverted_index: Optional[Dict]) -> str:
    """从 OpenAlex abstract_inverted_index 倒排索引还原完整摘要文本"""
    if not inverted_index or not isinstance(inverted_index, dict):
        return ""
    try:
        word_positions = []
        for word, positions in inverted_index.items():
            for pos in positions:
                word_positions.append((pos, word))
        word_positions.sort(key=lambda x: x[0])
        return " ".join([word for _, word in word_positions]).strip()
    except Exception as e:
        print(f"Error reconstructing abstract: {e}", file=sys.stderr)
        return ""


def clean_crossref_abstract(xml_abstract: Optional[str]) -> str:
    """清理 Crossref 中的 JATS XML/HTML 标签，获取纯文本摘要"""
    if not xml_abstract:
        return ""
    clean_text = re.sub(r'<[^>]+>', ' ', str(xml_abstract))
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    return clean_text


class AbstractFetcher:
    """
    多源多级论文摘要获取器 (Multi-tier Abstract Fetcher)
    针对 IEEE (TGRS/JSTARS/GRSL) 等出版物在开放数据源中的元数据碎片化问题，
    提供高鲁棒性的多层级降级抓取流水线：
      Tier 1: OpenAlex 倒排索引还原 (OpenAlex Inverted Index)
      Tier 2: Crossref 原生摘要 (JATS XML Cleaning)
      Tier 3: Semantic Scholar 学术图谱 API (DOI)
      Tier 4: arXiv 预印本检索 (arXiv Title Fuzzy Search)
      Tier 5: Europe PMC 开放学术库 API (DOI)
    """

    def __init__(self, request_timeout: int = 8, min_interval: float = 0.25):
        self.timeout = request_timeout
        self.min_interval = min_interval
        self._last_request_time = 0.0
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "daily-arXiv-ai-enhanced/1.0 (mailto:dw-dengwei@users.noreply.github.com)",
            "Accept": "application/json, text/plain, */*"
        })
        self._cache = {}

    def _rate_limit(self):
        """控制请求频率，防止对第三方学术接口造成压力或触发 429"""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_time = time.time()

    def fetch_from_semantic_scholar(self, doi: str) -> Optional[str]:
        """通过 DOI 查询 Semantic Scholar API 获取摘要"""
        if not doi:
            return None
        clean_doi = doi.lower().replace("https://doi.org/", "").strip()
        if not clean_doi:
            return None

        cache_key = f"s2:{clean_doi}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        self._rate_limit()
        url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{clean_doi}?fields=abstract"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                abstract = data.get("abstract")
                if abstract and isinstance(abstract, str) and len(abstract.strip()) > 20:
                    clean_abs = abstract.strip()
                    self._cache[cache_key] = clean_abs
                    return clean_abs
            elif resp.status_code == 429:
                # 触发限流时稍作避让
                time.sleep(1.0)
        except Exception:
            pass

        self._cache[cache_key] = None
        return None

    def fetch_from_europe_pmc(self, doi: str) -> Optional[str]:
        """通过 DOI 查询 Europe PMC API 获取摘要"""
        if not doi:
            return None
        clean_doi = doi.lower().replace("https://doi.org/", "").strip()
        if not clean_doi:
            return None

        cache_key = f"epmc:{clean_doi}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        self._rate_limit()
        url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:\"{clean_doi}\"&format=json&resultType=core"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("resultList", {}).get("result", [])
                if results:
                    abstract = results[0].get("abstractText")
                    if abstract and isinstance(abstract, str) and len(abstract.strip()) > 20:
                        clean_abs = clean_crossref_abstract(abstract)
                        self._cache[cache_key] = clean_abs
                        return clean_abs
        except Exception:
            pass

        self._cache[cache_key] = None
        return None

    def fetch_from_arxiv_by_title(self, title: str) -> Optional[str]:
        """通过标题模糊匹配检索 arXiv 预印本摘要"""
        if not title or len(title.strip()) < 8:
            return None

        clean_title = re.sub(r'[^a-zA-Z0-9\s]', ' ', title)
        clean_title = " ".join(clean_title.split()).strip()
        if not clean_title or len(clean_title) < 8:
            return None

        cache_key = f"arxiv:{clean_title.lower()}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        self._rate_limit()
        encoded_title = urllib.parse.quote(clean_title)
        url = f"https://export.arxiv.org/api/query?search_query=ti:%22{encoded_title}%22&max_results=1"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200 and "<summary>" in resp.text:
                root = ET.fromstring(resp.text)
                entry = root.find("{http://www.w3.org/2005/Atom}entry")
                if entry is not None:
                    summary_el = entry.find("{http://www.w3.org/2005/Atom}summary")
                    title_el = entry.find("{http://www.w3.org/2005/Atom}title")
                    if summary_el is not None and summary_el.text and title_el is not None and title_el.text:
                        norm_atitle = re.sub(r'[^a-zA-Z0-9\s]', ' ', title_el.text).lower()
                        norm_atitle = " ".join(norm_atitle.split())
                        norm_otitle = clean_title.lower()
                        
                        check_len = min(20, len(norm_otitle))
                        if norm_otitle[:check_len] in norm_atitle or norm_atitle[:check_len] in norm_otitle:
                            abstract = summary_el.text.strip().replace('\n', ' ')
                            self._cache[cache_key] = abstract
                            return abstract
        except Exception:
            pass

        self._cache[cache_key] = None
        return None

    def get_abstract(
        self,
        doi: str = "",
        title: str = "",
        openalex_inverted_index: Optional[Dict] = None,
        crossref_abstract: str = "",
        arxiv_abstract: str = ""
    ) -> Tuple[str, str]:
        """
        统一执行多级摘要获取策略：
        返回: (abstract_text, source_tag)
        """
        # Tier 1: OpenAlex 倒排索引
        oa_abs = reconstruct_abstract(openalex_inverted_index)
        if oa_abs and oa_abs != "No abstract available in OpenAlex." and len(oa_abs.strip()) > 20:
            return oa_abs, "openalex"

        # Tier 2: 直接传入的 arXiv 预解析摘要
        if arxiv_abstract and arxiv_abstract.strip() and len(arxiv_abstract.strip()) > 20:
            return arxiv_abstract.strip(), "arxiv_link"

        # Tier 3: Crossref 原始摘要
        if crossref_abstract and crossref_abstract.strip():
            cr_clean = clean_crossref_abstract(crossref_abstract)
            if cr_clean and len(cr_clean) > 20:
                return cr_clean, "crossref"

        # Tier 4: Semantic Scholar 学术图谱
        if doi:
            s2_abs = self.fetch_from_semantic_scholar(doi)
            if s2_abs:
                return s2_abs, "semantic_scholar"

        # Tier 5: arXiv 标题检索
        if title:
            ar_abs = self.fetch_from_arxiv_by_title(title)
            if ar_abs:
                return ar_abs, "arxiv_title"

        # Tier 6: Europe PMC 开放学术库
        if doi:
            epmc_abs = self.fetch_from_europe_pmc(doi)
            if epmc_abs:
                return epmc_abs, "europe_pmc"

        return "No abstract available.", "missing"


# 全局默认单例
_default_fetcher = AbstractFetcher()

def fetch_comprehensive_abstract(
    doi: str = "",
    title: str = "",
    openalex_inverted_index: Optional[Dict] = None,
    crossref_abstract: str = "",
    arxiv_abstract: str = ""
) -> Tuple[str, str]:
    """快捷调用全局 AbstractFetcher 获取摘要"""
    return _default_fetcher.get_abstract(
        doi=doi,
        title=title,
        openalex_inverted_index=openalex_inverted_index,
        crossref_abstract=crossref_abstract,
        arxiv_abstract=arxiv_abstract
    )

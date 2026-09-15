import re
import sys
import time
import requests
import xml.etree.ElementTree as ET

import threading
from scrapy.selector import Selector

_last_arxiv_request_time = 0.0
_arxiv_lock = threading.Lock()
_session = requests.Session()


def fetch_arxiv_abstract(oa_url):
    """
    优先通过请求 https://arxiv.org/abs/{arxiv_id} 页面极速获取论文摘要（通常 0.5 秒内返回且无 API 限流影响）。
    若网页获取失败，则回退调用 export.arxiv.org 官方 API。
    """
    global _last_arxiv_request_time
    match = re.search(r'arxiv\.org/(?:abs|pdf)/([a-zA-Z\-]+(?:\.[a-zA-Z\-]+)?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?', oa_url)
    if not match:
        return None
    arxiv_id = match.group(1)
    
    if '/' in arxiv_id:
        parts = arxiv_id.split('/')
        if len(parts) == 2 and '.' in parts[0]:
            arxiv_id = f"{parts[0].split('.')[0]}/{parts[1]}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }

    # 1. 优先直接请求 https://arxiv.org/abs/{arxiv_id} 页面（响应极快且不受 API 429 限流影响）
    abs_page_url = f"https://arxiv.org/abs/{arxiv_id}"
    try:
        resp = _session.get(abs_page_url, headers=headers, timeout=10)
        if resp.status_code == 200:
            sel = Selector(text=resp.text)
            abs_text = "".join(sel.css("blockquote.abstract ::text").getall()).strip()
            if abs_text:
                clean_abs = re.sub(r"^Abstract:\s*", "", abs_text, flags=re.IGNORECASE).strip()
                clean_abs = " ".join(clean_abs.split())
                if len(clean_abs) > 20:
                    return clean_abs
    except Exception as e:
        print(f"Direct abs page fetch failed for {arxiv_id}: {e}", file=sys.stderr)

    # 2. 回退：若网页未获取到，使用官方 API 请求 (https://info.arxiv.org/help/api/basics.html)
    api_url = f'https://export.arxiv.org/api/query?id_list={arxiv_id}&max_results=1'
    max_retries = 2

    for attempt in range(max_retries):
        with _arxiv_lock:
            now = time.time()
            elapsed = now - _last_arxiv_request_time
            if elapsed < 3.5:
                time.sleep(3.5 - elapsed)
            _last_arxiv_request_time = time.time()

        try:
            api_resp = _session.get(api_url, headers=headers, timeout=(10, 35))
            if api_resp.status_code == 200:
                root = ET.fromstring(api_resp.content)
                entries = root.findall('{http://www.w3.org/2005/Atom}entry')
                if entries:
                    summary_elem = entries[0].find('{http://www.w3.org/2005/Atom}summary')
                    if summary_elem is not None and summary_elem.text:
                        abstract = summary_elem.text.strip().replace('\n', ' ')
                        return " ".join(abstract.split())
                break
            elif api_resp.status_code == 429:
                time.sleep(3.0 * (attempt + 1))
        except Exception:
            pass

    return None

def find_arxiv_url(paper):
    """Find potential arXiv URL from various fields in OpenAlex response"""
    oa_url = paper.get("open_access", {}).get("oa_url")
    if oa_url and "arxiv.org" in oa_url:
        return oa_url
    
    prim_loc = paper.get("primary_location") or {}
    for field in ["landing_page_url", "pdf_url"]:
        url = prim_loc.get(field)
        if url and "arxiv.org" in url:
            return url
            
    for loc in paper.get("locations", []):
        if not loc:
            continue
        for field in ["landing_page_url", "pdf_url"]:
            url = loc.get(field)
            if url and "arxiv.org" in url:
                return url
                
    return None

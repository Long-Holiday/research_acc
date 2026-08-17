import os
import re
import sys
import time
from typing import List, Dict, Optional
import requests

def clean_crossref_abstract(xml_abstract: Optional[str]) -> str:
    """Clean JATS XML/HTML formatting tags from Crossref abstract text."""
    if not xml_abstract:
        return ""
    # Remove XML tags (JATS format)
    clean_text = re.sub(r'<[^>]+>', ' ', str(xml_abstract))
    clean_text = re.sub(r'\s+([.,;:!?])', r'\1', clean_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    return clean_text

def fetch_crossref_papers(
    issn_list: List[str],
    from_date: str,
    to_date: str,
    request_delay: float = 0.6,
    max_retries: int = 3,
    timeout: int = 25
) -> List[Dict]:
    """Query Crossref to get DOI list and basic metadata for papers in given ISSNs and date range.
    
    Uses both 'from-pub-date/until-pub-date' and 'from-created-date/until-created-date' filters
    to ensure papers from publishers with differing date metadata conventions (e.g., IEEE TGRS)
    are properly captured without omission.
    
    Includes polite rate-limiting intervals and exponential backoff retry for HTTP 429 (Too Many Requests).
    """
    dois_metadata = {}
    
    # 优先从环境变量获取 Polite 邮箱
    mailto = os.environ.get("CROSSREF_MAILTO") or os.environ.get("MAILTO") or "dw-dengwei@users.noreply.github.com"
    mailto = mailto.strip()
    
    # 支持从环境变量调整默认请求间隔
    try:
        env_delay = os.environ.get("CROSSREF_REQUEST_DELAY")
        if env_delay:
            request_delay = float(env_delay)
    except (ValueError, TypeError):
        pass

    headers = {
        "User-Agent": f"daily-arXiv-ai-enhanced/1.0 (mailto:{mailto})"
    }
    
    # 结合出版日期与创建入库日期双重过滤，防止 IEEE 等只填写年份的出版物漏抓
    filter_schemes = [
        ("from-pub-date", "until-pub-date", "publication date"),
        ("from-created-date", "until-created-date", "creation date")
    ]
    
    session = requests.Session()
    last_request_time = 0.0
    
    def _wait_for_rate_limit():
        nonlocal last_request_time
        if request_delay > 0:
            now = time.time()
            elapsed = now - last_request_time
            if elapsed < request_delay:
                time.sleep(request_delay - elapsed)
        last_request_time = time.time()
    
    for issn in issn_list:
        for from_key, until_key, label in filter_schemes:
            cursor = "*"
            page = 1
            while True:
                url = "https://api.crossref.org/works"
                params = {
                    "filter": f"issn:{issn},{from_key}:{from_date},{until_key}:{to_date}",
                    "cursor": cursor,
                    "rows": 100,
                    "mailto": mailto
                }
                print(f"Fetching Crossref ({label}) page {page} for ISSN {issn} ({from_date} to {to_date})...", file=sys.stderr)
                
                resp = None
                success = False
                
                for attempt in range(max_retries + 1):
                    _wait_for_rate_limit()
                    try:
                        resp = session.get(url, params=params, headers=headers, timeout=timeout)
                        
                        if resp.status_code == 200:
                            success = True
                            break
                        
                        if resp.status_code == 429:
                            # 触发限流，计算退避时间
                            retry_after = resp.headers.get("Retry-After")
                            if retry_after and retry_after.isdigit():
                                backoff = float(retry_after) + 0.5
                            else:
                                backoff = max(2.0, (2 ** attempt) * 1.5)
                                
                            if attempt < max_retries:
                                print(
                                    f"Crossref ({label}) status 429 (Too Many Requests) for ISSN {issn}. "
                                    f"Backing off for {backoff:.1f}s (retry {attempt + 1}/{max_retries})...",
                                    file=sys.stderr
                                )
                                time.sleep(backoff)
                                continue
                            else:
                                print(
                                    f"Crossref ({label}) status 429 for ISSN {issn}: Rate limit exceeded after {max_retries} retries.",
                                    file=sys.stderr
                                )
                                break
                                
                        elif resp.status_code in (500, 502, 503, 504):
                            backoff = max(2.0, (2 ** attempt) * 1.5)
                            if attempt < max_retries:
                                print(
                                    f"Crossref ({label}) status {resp.status_code} for ISSN {issn}. "
                                    f"Retrying in {backoff:.1f}s (retry {attempt + 1}/{max_retries})...",
                                    file=sys.stderr
                                )
                                time.sleep(backoff)
                                continue
                            else:
                                print(f"Crossref ({label}) status {resp.status_code} for ISSN {issn}: {resp.text}", file=sys.stderr)
                                break
                        else:
                            # 其他 4xx 错误不进行无意义重试
                            print(f"Crossref ({label}) status {resp.status_code} for ISSN {issn}: {resp.text}", file=sys.stderr)
                            break
                            
                    except requests.exceptions.RequestException as e:
                        backoff = max(2.0, (2 ** attempt) * 1.5)
                        if attempt < max_retries:
                            print(
                                f"Crossref request exception for ISSN {issn} ({label}): {e}. "
                                f"Retrying in {backoff:.1f}s (retry {attempt + 1}/{max_retries})...",
                                file=sys.stderr
                            )
                            time.sleep(backoff)
                            continue
                        else:
                            print(f"Crossref request failed after {max_retries} retries for ISSN {issn} ({label}): {e}", file=sys.stderr)
                            break

                if not success or resp is None:
                    break

                try:
                    data = resp.json()
                except Exception as e:
                    print(f"Failed to parse Crossref JSON response for ISSN {issn} ({label}): {e}", file=sys.stderr)
                    break
                    
                items = data.get("message", {}).get("items", [])
                if not items:
                    break
                
                for item in items:
                    doi = item.get("DOI")
                    if doi:
                        doi_clean = doi.lower().strip()
                        # Extract title
                        titles = item.get("title", [])
                        title = titles[0] if titles else "No Title"
                        
                        # Extract authors
                        authors = []
                        for aut in item.get("author", []):
                            given = aut.get("given", "")
                            family = aut.get("family", "")
                            name = f"{given} {family}".strip()
                            if name:
                                authors.append(name)
                        if not authors:
                            authors = ["Unknown Author"]
                            
                        # Extract abstract
                        abstract_raw = item.get("abstract", "")
                        abstract = clean_crossref_abstract(abstract_raw)
                        
                        # Store or update metadata (preferring non-empty abstract/title)
                        if doi_clean not in dois_metadata or (not dois_metadata[doi_clean].get("abstract") and abstract):
                            dois_metadata[doi_clean] = {
                                "doi": doi_clean,
                                "title": title,
                                "authors": authors,
                                "abstract": abstract
                            }
                
                next_cursor = data.get("message", {}).get("next-cursor")
                if not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor
                page += 1
                
    return list(dois_metadata.values())



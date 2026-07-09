import re
import sys
import time
import requests
import xml.etree.ElementTree as ET

_last_arxiv_request_time = 0.0

def fetch_arxiv_abstract(oa_url):
    """Fallback: fetch abstract from arXiv API if the paper has an arXiv preprint"""
    global _last_arxiv_request_time
    match = re.search(r'arxiv\.org/(?:abs|pdf)/([a-zA-Z\-]+(?:\.[a-zA-Z\-]+)?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?', oa_url)
    if not match:
        return None
    arxiv_id = match.group(1)
    
    if '/' in arxiv_id:
        parts = arxiv_id.split('/')
        if len(parts) == 2 and '.' in parts[0]:
            arxiv_id = f"{parts[0].split('.')[0]}/{parts[1]}"
            
    now = time.time()
    elapsed = now - _last_arxiv_request_time
    if elapsed < 3.0:
        time.sleep(3.0 - elapsed)
    _last_arxiv_request_time = time.time()

    try:
        url = f'https://export.arxiv.org/api/query?id_list={arxiv_id}&max_results=1'
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.content)
            entries = root.findall('{http://www.w3.org/2005/Atom}entry')
            if entries:
                summary_elem = entries[0].find('{http://www.w3.org/2005/Atom}summary')
                if summary_elem is not None and summary_elem.text:
                    abstract = summary_elem.text.strip().replace('\n', ' ')
                    return abstract
    except Exception as e:
        print(f"Failed to fetch arXiv abstract for {arxiv_id}: {e}", file=sys.stderr)
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

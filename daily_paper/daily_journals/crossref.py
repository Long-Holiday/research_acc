import re
import sys
import requests

def clean_crossref_abstract(xml_abstract):
    if not xml_abstract:
        return ""
    # Remove XML tags (JATS format)
    clean_text = re.sub(r'<[^>]+>', ' ', xml_abstract)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    return clean_text

def fetch_crossref_papers(issn_list, from_date, to_date):
    """Query Crossref to get DOI list and basic metadata for papers in given ISSNs and date range.
    
    Uses both 'from-pub-date/until-pub-date' and 'from-created-date/until-created-date' filters
    to ensure papers from publishers with differing date metadata conventions (e.g., IEEE TGRS)
    are properly captured without omission.
    """
    dois_metadata = {}
    headers = {
        "User-Agent": "daily-arXiv-ai-enhanced/1.0 (mailto:dw-dengwei@users.noreply.github.com)"
    }
    
    # 结合出版日期与创建入库日期双重过滤，防止 IEEE 等只填写年份的出版物漏抓
    filter_schemes = [
        ("from-pub-date", "until-pub-date", "publication date"),
        ("from-created-date", "until-created-date", "creation date")
    ]
    
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
                    "mailto": "dw-dengwei@users.noreply.github.com"
                }
                print(f"Fetching Crossref ({label}) page {page} for ISSN {issn} ({from_date} to {to_date})...", file=sys.stderr)
                try:
                    resp = requests.get(url, params=params, headers=headers, timeout=20)
                    if resp.status_code != 200:
                        print(f"Crossref ({label}) status {resp.status_code} for ISSN {issn}: {resp.text}", file=sys.stderr)
                        break
                    data = resp.json()
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
                except Exception as e:
                    print(f"Crossref request exception for ISSN {issn} ({label}): {e}", file=sys.stderr)
                    break
                
    return list(dois_metadata.values())


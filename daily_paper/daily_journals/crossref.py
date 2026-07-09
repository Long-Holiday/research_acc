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
    """Query Crossref to get DOI list and basic metadata for papers in given ISSNs and date range."""
    dois_metadata = {}
    headers = {
        "User-Agent": "daily-arXiv-ai-enhanced/1.0 (mailto:dw-dengwei@users.noreply.github.com)"
    }
    
    for issn in issn_list:
        cursor = "*"
        page = 1
        while True:
            url = "https://api.crossref.org/works"
            params = {
                "filter": f"issn:{issn},from-pub-date:{from_date},until-pub-date:{to_date}",
                "cursor": cursor,
                "rows": 100,
                "mailto": "dw-dengwei@users.noreply.github.com"
            }
            print(f"Fetching Crossref page {page} for ISSN {issn} ({from_date} to {to_date})...", file=sys.stderr)
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=20)
                if resp.status_code != 200:
                    print(f"Crossref error {resp.status_code} for ISSN {issn}: {resp.text}", file=sys.stderr)
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
                        
                        # Store metadata
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
                print(f"Crossref request exception for ISSN {issn}: {e}", file=sys.stderr)
                break
                
    return list(dois_metadata.values())

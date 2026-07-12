import os
import sys
import requests

def reconstruct_abstract(inverted_index):
    if not inverted_index:
        return "No abstract available in OpenAlex."
    try:
        # 找出所有的单词和它们对应的位置，并按位置排序
        word_positions = []
        for word, positions in inverted_index.items():
            for pos in positions:
                word_positions.append((pos, word))
        
        word_positions.sort()
        # 将单词拼接成完整的句子
        return " ".join([word for pos, word in word_positions])
    except Exception as e:
        print(f"Error reconstructing abstract: {e}")
        return "No abstract available in OpenAlex."

def fetch_openalex_details_by_dois(dois_list):
    """Query OpenAlex to get metadata details for a list of DOIs in batches."""
    if not dois_list:
        return {}
        
    headers = {
        "User-Agent": "daily-arXiv-ai-enhanced/1.0 (mailto:dw-dengwei@users.noreply.github.com)"
    }
    
    api_key = os.environ.get("OPENALEX_API_KEY", "")
    details_map = {}
    batch_size = 40
    
    for i in range(0, len(dois_list), batch_size):
        batch = dois_list[i:i+batch_size]
        filter_str = f"doi:{'|'.join(batch)}"
        url = "https://api.openalex.org/works"
        params = {
            "filter": filter_str,
            "per_page": 100
        }
        if api_key:
            params["api_key"] = api_key
            
        print(f"Fetching OpenAlex details for batch {i//batch_size + 1} ({len(batch)} DOIs)...", file=sys.stderr)
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=20)
            if resp.status_code != 200:
                print(f"OpenAlex details error {resp.status_code}: {resp.text}", file=sys.stderr)
                continue
            data = resp.json()
            results = data.get("results", [])
            for paper in results:
                oa_doi_raw = paper.get("doi") or ""
                oa_doi = oa_doi_raw.replace("https://doi.org/", "").lower().strip()
                if not oa_doi:
                    continue
                details_map[oa_doi] = paper
        except Exception as e:
            print(f"OpenAlex batch exception: {e}", file=sys.stderr)
            
    return details_map

def fetch_openalex_single_detail(doi, api_key=None):
    """Fallback: Query OpenAlex for a single DOI to get detailed metadata."""
    headers = {
        "User-Agent": "daily-arXiv-ai-enhanced/1.0 (mailto:dw-dengwei@users.noreply.github.com)"
    }
    url = f"https://api.openalex.org/works/doi:{doi}"
    params = {}
    if api_key:
        params["api_key"] = api_key
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"Failed to fetch single DOI {doi} from OpenAlex: {e}", file=sys.stderr)
    return None

def fetch_openalex_papers(issn_list, from_date, to_date):
    """Fallback query method: Query OpenAlex directly using ISSN and publication date range."""
    issn_str = "|".join(issn_list)
    page = 1
    papers = []
    
    headers = {
        "User-Agent": "daily-arXiv-ai-enhanced/1.0 (mailto:dw-dengwei@users.noreply.github.com)"
    }

    api_key = os.environ.get("OPENALEX_API_KEY", "")
    
    while True:
        url = "https://api.openalex.org/works"
        params = {
            "filter": f"primary_location.source.issn:{issn_str},from_publication_date:{from_date},to_publication_date:{to_date}",
            "per_page": 100,
            "page": page
        }
        if api_key:
            params["api_key"] = api_key
        
        print(f"Fallback Fetching page {page} for ISSNs {issn_list} from {from_date} to {to_date}...", file=sys.stderr)
        try:
            response = requests.get(url, params=params, headers=headers, timeout=30)
            if response.status_code != 200:
                print(f"Failed to fetch: HTTP {response.status_code}. Response: {response.text}", file=sys.stderr)
                break
                
            data = response.json()
            results = data.get("results", [])
            if not results:
                break
                
            papers.extend(results)
            
            meta = data.get("meta", {})
            count = meta.get("count", 0)
            per_page = meta.get("per_page", 100)
            if page * per_page >= count:
                break
                
            page += 1
        except Exception as e:
            print(f"Error during fallback request: {e}", file=sys.stderr)
            break
            
    return papers

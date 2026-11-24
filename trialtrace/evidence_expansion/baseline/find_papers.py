##############################################################################
# trialtrace/evidence_expansion/baseline/find_papers.py
#
# PURPOSE: Find papers that might report a trial, beyond the ones already linked.
#          Two plain searches: by NCT ID, and by words from the trial title.
#          Every paper comes back with HOW it was found, so the source is never lost.
#
# USE:     papers = find_companion_papers(http, settings, nct_id, trial_title, linked_pmids)
##############################################################################

import json
import time

import httpx

from trialtrace.config import Settings
from trialtrace.parse.pubmed_parser import Paper, parse_pubmed_xml
from trialtrace.sources.pubmed import fetch_pubmed_records, search_pubmed

SMALL_WORDS = {"the", "and", "for", "with", "study", "trial", "of", "in", "a", "an", "to", "on", "versus", "vs", "from"}
WAIT_SECONDS = 0.4          # PubMed allows about 3 requests a second without a key


def title_query(trial_title: str) -> str:
    """Keep the longest few words of the title, e.g. 'From Clinic to Community Study' -> 'Community Clinic'."""
    words = []
    for word in trial_title.replace("-", " ").split():
        word = word.strip(".,:;()\"'")
        if word.lower() in SMALL_WORDS:
            continue
        if len(word) < 4:
            continue
        words.append(word)
    words.sort(key=len, reverse=True)
    return " ".join(words[:4])


def search_words(http: httpx.Client, settings: Settings, query: str) -> list[str]:
    """Search PubMed for plain words. Returns paper IDs, or an empty list if anything goes wrong."""
    if not query.strip():
        return []
    url = f"{settings.pubmed_base_url}/esearch.fcgi"
    params = {"db": "pubmed", "term": query, "retmode": "json", "retmax": 10}
    time.sleep(WAIT_SECONDS)
    try:
        response = http.get(url, params=params, timeout=settings.request_timeout_seconds)
        pmids = json.loads(response.content)["esearchresult"]["idlist"]
    except (httpx.HTTPError, ValueError, KeyError):
        return []
    return [str(pmid) for pmid in pmids]


def load_papers(http: httpx.Client, settings: Settings, nct_id: str, pmids: list[str]) -> list[Paper]:
    """Fetch the paper records (title, abstract) for some paper IDs."""
    papers = []
    batch_size = settings.pubmed_batch_size
    for start in range(0, len(pmids), batch_size):
        batch = pmids[start:start + batch_size]
        time.sleep(WAIT_SECONDS)
        fetch = fetch_pubmed_records(http, settings, nct_id, batch)
        if fetch.status == "ok" and fetch.body is not None:
            papers.extend(parse_pubmed_xml(fetch.body))
    return papers


def find_companion_papers(
    http: httpx.Client,
    settings: Settings,
    nct_id: str,
    trial_title: str,
    linked_pmids: list[str],
) -> list[dict]:
    """Papers NOT already linked. Each is {"paper": Paper, "found_by": "nct_search" or "title_search"}."""
    found = {}                                    # pmid -> how it was found

    # 1. Search by NCT ID.
    search = search_pubmed(http, settings, nct_id)
    for pmid in search.pmids:
        if pmid not in linked_pmids:
            found[pmid] = "nct_search"

    # 2. Search by words from the trial title.
    time.sleep(WAIT_SECONDS)
    for pmid in search_words(http, settings, title_query(trial_title)):
        if pmid not in linked_pmids and pmid not in found:
            found[pmid] = "title_search"

    # 3. Load the records for everything new.
    papers = load_papers(http, settings, nct_id, list(found))
    results = []
    for paper in papers:
        results.append({"paper": paper, "found_by": found[paper.pmid]})
    return results

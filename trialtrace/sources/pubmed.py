##############################################################################
# trialtrace/sources/pubmed.py
#
# ---------------------------------------------------------------------------
# PURPOSE
# ---------------------------------------------------------------------------
# Ask PubMed which papers mention one clinical trial, and report what happened.
# PubMed is the free public database of biomedical papers. This file does only
# the first of two PubMed steps:
#
#     1. SEARCH  search_pubmed         NCT ID  ->  a list of paper IDs (PMIDs)
#     2. FETCH   fetch_pubmed_records  PMIDs   ->  the paper records (XML, untouched)
#
# The fetch returns the raw XML bytes only. Saving them is raw_store.save_raw
# (source "pubmed", extension "xml"); turning them into papers is the parser.
#
# A search hit is only a CANDIDATE. It means "this paper mentions the trial's
# ID somewhere", not "this paper reports the trial's results". Deciding which
# candidates really belong to the trial comes in a later lesson.
#
# Key rules this file follows:
#   - Unknown is unknown. A failed search is never returned as "no papers":
#         ok            PubMed answered and listed at least one paper
#         not_found     PubMed answered and listed none (a real answer)
#         unavailable   timeout, network trouble, or a server error: we do not know
#         invalid       PubMed answered, but not in the shape we expect: we do not know
#   - Only a valid NCT ID may go into the search text, never free text.
#   - The search finds only papers that NAME the ID. "not_found" means "none
#     found by this search", not "no paper exists".
#   - If PubMed has more papers than we keep, `truncated` says so; the total
#     is kept so nobody mistakes the kept list for everything.
#   - The API key (optional) is a secret. It is sent to PubMed but is never
#     stored in the result.
#   - It never raises for network problems; it reports them.
#
# ---------------------------------------------------------------------------
# PIPELINE  (what search_pubmed does, step by step)
# ---------------------------------------------------------------------------
# Step 1. Check the NCT ID looks valid               (raises ValueError if not)
#
# Step 2. Build the question: "<NCT ID>"[All Fields], asking for at most
#         settings.pubmed_max_results paper IDs, as JSON.
#
# Step 3. Ask PubMed (esearch)
#         - timeout or network problem  -> status "unavailable"
#         - any answer except 200       -> status "unavailable" (error code http_NNN)
#
# Step 4. Read the answer
#         - not JSON, or missing fields -> status "invalid"
#         - PubMed reports an error     -> status "invalid"
#
# Step 5. Return the result: the paper IDs, how many PubMed has in total, and
#         whether we kept fewer than that.
#
# ---------------------------------------------------------------------------
# RESULT  (PubMedSearch)
# ---------------------------------------------------------------------------
#   nct_id       the trial searched for
#   query        the exact search text sent
#   status       ok | not_found | unavailable | invalid
#   pmids        the paper IDs kept (as text)
#   total_count  how many papers PubMed says match (None if unknown)
#   truncated    True if PubMed has more than the pmids we kept
#   retrieved_at when we asked
#   error_code   why it failed ("timeout", "http_429", "pubmed_error"...)
#
# ---------------------------------------------------------------------------
# FETCH  (what fetch_pubmed_records does, step by step)
# ---------------------------------------------------------------------------
# Step 1. Check the paper IDs: at least one, not more than settings.pubmed_batch_size
#         (default 20), and digits only (ValueError otherwise). Only digits may
#         go into the request, never free text.
#
# Step 2. Ask PubMed (efetch) for those records as XML.
#         - timeout or network problem  -> status "unavailable"
#         - any answer except 200       -> status "unavailable" (error code http_NNN)
#
# Step 3. Return the answer exactly as it came: the bytes are not decoded,
#         trimmed or changed, so the saved file is real evidence. The URL kept
#         in the result never contains the API key.
#
# RESULT  (PubMedFetch)
#   nct_id       the trial whose search produced these paper IDs
#   pmids        the paper IDs asked for
#   url          the request address WITHOUT the API key
#   status       ok | unavailable
#   body         the untouched XML bytes (only when status is ok)
#   retrieved_at when we asked
#   error_code   why it failed
#
# One XML file holds up to a batch of papers. Checking that the XML is well
# formed and splitting it into papers is the parser's job (next lesson).
#
# ---------------------------------------------------------------------------
# USE
# ---------------------------------------------------------------------------
#   with httpx.Client() as http:
#       search = search_pubmed(http, Settings(), "NCT04644237")
#       fetch = fetch_pubmed_records(http, Settings(), "NCT04644237", search.pmids[:20])
#       stored = save_raw(fetch.nct_id, fetch.url, fetch.retrieved_at, fetch.body,
#                         settings.data_dir, source="pubmed", extension="xml")
#   # PubMed allows about 3 requests a second without a key: the caller waits
#   # between requests.
##############################################################################

import json
import re
from datetime import datetime, timezone
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from trialtrace.config import Settings
from trialtrace.contracts import NCT_ID_PATTERN


class PubMedSearch(BaseModel):
    """What happened when we searched PubMed for one trial's NCT ID."""

    nct_id: str
    query: str
    status: Literal["ok", "not_found", "unavailable", "invalid"]
    pmids: list[str] = Field(default_factory=list)
    total_count: int | None = None              # how many PubMed says match, even if we kept fewer
    truncated: bool = False
    retrieved_at: datetime
    error_code: str | None = None               # only filled when we do not know the answer


def search_pubmed(http: httpx.Client, settings: Settings, nct_id: str) -> PubMedSearch:
    """Search PubMed for papers that mention one trial. Never raises for network problems."""
    # Step 1: only a valid ID may go into the search text
    if not re.fullmatch(NCT_ID_PATTERN, nct_id):
        raise ValueError(f"not a valid NCT ID: {nct_id!r}")

    # Step 2: the question and its settings
    query = f'"{nct_id}"[All Fields]'                   # searches titles, abstracts and the trial-ID field
    params = {"db": "pubmed", "term": query, "retmode": "json", "retmax": settings.pubmed_max_results}
    if settings.ncbi_api_key is not None:
        params["api_key"] = settings.ncbi_api_key.get_secret_value()
    url = f"{settings.pubmed_base_url}/esearch.fcgi"
    retrieved_at = datetime.now(timezone.utc)

    def failed(status: str, error_code: str) -> PubMedSearch:
        return PubMedSearch(nct_id=nct_id, query=query, status=status, retrieved_at=retrieved_at, error_code=error_code)

    # Step 3: ask PubMed
    try:
        response = http.get(url, params=params, timeout=settings.request_timeout_seconds)
    except httpx.TimeoutException:                      # must come first: it is a kind of HTTPError
        return failed("unavailable", "timeout")
    except httpx.HTTPError:
        return failed("unavailable", "network_error")
    if response.status_code != 200:
        return failed("unavailable", f"http_{response.status_code}")

    # Step 4: read the answer; anything unexpected is "invalid", not "no papers"
    try:
        result = json.loads(response.content)["esearchresult"]
        if "ERROR" in result:
            return failed("invalid", "pubmed_error")
        pmids = [str(pmid) for pmid in result["idlist"]]
        total_count = int(result["count"])
    except (ValueError, KeyError, TypeError):
        return failed("invalid", "unexpected_response")

    # Step 5: zero hits is an answer ("nothing matched"), not a failure
    return PubMedSearch(
        nct_id=nct_id, query=query,
        status="ok" if pmids else "not_found",
        pmids=pmids, total_count=total_count,
        truncated=total_count > len(pmids),
        retrieved_at=retrieved_at,
    )


class PubMedFetch(BaseModel):
    """What happened when we asked PubMed for the full records of some papers. Raw bytes, not parsed."""

    nct_id: str                                 # the trial whose search produced these paper IDs
    pmids: list[str]
    url: str                                    # the request address WITHOUT the API key
    status: Literal["ok", "unavailable"]
    body: bytes | None = None                   # the untouched XML, only when status is ok
    retrieved_at: datetime
    error_code: str | None = None


def fetch_pubmed_records(http: httpx.Client, settings: Settings, nct_id: str, pmids: list[str]) -> PubMedFetch:
    """Fetch the full records (XML) for a batch of papers. Never raises for network problems."""
    # Step 1: only a small batch of plain numbers may go into the request
    if not re.fullmatch(NCT_ID_PATTERN, nct_id):
        raise ValueError(f"not a valid NCT ID: {nct_id!r}")
    if not pmids:
        raise ValueError("no paper IDs given")
    if len(pmids) > settings.pubmed_batch_size:
        raise ValueError(f"at most {settings.pubmed_batch_size} paper IDs per fetch, got {len(pmids)}")
    for pmid in pmids:
        if not re.fullmatch(r"[0-9]{1,9}", pmid):
            raise ValueError(f"not a valid paper ID: {pmid!r}")

    # Step 2: build the request. The key is a secret, so the address we keep never includes it.
    id_list = ",".join(pmids)
    params = {"db": "pubmed", "id": id_list, "retmode": "xml"}
    if settings.ncbi_api_key is not None:
        params["api_key"] = settings.ncbi_api_key.get_secret_value()
    saved_url = f"{settings.pubmed_base_url}/efetch.fcgi?db=pubmed&retmode=xml&id={id_list}"
    retrieved_at = datetime.now(timezone.utc)

    def failed(error_code: str) -> PubMedFetch:
        return PubMedFetch(nct_id=nct_id, pmids=pmids, url=saved_url, status="unavailable",
                           retrieved_at=retrieved_at, error_code=error_code)

    try:
        response = http.get(f"{settings.pubmed_base_url}/efetch.fcgi", params=params,
                            timeout=settings.request_timeout_seconds)
    except httpx.TimeoutException:                      # must come first: it is a kind of HTTPError
        return failed("timeout")
    except httpx.HTTPError:
        return failed("network_error")
    if response.status_code != 200:
        return failed(f"http_{response.status_code}")

    # Step 3: the answer exactly as it came
    return PubMedFetch(nct_id=nct_id, pmids=pmids, url=saved_url, status="ok",
                       body=response.content, retrieved_at=retrieved_at)

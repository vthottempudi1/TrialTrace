##############################################################################
# trialtrace/ingest_papers.py
#
# PURPOSE: Bring the PubMed papers for ONE trial into the database. Glue, like ingest_registry.py:
#            search_pubmed -> fetch_pubmed_records (batches) -> save_raw
#            -> parse_pubmed_xml -> check_paper_link -> save_papers
#          Every search is recorded (paper_searches), so "searched, nothing matched"
#          is never confused with "could not search" or "never searched".
#          A failed batch is counted, never dropped; the other batches are still saved.
#          Papers that are not confirmed as linked are stored too, with their verdict.
#          Waits briefly before each request (PubMed allows ~3 a second without a key).
#          Does not commit: the caller does.
#
# USE:     result = ingest_papers(conn, http, settings, "NCT04644237")
#          conn.commit()
##############################################################################

import re
import time
from typing import Callable, Literal, Protocol, TypeVar

import httpx
import psycopg
from pydantic import BaseModel, ValidationError

from trialtrace.checks.paper_link import check_paper_link
from trialtrace.config import Settings
from trialtrace.contracts import NCT_ID_PATTERN
from trialtrace.db.repository import save_paper_search, save_papers
from trialtrace.ingest_registry import MAX_ATTEMPTS, is_temporary
from trialtrace.parse.pubmed_parser import ParseError, parse_pubmed_xml
from trialtrace.sources.pubmed import fetch_pubmed_records, search_pubmed
from trialtrace.sources.raw_store import save_raw

PAUSE_SECONDS = 0.4                                 # keeps us under ~3 requests a second


class PaperIngestResult(BaseModel):
    """What happened to one trial's papers."""

    nct_id: str
    status: Literal["invalid_id", "search_failed", "no_papers_found", "done", "partial", "fetch_failed", "error"]   # "error" = unexpected, set by the CLI
    papers_found: int = 0                           # paper IDs the search kept
    papers_new: int = 0                             # papers saved that we did not have
    batches_failed: int = 0
    detail: str | None = None                       # the error code, if any


class HasStatus(Protocol):
    """Any PubMed result we can retry: it has a status and an error code."""

    status: str
    error_code: str | None


Result = TypeVar("Result", bound=HasStatus)         # lets the editor know the result type comes back unchanged


def call_with_retries(call: Callable[[], Result], sleep: Callable[[float], None]) -> tuple[Result, int]:
    """Run one PubMed request: pause first, retry only temporary failures, at most MAX_ATTEMPTS.

    Returns the last result and the number of attempts used.
    """
    attempt = 1
    while True:
        sleep(PAUSE_SECONDS)
        result = call()
        if result.status != "unavailable" or not is_temporary(result.error_code):
            return result, attempt
        if attempt >= MAX_ATTEMPTS:
            return result, attempt
        sleep(2 ** (attempt - 1))                   # 1 second, then 2
        attempt += 1


def ingest_papers(
    conn: psycopg.Connection,
    http: httpx.Client,
    settings: Settings,
    nct_id: str,
    sleep: Callable[[float], None] = time.sleep,
) -> PaperIngestResult:
    """Search, fetch, save and link the papers for one trial. The caller commits."""
    if not re.fullmatch(NCT_ID_PATTERN, nct_id):
        return PaperIngestResult(nct_id=nct_id, status="invalid_id")

    # Step 1: search; a search we could not do is recorded, never "no papers"
    search, attempts = call_with_retries(lambda: search_pubmed(http, settings, nct_id), sleep)
    if search.status in ("unavailable", "invalid"):
        save_paper_search(conn, search, attempts)
        return PaperIngestResult(nct_id=nct_id, status="search_failed", detail=search.error_code)
    if search.status == "not_found":
        save_paper_search(conn, search, attempts)
        return PaperIngestResult(nct_id=nct_id, status="no_papers_found")

    # Step 2: fetch the papers in small batches
    size = settings.pubmed_batch_size
    batches = [search.pmids[i : i + size] for i in range(0, len(search.pmids), size)]
    papers_new, batches_failed, last_error = 0, 0, None

    for batch in batches:
        fetch, _ = call_with_retries(lambda: fetch_pubmed_records(http, settings, nct_id, batch), sleep)
        if fetch.status != "ok" or fetch.body is None:
            batches_failed += 1
            last_error = fetch.error_code or "unknown"
            continue

        # keep the untouched file BEFORE reading it, so a parser bug never loses the original
        stored = save_raw(nct_id, fetch.url, fetch.retrieved_at, fetch.body, settings.data_dir,
                          source="pubmed", extension="xml")
        try:
            papers = parse_pubmed_xml(fetch.body)
        except (ParseError, ValidationError):
            batches_failed += 1
            last_error = "ParseError"
            continue

        links = [check_paper_link(paper, nct_id) for paper in papers]
        papers_new += save_papers(conn, papers, links, stored.content_hash, stored.raw_file_path,
                                  fetch.url, fetch.retrieved_at)

        # PubMed can answer 200 with fewer papers than asked: keep what came, but the batch was not fully read
        if set(batch) - {paper.pmid for paper in papers}:
            batches_failed += 1
            last_error = "missing_papers"

    # Step 3: record the search with its batch counts
    save_paper_search(conn, search, attempts, len(batches), batches_failed, last_error)
    if batches_failed == 0:
        status = "done"
    elif batches_failed == len(batches):
        status = "fetch_failed"                     # every batch failed or came back incomplete
    else:
        status = "partial"
    return PaperIngestResult(
        nct_id=nct_id,
        status=status,
        papers_found=len(search.pmids), papers_new=papers_new,
        batches_failed=batches_failed, detail=last_error,
    )

##############################################################################
# trialtrace/ingest_registry.py
#
# ---------------------------------------------------------------------------
# PURPOSE
# ---------------------------------------------------------------------------
# This file brings ONE clinical trial from ClinicalTrials.gov into our
# database. It is the "glue" that connects modules we already built:
#
#     fetch_study      (sources/registry.py)       -> downloads the trial record
#     save_raw         (sources/raw_store.py)      -> keeps the untouched file
#     parse_study,
#     extract_evidence,
#     parse_outcomes   (parse/registry_parser.py)  -> turns JSON into our models
#     parse_results_outcomes (parse/results_parser.py) -> the reported outcomes
#     save_snapshot,
#     save_failure     (db/repository.py)          -> writes to PostgreSQL
#
# Key rules this file follows:
#   - No silent failures: every EXPECTED problem after the ID check (fetch
#     trouble, an unreadable record, a bad outcomes section) is written to the
#     ingest_failures table with the stage ("fetch" or "parse"), an error
#     code, and how many fetch attempts were made. Unexpected errors (disk,
#     database) are not caught here; they reach the caller (see cli.ingest_many).
#   - The raw file is saved BEFORE parsing, so a parsing bug never loses the
#     original data. We can fix the parser and re-parse later.
#   - This file only collects and stores data. It makes no judgement about
#     the trial (no scoring, no flags, no conclusions).
#   - It does NOT commit the database transaction. The caller decides when
#     to call conn.commit() (for example, after a batch of trials).
#
# ---------------------------------------------------------------------------
# PIPELINE  (what ingest_trial() does, step by step)
# ---------------------------------------------------------------------------
# Step 1. Validate the NCT ID
#         Checks the ID against NCT_ID_PATTERN (e.g. "NCT04644237").
#         Bad format -> return status "invalid_id" immediately.
#         No network call is made and nothing is written to the database.
#
# Step 2. Fetch from the registry, with limited retries (fetch_with_retries)
#         - Up to MAX_ATTEMPTS = 3 tries (1 first try + 2 retries).
#         - Only TEMPORARY errors are retried: timeout, network_error,
#           http_429 (too many requests) and any http_5xx (server error).
#         - Wait time between tries: 1 second, then 2 seconds.
#         Outcomes:
#           - 404 (trial does not exist) -> never retried; recorded in
#             ingest_failures; return status "not_found".
#           - Still failing after retries, or a non-temporary error
#             -> recorded in ingest_failures with the error code;
#             return status "failed" (detail = the error code).
#           - Success -> continue to Step 3.
#
# Step 3. Save the raw file (save_raw)
#         Writes the response body exactly as received into
#         settings.data_dir. The file name is the hash of its content, so
#         the same download is never stored twice.
#
# Step 4. Parse the main record (parse_study + extract_evidence)
#         - parse_study      -> builds the trial snapshot (core fields).
#         - extract_evidence -> builds the evidence linking each field back
#                               to the raw file it came from.
#         If either raises ParseError or ValidationError (pydantic):
#           -> recorded in ingest_failures as "ParseError";
#              return status "failed". The raw file from Step 3 is kept.
#
# Step 5. Parse the outcomes section (parse_outcomes)
#         Outcomes are optional for the pipeline. If this section is broken:
#           -> recorded in ingest_failures as "outcomes_ParseError",
#              outcomes is set to None ("unknown"), and we CONTINUE.
#         A bad outcomes section must not cost us the whole snapshot.
#
# Step 5c. Parse the adverse-event module (parse_safety); a problem is recorded as "safety_ParseError", safety stays unknown.
#         Also builds the evidence the safety finding cites (safety_evidence).
#
# Step 5b. Parse the posted-results outcomes (parse_results_outcomes)
#         The outcomes the trial REPORTED (only trials that posted results have
#         them). Same rule as Step 5: a broken section is recorded as
#         "results_ParseError", results is set to None ("unknown"), we CONTINUE.
#
# Step 6. Store the snapshot (save_snapshot)
#         Saves snapshot + evidence + outcomes + results to the database.
#           - This exact content was never stored for the trial -> "new_version"
#           - This exact content is already stored (even if an
#             older version, as in A -> B -> A)                 -> "unchanged"
#         Either way, today's sighting is added to the observations table.
#
# ---------------------------------------------------------------------------
# RESULT  (IngestResult returned for every trial)
# ---------------------------------------------------------------------------
#   nct_id    the trial ID that was processed
#   status    one of:
#               "new_version"  stored a new version of the trial
#               "unchanged"    same as what we already had; nothing new
#               "not_found"    registry returned 404
#               "failed"       fetch or main parse failed (see detail)
#               "invalid_id"   ID format was wrong; nothing attempted
#               "error"        unexpected crash; set by the CLI, NOT here
#   attempts  how many times we called the registry (0 for "invalid_id")
#   detail    error code for failures (e.g. "timeout", "http_503",
#             "ParseError"), otherwise None
#
# ---------------------------------------------------------------------------
# USE
# ---------------------------------------------------------------------------
#   result = ingest_trial(conn, http, settings, "NCT04644237")
#   conn.commit()                      # caller commits (ingest_trial does not)
#   print(result.status, result.attempts, result.detail)
#
#   conn      open psycopg connection
#   http      httpx.Client (reuse one client for many trials)
#   settings  Settings from trialtrace.config (API URL, data_dir, ...)
#   sleep     optional; tests pass a fake sleep so retries run instantly
##############################################################################

import re
import time
from typing import Callable, Literal

import httpx
import psycopg
from pydantic import BaseModel, ValidationError

from trialtrace.safety.safety_registry import safety_evidence
from trialtrace.config import Settings
from trialtrace.contracts import NCT_ID_PATTERN
from trialtrace.db.repository import save_failure, save_snapshot
from trialtrace.parse.registry_parser import ParseError, extract_evidence, parse_outcomes, parse_study
from trialtrace.parse.results_parser import extract_results_evidence, parse_results_outcomes
from trialtrace.safety.safety_parser import parse_safety
from trialtrace.pipeline_events import record_event, record_started
from trialtrace.sources.raw_store import save_raw
from trialtrace.sources.registry import RegistryFetch, fetch_study

MAX_ATTEMPTS = 3                                    # one try plus two retries


class IngestResult(BaseModel):
    """What happened to one trial."""

    nct_id: str
    status: Literal["new_version", "unchanged", "not_found", "failed", "invalid_id", "error"]   # "error" = unexpected, set by the CLI
    attempts: int = 0                               # how many times we asked the registry
    detail: str | None = None                       # the error code, if any


def is_temporary(error_code: str | None) -> bool:
    """Worth trying again? Timeouts, network trouble, "too many requests" and server errors are."""
    if error_code in ("timeout", "network_error", "http_429"):
        return True
    return error_code is not None and error_code.startswith("http_5")


def fetch_with_retries(
    http: httpx.Client,
    settings: Settings,
    nct_id: str,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[RegistryFetch, int]:
    """Fetch one trial, retrying only temporary failures, at most MAX_ATTEMPTS times.

    Waits 1 second, then 2 seconds, between tries. A 404 or other final answer is never retried.
    Returns the last fetch and the number of attempts used.
    """
    attempt = 1
    while True:
        fetch = fetch_study(http, settings, nct_id)
        if fetch.status != "unavailable" or not is_temporary(fetch.error_code):
            return fetch, attempt
        if attempt >= MAX_ATTEMPTS:
            return fetch, attempt
        sleep(2 ** (attempt - 1))                   # 1 second, then 2
        attempt += 1


def ingest_trial(
    conn: psycopg.Connection,
    http: httpx.Client,
    settings: Settings,
    nct_id: str,
    sleep: Callable[[float], None] = time.sleep,
) -> IngestResult:
    """Fetch, save, parse and store one trial, and record a pipeline event for the attempt. The caller commits."""
    started = time.monotonic()
    started_id = record_started(conn, nct_id, "registry") if re.fullmatch(NCT_ID_PATTERN, nct_id) else None
    try:
        result = _ingest_trial(conn, http, settings, nct_id, sleep)
    except Exception as error:                                    # an unexpected crash is recorded, then raised again
        record_event(conn, nct_id, "registry", "failed", error_code=type(error).__name__, started=started, started_id=started_id,
                     durable=True)         # the caller will roll back, so a normal write would vanish
        raise
    if result.status != "invalid_id":
        worked = result.status in ("new_version", "unchanged")
        record_event(conn, nct_id, "registry", "ok" if worked else "failed", error_code=None if worked else (result.detail or result.status),
                     detail={"result": result.status, "attempts": result.attempts}, started=started, started_id=started_id)
    return result


def _ingest_trial(
    conn: psycopg.Connection,
    http: httpx.Client,
    settings: Settings,
    nct_id: str,
    sleep: Callable[[float], None] = time.sleep,
) -> IngestResult:
    """Fetch, save, parse and store one trial. The caller commits."""
    if not re.fullmatch(NCT_ID_PATTERN, nct_id):
        return IngestResult(nct_id=nct_id, status="invalid_id")

    # Step 2: fetch
    fetch, attempts = fetch_with_retries(http, settings, nct_id, sleep)
    if fetch.status == "not_found":
        save_failure(conn, nct_id, "fetch", "not_found", attempts)
        return IngestResult(nct_id=nct_id, status="not_found", attempts=attempts)
    if fetch.status != "ok" or fetch.body is None:
        code = fetch.error_code or "unknown"
        save_failure(conn, nct_id, "fetch", code, attempts)
        return IngestResult(nct_id=nct_id, status="failed", attempts=attempts, detail=code)

    # Step 3: keep the untouched file, even if parsing fails later
    stored = save_raw(nct_id, fetch.url, fetch.retrieved_at, fetch.body, settings.data_dir, bucket=settings.raw_bucket)
    if stored.backup_status in ("ok", "failed"):
        record_event(conn, nct_id, "raw_backup", "ok" if stored.backup_status == "ok" else "warning",
                     error_code=None if stored.backup_status == "ok" else "upload_failed", detail={"source": "clinicaltrials"})

    # Step 4: the snapshot and its evidence
    try:
        snapshot = parse_study(fetch.body, stored)
        evidence = extract_evidence(fetch.body, stored)
    except (ParseError, ValidationError):
        save_failure(conn, nct_id, "parse", "ParseError", attempts)
        return IngestResult(nct_id=nct_id, status="failed", attempts=attempts, detail="ParseError")

    # Step 5: the outcomes; a problem here must not lose the snapshot
    outcomes = None
    try:
        outcomes = parse_outcomes(fetch.body, stored)
    except (ParseError, ValidationError):
        save_failure(conn, nct_id, "parse", "outcomes_ParseError", attempts)

    # Step 5b: the outcomes reported in posted results; same rule, a problem here keeps the snapshot
    results = None
    try:
        results = parse_results_outcomes(fetch.body, stored)
        evidence = evidence + extract_results_evidence(fetch.body, stored)      # so findings can cite reported fields
    except (ParseError, ValidationError):
        save_failure(conn, nct_id, "parse", "results_ParseError", attempts)

    # Step 5c: the adverse-event module (safety); same rule, a problem here keeps the snapshot
    safety = None
    try:
        safety = parse_safety(fetch.body, stored)
        evidence = evidence + safety_evidence(safety, stored.retrieved_at)      # so the safety finding can cite its numbers
    except (ParseError, ValidationError):
        save_failure(conn, nct_id, "parse", "safety_ParseError", attempts)

    # Step 6: store
    is_new = save_snapshot(conn, snapshot, evidence, outcomes, results, safety)
    return IngestResult(nct_id=nct_id, status="new_version" if is_new else "unchanged", attempts=attempts)

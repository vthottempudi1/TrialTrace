##############################################################################
# trialtrace/match_papers.py
#
# PURPOSE: For one stored trial, ask the LLM whether each abstract reports each registered outcome.
#          Optionally run the diagnostic code reader; keep its answer separate when enabled.
#            - the LLM (the frozen reader, trialtrace/paper_reader.py) is the primary reader;
#              code checks its quote, its position, and the paper type (compare/abstract_llm.py)
#            - the optional code-only baseline (compare/abstract_baseline.py) is a cross-check
#          Every result stays PROVISIONAL until a person reviews it. If the LLM fails, the baseline
#          answer is shown as a provisional fallback, never as a verified result.
#          "Not identified in the abstract" does not mean the outcome is missing from the full paper.
#
# USE:     summary = match_trial(conn, settings, "NCT04644237", run_id)     # the caller commits
##############################################################################

import time
from collections import Counter
from typing import Callable

import psycopg
from openai import OpenAI
from pydantic import BaseModel, Field

from trialtrace.compare.abstract_baseline import AbstractMatch, TextQuote, match_one
from trialtrace.compare.abstract_llm import LLMMatch, match_with_llm
from trialtrace.compare.paper_agreement import EVIDENCE_ERRORS, compare_predictions  # noqa: F401 (re-exported)
from trialtrace.compare.paper_outcomes import evidence_for_paper
from trialtrace.config import Settings
from trialtrace.pipeline_events import record_event, record_started
from trialtrace.contracts import RegisteredOutcome
from trialtrace.db.repository import (
    find_reusable_match, get_latest_snapshot, get_outcomes, get_papers_for_trial, save_evidence, save_paper_matches,
    save_skipped_papers,
)
from trialtrace.paper_reader import llm_input_hash, reader_info, reader_settings
from trialtrace.parse.pubmed_parser import Paper

class PairResult(BaseModel):
    """The model answer and optional baseline for one outcome/paper pair."""

    outcome: RegisteredOutcome
    pmid: str
    paper_raw_hash: str
    baseline: AbstractMatch | None
    llm: LLMMatch
    agreement: str      # agree_identified, agree_not_identified, llm_only, baseline_only, llm_unclear, llm_failed, ...
    risk: str           # review priority: high, medium or low
    why: str            # one plain sentence for the reviewer
    reused: dict | None = None      # the stored LLM row this answer was copied from (None = a real model call was made)
    input_hash: str | None = None


class TrialMatchSummary(BaseModel):
    nct_id: str
    status: str         # done, no_snapshot, no_outcomes, no_papers
    papers_used: int = 0
    papers_skipped: int = 0     # listed papers beyond the per-trial cap: NOT read
    skipped_pmids: list[str] = Field(default_factory=list)
    llm_calls: int = 0          # real model calls made in this run
    llm_reused: int = 0         # answers copied from an earlier run instead of calling the model
    llm_failed: int = 0         # model answers that could not be used (the pair is kept with its error code)
    pairs: int = 0
    baseline_enabled: bool = False
    agreements: dict[str, int] = Field(default_factory=dict)
    risks: dict[str, int] = Field(default_factory=dict)


def llm_match_from_row(row: dict) -> LLMMatch:
    """Rebuild an earlier LLM answer from its stored row. No model call is made."""
    quote = None
    if row["quote_text"] is not None:
        quote = TextQuote(text=row["quote_text"], start=row["quote_start"], end=row["quote_end"])
    return LLMMatch(
        status=row["status"], reported_role=row["reported_role"], matched_name=row["matched_name"],
        quote=quote, error_code=row["error_code"], reason=row["reason"], llm=None,
    )


def match_pair(
    outcome: RegisteredOutcome,
    paper: Paper,
    raw_hash: str,
    reader: Settings,
    client: OpenAI | None = None,
    sleep: Callable[[float], None] = time.sleep,
    reusable: dict | None = None,
    input_hash: str | None = None,
    include_baseline: bool = True,
) -> PairResult:
    """Read one outcome/paper pair, optionally adding the diagnostic code reader. A stored LLM answer (reusable) replaces the model call."""
    baseline = match_one(outcome, paper) if include_baseline else None
    if reusable is not None:
        llm = llm_match_from_row(reusable)
    else:
        llm = match_with_llm(outcome, paper, reader, client=client, sleep=sleep)
    agreement, risk, why = compare_predictions(baseline.status if baseline else None, llm.status, llm.error_code)
    return PairResult(
        outcome=outcome, pmid=paper.pmid, paper_raw_hash=raw_hash, baseline=baseline, llm=llm,
        agreement=agreement, risk=risk, why=why, reused=reusable, input_hash=input_hash,
    )


def rows_for_pair(run_id: str, registry_hash: str, pair: PairResult, info: dict) -> list[dict]:
    """The model row and, when enabled, a separate baseline row."""
    common = {
        "match_run_id": run_id, "nct_id": pair.outcome.nct_id, "registry_hash": registry_hash,
        "outcome_position": pair.outcome.position, "outcome_role": pair.outcome.role,
        "outcome_text": pair.outcome.measure_text, "pmid": pair.pmid, "paper_raw_hash": pair.paper_raw_hash,
    }

    baseline_row = None
    if pair.baseline is not None:
        base = pair.baseline
        baseline_row = {
            **common, "system": "baseline", "status": base.status,
            "reported_role": base.reported_role, "matched_name": base.matched_name,
        }
        if base.quote is not None:
            baseline_row.update(quote_text=base.quote.text, quote_start=base.quote.start, quote_end=base.quote.end)

    llm = pair.llm
    llm_row = {
        **common, "system": "llm", "model": info["model"], "prompt_hash": info["prompt_hash"],
        "schema_version": info["schema_version"], "status": llm.status, "reported_role": llm.reported_role,
        "matched_name": llm.matched_name, "error_code": llm.error_code, "reason": llm.reason,
        "input_hash": pair.input_hash,
    }
    if pair.reused is not None:
        # copied from an earlier real call: keep the raw answer, point to the source row, count no new tokens
        llm_row.update(raw_response=pair.reused["raw_response"], reused_from=pair.reused["reused_from"] or pair.reused["match_id"],
                       served_model=pair.reused["served_model"])
    if llm.quote is not None:
        llm_row.update(quote_text=llm.quote.text, quote_start=llm.quote.start, quote_end=llm.quote.end)
    if llm.llm is not None:
        llm_row.update(
            raw_response=llm.llm.text, input_tokens=llm.llm.input_tokens,
            output_tokens=llm.llm.output_tokens, seconds=llm.llm.seconds, served_model=llm.llm.model,
        )
    return [row for row in (baseline_row, llm_row) if row is not None]


def select_papers(papers: list[dict], max_papers: int) -> tuple[list[dict], list[dict]]:
    """Production selection: input newest first, listed abstracts only, unique PMID, lowest IDs first."""
    newest_by_pmid = {}
    for paper in papers:
        if paper["status"] == "listed" and paper["abstract"] and paper["pmid"] not in newest_by_pmid:
            newest_by_pmid[paper["pmid"]] = paper
    ordered = sorted(newest_by_pmid.values(), key=lambda paper: int(paper["pmid"]))
    return ordered[:max_papers], ordered[max_papers:]


def choose_papers(conn: psycopg.Connection, nct_id: str, max_papers: int) -> tuple[list[dict], list[dict]]:
    """Load stored versions and apply the shared production paper selector."""
    return select_papers(get_papers_for_trial(conn, nct_id), max_papers)


def match_trial(
    conn: psycopg.Connection,
    settings: Settings,
    nct_id: str,
    run_id: str,
    client: OpenAI | None = None,
    sleep: Callable[[float], None] = time.sleep,
    refresh: bool = False,
    served_model: str | None = None,
    include_baseline: bool = True,
) -> TrialMatchSummary:
    """Match one trial (see _match_trial) and record a pipeline event for the run. The caller commits."""
    started = time.monotonic()
    started_id = record_started(conn, nct_id, "matching", run_id)
    try:
        summary = _match_trial(conn, settings, nct_id, run_id, client, sleep, refresh, served_model, include_baseline)
    except Exception as error:                                    # an unexpected crash is recorded, then raised again
        record_event(conn, nct_id, "matching", "failed", error_code=type(error).__name__, analysis_id=run_id, started=started, started_id=started_id,
                     durable=True)         # the caller will roll back, so a normal write would vanish
        raise
    if summary.status == "done":
        status = "ok" if summary.llm_failed == 0 else ("failed" if summary.llm_failed >= summary.pairs else "warning")
        error = None if status == "ok" else "model_answers_failed"
    else:
        status = "no_eligible" if summary.status == "no_papers" else "failed"
        error = None if status == "no_eligible" else summary.status
    record_event(conn, nct_id, "matching", status, error_code=error, analysis_id=run_id, started=started, started_id=started_id,
                 detail={"result": summary.status, "pairs": summary.pairs, "calls": summary.llm_calls, "reused": summary.llm_reused,
                         "failed": summary.llm_failed, "papers_used": summary.papers_used, "papers_skipped": summary.papers_skipped})
    return summary


def _match_trial(
    conn: psycopg.Connection,
    settings: Settings,
    nct_id: str,
    run_id: str,
    client: OpenAI | None = None,
    sleep: Callable[[float], None] = time.sleep,
    refresh: bool = False,
    served_model: str | None = None,
    include_baseline: bool = True,
) -> TrialMatchSummary:
    """Match every registered outcome of one trial against its chosen papers, and store both predictions. The caller commits.

    An LLM answer already stored for exactly the same question is reused, not paid for again (refresh=True asks again).
    served_model is the model name the API reports now: only answers made by that served model are reused.
    """
    snapshot = get_latest_snapshot(conn, nct_id)
    if snapshot is None:
        return TrialMatchSummary(nct_id=nct_id, status="no_snapshot")
    extraction = get_outcomes(conn, nct_id, snapshot.content_hash)
    if extraction is None or not extraction.outcomes:
        return TrialMatchSummary(nct_id=nct_id, status="no_outcomes")
    papers, skipped = choose_papers(conn, nct_id, settings.paper_reader_max_papers)
    if not papers:
        return TrialMatchSummary(nct_id=nct_id, status="no_papers")

    reader = reader_settings(settings)
    info = reader_info(reader)
    rows = []
    agreements = Counter()
    risks = Counter()
    calls = 0
    reused = 0
    for stored in papers:
        paper = Paper(
            pmid=stored["pmid"], title=stored["title"], abstract=stored["abstract"],
            journal=stored["journal"], year=stored["year"], listed_nct_ids=stored["listed_nct_ids"],
        )
        for outcome in extraction.outcomes:
            input_hash = llm_input_hash(outcome.measure_text, outcome.time_frame, stored["abstract"], info, outcome.nct_id)
            reusable = None
            if not refresh:
                reusable = find_reusable_match(conn, input_hash, served_model)
            pair = match_pair(outcome, paper, stored["raw_hash"], reader, client, sleep, reusable, input_hash, include_baseline=include_baseline)
            if reusable is None:
                calls += 1
            else:
                reused += 1
            rows += rows_for_pair(run_id, snapshot.content_hash, pair, info)
            agreements[pair.agreement] += 1
            risks[pair.risk] += 1
    save_paper_matches(conn, rows)
    save_skipped_papers(conn, run_id, nct_id, skipped)            # what was NOT read is recorded too
    for stored in papers:                                   # the abstract text the answers rest on, saved as evidence
        paper_rows = [row for row in rows if row["pmid"] == stored["pmid"]]
        save_evidence(conn, evidence_for_paper(nct_id, stored, paper_rows))
    return TrialMatchSummary(
        nct_id=nct_id, status="done", papers_used=len(papers), papers_skipped=len(skipped),
        skipped_pmids=[paper["pmid"] for paper in skipped],
        pairs=sum(row["system"] == "llm" for row in rows), baseline_enabled=include_baseline, agreements=dict(agreements), risks=dict(risks), llm_calls=calls, llm_reused=reused,
        llm_failed=sum(row["system"] == "llm" and row["status"] == "failed" for row in rows),
    )


class CallEstimate(BaseModel):
    """What match_trial would do, worked out without any model call."""
    status: str                    # "ok", or the reason there is nothing to ask (no_snapshot, no_outcomes, no_papers)
    pairs: int = 0                 # outcomes x papers that would be read
    reused: int = 0                # of those, answers already stored for exactly the same question
    new_calls: int = 0             # pairs - reused: the paid calls
    papers_used: int = 0
    papers_skipped: int = 0


def estimate_match_calls(conn: psycopg.Connection, settings: Settings, nct_id: str, refresh: bool = False,
                         served_model: str | None = None) -> CallEstimate:
    """Count the paid model calls match_trial would make now: the same pairs, the same reuse check, no call, nothing stored."""
    snapshot = get_latest_snapshot(conn, nct_id)
    if snapshot is None:
        return CallEstimate(status="no_snapshot")
    extraction = get_outcomes(conn, nct_id, snapshot.content_hash)
    if extraction is None or not extraction.outcomes:
        return CallEstimate(status="no_outcomes")
    papers, skipped = choose_papers(conn, nct_id, settings.paper_reader_max_papers)
    if not papers:
        return CallEstimate(status="no_papers", papers_skipped=len(skipped))

    info = reader_info(reader_settings(settings))
    pairs = 0
    reused = 0
    for stored in papers:
        for outcome in extraction.outcomes:
            pairs += 1
            input_hash = llm_input_hash(outcome.measure_text, outcome.time_frame, stored["abstract"], info, outcome.nct_id)
            if not refresh and find_reusable_match(conn, input_hash, served_model) is not None:
                reused += 1
    return CallEstimate(status="ok", pairs=pairs, reused=reused, new_calls=pairs - reused,
                        papers_used=len(papers), papers_skipped=len(skipped))

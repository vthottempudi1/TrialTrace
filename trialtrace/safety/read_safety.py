##############################################################################
# trialtrace/safety/read_safety.py
#
# PURPOSE: Safety, step S6: for one stored trial, read the safety statements in each linked paper's
#          abstract with the model and optionally the diagnostic code reader:
#            - the model (compare/safety_llm.py): one call per abstract, every statement checked by code
#            - the code-only baseline (compare/safety_baseline.py), as a cross-check
#          Nothing is compared with the registry here (that is step S7). Everything is provisional.
#          Last, the evidence the safety findings will cite is saved (abstract quotes, registry values).
#          A model answer already stored for exactly the same abstract and settings is NOT paid for
#          again: its raw answer is checked again by today's code (no model call).
#          Only trials with adverse-event data in the registry are read: without it there is nothing
#          to compare with later.
#
# USE:     summary = read_safety_for_trial(conn, settings, "NCT04644237", run_id)     # the caller commits
##############################################################################

import time
from typing import Callable

import psycopg
from openai import OpenAI
from pydantic import BaseModel, Field

from trialtrace.compare.paper_outcomes import evidence_for_paper
from trialtrace.safety.safety_baseline import SafetyReading, read_safety_statements
from trialtrace.safety.safety_llm import (
    ModelSafetyStatement, read_safety_with_llm, reading_from_answer, safety_input_hash, safety_reader_info,
)
from trialtrace.safety.safety_rollup import load_trial_safety, registry_evidence
from trialtrace.config import Settings
from trialtrace.db.repository import (
    find_reusable_safety_reading, get_latest_snapshot, get_papers_for_trial, get_safety, get_safety_readings,
    save_evidence, save_safety_reading,
)
from trialtrace.match_papers import choose_papers
from trialtrace.paper_reader import reader_settings
from trialtrace.parse.pubmed_parser import Paper


class SafetyReadSummary(BaseModel):
    nct_id: str
    status: str                 # done, no_snapshot, no_safety_data, no_papers
    papers_read: int = 0
    papers_skipped: int = 0     # listed papers beyond the per-trial cap: NOT read
    skipped_pmids: list[str] = Field(default_factory=list)
    llm_calls: int = 0          # real model calls made in this run
    llm_reused: int = 0         # stored answers checked again instead of calling the model
    llm_failed: int = 0         # papers with no usable model answer
    model_statements: int = 0   # passed every code check
    model_rejected: int = 0     # failed a check: stored with the reason, never used
    baseline_statements: int = 0
    baseline_enabled: bool = False


def quote_columns(quote) -> dict:
    if quote is None:
        return {}
    return {"quote_text": quote.text, "quote_start": quote.start, "quote_end": quote.end}


def model_statement_row(statement: ModelSafetyStatement) -> dict:
    """One model statement as a table row. rejected_code None = it passed every check."""
    return {
        "kind": statement.kind, "term": statement.term, "arm": statement.arm, "arm_in_quote": statement.arm_in_quote,
        "affected": statement.count, "denominator": statement.denominator, "percent": statement.percent,
        "grade": statement.grade, "treatment_related": statement.treatment_related, "cause": statement.cause,
        "all_cause": statement.all_cause, "negated": statement.negated, "sentence": statement.sentence,
        "measure": statement.measure, "attribution_quote": statement.attribution_quote,
        "population": statement.population, "population_scope_unverified": statement.population_scope_unverified,
        "event_scope": statement.event_scope, "evidence_group": statement.evidence_group,
        "detail_level": statement.detail_level, "validation_notes": statement.validation_notes,
        "rejected_code": statement.error_code, **quote_columns(statement.quote),
    }


def baseline_rows(reading: SafetyReading) -> list[dict]:
    rows = []
    for statement in reading.statements:
        rows.append({
            "kind": statement.kind, "term": statement.term, "numbers": statement.numbers, "grade": statement.grade,
            "treatment_related": statement.treatment_related, "negated": statement.negated,
            "sentence": statement.sentence, **quote_columns(statement.quote),
        })
    return rows


def save_safety_evidence(conn: psycopg.Connection, nct_id: str) -> int:
    """Save the evidence the safety findings cite, from the newest stored readings. Returns how many rows were new.

    Paper side: each abstract read and each quote. Registry side: each value a comparison used.
    Rows already stored are left alone, so running it again adds nothing. The caller commits.
    """
    rows_by_paper = {}                              # (PMID, raw hash) -> every statement row of both readers
    for reading in get_safety_readings(conn, nct_id):
        key = (reading["pmid"], reading["paper_raw_hash"])
        if key not in rows_by_paper:
            rows_by_paper[key] = []
        rows_by_paper[key] += reading["statements"]

    added = 0
    for stored in get_papers_for_trial(conn, nct_id):
        key = (stored["pmid"], stored["raw_hash"])
        if key in rows_by_paper and stored["abstract"]:
            added += save_evidence(conn, evidence_for_paper(nct_id, stored, rows_by_paper[key]))

    data = load_trial_safety(conn, nct_id)
    snapshot = get_latest_snapshot(conn, nct_id)
    if data is not None and snapshot is not None:
        added += save_evidence(conn, registry_evidence(snapshot, data))
    return added


def read_safety_for_trial(
    conn: psycopg.Connection,
    settings: Settings,
    nct_id: str,
    run_id: str,
    client: OpenAI | None = None,
    sleep: Callable[[float], None] = time.sleep,
    refresh: bool = False,
    served_model: str | None = None,
    include_baseline: bool = True,
) -> SafetyReadSummary:
    """Read one trial's chosen abstracts with the model and an optional code cross-check.

    refresh=True asks the model again even where a stored answer exists. served_model is the model name the
    API reports now: only answers made by that served model are reused. The caller commits.
    """
    snapshot = get_latest_snapshot(conn, nct_id)
    if snapshot is None:
        return SafetyReadSummary(nct_id=nct_id, status="no_snapshot")
    safety = get_safety(conn, nct_id, snapshot.content_hash)
    if safety is None or safety.status != "ok" or not safety.arms:
        return SafetyReadSummary(nct_id=nct_id, status="no_safety_data")
    papers, skipped = choose_papers(conn, nct_id, settings.paper_reader_max_papers)
    if not papers:
        return SafetyReadSummary(nct_id=nct_id, status="no_papers")

    reader = reader_settings(settings)
    info = safety_reader_info(reader)
    summary = SafetyReadSummary(
        nct_id=nct_id, status="done", papers_read=len(papers), papers_skipped=len(skipped),
        skipped_pmids=[paper["pmid"] for paper in skipped], baseline_enabled=include_baseline,
    )
    for stored in papers:
        paper = Paper(pmid=stored["pmid"], title=stored["title"], abstract=stored["abstract"])
        common = {"safety_run_id": run_id, "nct_id": nct_id, "pmid": paper.pmid, "paper_raw_hash": stored["raw_hash"]}

        if include_baseline:
            # Optional diagnostic cross-check; the model does not depend on it.
            baseline = read_safety_statements(paper)
            save_safety_reading(conn, {**common, "system": "baseline", "status": baseline.status}, baseline_rows(baseline))
            summary.baseline_statements += len(baseline.statements)

        # The model: a stored answer to exactly this question is checked again; otherwise one real call.
        input_hash = safety_input_hash(stored["abstract"], info, nct_id, registry_safety=safety, registry_title=snapshot.title)
        reusable = None
        if not refresh:
            reusable = find_reusable_safety_reading(conn, input_hash, served_model)
        row = {**common, "system": "llm", "model": info["model"], "prompt_hash": info["prompt_hash"],
               "schema_version": info["schema_version"], "input_hash": input_hash}
        if reusable is not None:
            reading = reading_from_answer(paper.pmid, stored["abstract"], reusable["raw_response"], target_nct_id=nct_id)
            row.update(raw_response=reusable["raw_response"], served_model=reusable["served_model"],
                       reused_from=reusable["reused_from"] or reusable["reading_id"])      # always the real call
            summary.llm_reused += 1
        else:
            reading = read_safety_with_llm(paper, reader, client=client, sleep=sleep, target_nct_id=nct_id)
            if reading.llm is not None:
                row.update(raw_response=reading.llm.text, input_tokens=reading.llm.input_tokens,
                           output_tokens=reading.llm.output_tokens, seconds=reading.llm.seconds,
                           served_model=reading.llm.model)
                summary.llm_calls += 1
        from trialtrace.safety.safety_identity import check_reading_identity
        reading = check_reading_identity(reading, stored["abstract"], safety, snapshot.title)
        row.update(status=reading.status, document=reading.document, error_code=reading.error_code, reason=reading.reason)
        statement_rows = [model_statement_row(statement) for statement in reading.statements + reading.rejected]
        save_safety_reading(conn, row, statement_rows)

        if reading.status == "failed":
            summary.llm_failed += 1
        summary.model_statements += len(reading.statements)
        summary.model_rejected += len(reading.rejected)

    save_safety_evidence(conn, nct_id)              # the quotes and registry values the findings (step S8) cite
    return summary

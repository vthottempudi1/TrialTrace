##############################################################################
# trialtrace/compare/paper_rollup.py
#
# PURPOSE: One finding per REGISTERED OUTCOME of a trial, summarising what the checked paper abstracts
#          say about it. (The per-paper findings in paper_outcomes.py stay as the supporting detail.)
#          Question 5b, paper arm, at the level a person reads: "is this outcome reported in the papers?"
#
# THE ROLL-UP RULE (per system, over the papers read for that outcome):
#          any paper `identified`                        -> identified (the supporting papers and quotes are named)
#          else any paper `ambiguous`                    -> unclear
#          else a failed paper, or a paper not read      -> unknown
#          else (every paper `not_identified`)           -> not identified in the checked abstracts
#          "Not read" = listed papers left out by the paper limit, stored after the match run, or whose
#          abstract changed after it. The LLM is the primary reader; the code baseline is rolled up the
#          same way and any difference is marked DISPUTED, which puts the finding first in the review queue.
#
# RULES:   - every result is provisional until a person reviews it
#          - "not identified" always means "in the available text (abstracts) of the papers read"
#          - an identified outcome's quote was verified by code; whether the sentence really reports the
#            result is for a person to confirm
#
# USE:     findings = check_paper_summary(conn, "NCT04644237")
##############################################################################

import re

import psycopg

from trialtrace.compare.paper_outcomes import (
    ABSTRACT_ONLY, PROVISIONAL, abstract_evidence_id, papers_changed_since_run, short,
)
from trialtrace.compare.abstract_baseline import find_name
from trialtrace.contracts import Finding
from trialtrace.db.repository import get_paper_matches, get_skipped_papers
from trialtrace.parse.registry_parser import outcome_path

# review priority of the summary categories (used by the review queue)
SUMMARY_HIGH_RISK = {"paper_summary_unclear", "paper_summary_reported_disputed", "paper_summary_not_identified_disputed",
                     "paper_summary_reported_papers_differ", "paper_summary_reported_no_detail"}
SUMMARY_LOW_RISK = {"paper_summary_reported", "paper_summary_not_identified"}


def rollup_statuses(statuses: list[str], unread: bool) -> str:
    """One answer from the per-paper statuses of ONE system for ONE outcome: identified, unclear, unknown or not_identified."""
    if "identified" in statuses:
        return "identified"
    if "ambiguous" in statuses:
        return "unclear"
    if unread:
        return "unknown"
    for status in statuses:
        if status in ("failed", "no_text"):
            return "unknown"
    return "not_identified"


# A time point is not a result: "at 3 or 6 months" is removed before looking for numbers
TIME_POINT = re.compile(r"\b(?:at|by|after|within|over|through|until|to)\s+\d+(?:\s*(?:or|to|and|-)\s*\d+)?\s*(?:months?|weeks?|days?|years?)\b", re.IGNORECASE)


def has_result_detail(quote: str, name: str) -> bool:
    """A number, a test, or the word 'significant' next to the outcome's name in its supporting sentence.

    Heuristic. Next to = after the name, up to the first comma or semicolon outside brackets; the numbers
    in brackets right after the name count. In "no difference in weight (0.2 kg), insulin resistance, or
    activity at 3 months" only weight has a result; the names after the comma have none.
    """
    start = find_name(name, quote)
    if start is None:
        return False
    window = ""
    depth = 0
    for character in quote[start + len(name):]:
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        elif character in ",;" and depth == 0:
            break
        window += character
    window = TIME_POINT.sub("", window)
    return bool(re.search(r"\d", window) or re.search(r"signific", window, re.IGNORECASE))


def category_for(llm_answer: str, baseline_answer: str | None, share_a_paper: bool = True) -> str:
    """The finding category. A difference between the two systems' roll-ups is marked 'disputed'.

    Both systems can say 'identified' while citing different papers; with no paper in common that is marked 'papers_differ'.
    """
    if llm_answer == "unknown":
        return "unknown"
    if llm_answer == "unclear":
        return "paper_summary_unclear"
    if baseline_answer is None:
        return "paper_summary_reported" if llm_answer == "identified" else "paper_summary_not_identified"
    if llm_answer == "identified":
        if baseline_answer == "identified":
            if not share_a_paper:
                return "paper_summary_reported_papers_differ"
            return "paper_summary_reported"
        return "paper_summary_reported_disputed"
    if baseline_answer == "not_identified":
        return "paper_summary_not_identified"
    return "paper_summary_not_identified_disputed"


def papers_text(pmids: list[str]) -> str:
    return ", ".join(pmids)


def summary_finding(nct_id: str, outcome_rows: list[dict], unread: dict[str, list[str]], source_run_id: str) -> Finding:
    """The finding for one registered outcome. outcome_rows are its paper_matches rows (both systems, every paper read)."""
    llm_rows = [row for row in outcome_rows if row["system"] == "llm"]
    baseline_rows = [row for row in outcome_rows if row["system"] == "baseline"]
    first = llm_rows[0]
    role, position, outcome_text = first["outcome_role"], first["outcome_position"], first["outcome_text"]
    any_unread = bool(unread["skipped"] or unread["new"] or unread["changed"])

    llm_answer = rollup_statuses([row["status"] for row in llm_rows], any_unread)
    baseline_answer = rollup_statuses([row["status"] for row in baseline_rows], any_unread) if baseline_rows else None
    baseline_identified = sorted({row["pmid"] for row in baseline_rows if row["status"] == "identified"}, key=int)
    llm_identified = sorted({row["pmid"] for row in llm_rows if row["status"] == "identified"}, key=int)
    share_a_paper = bool(set(llm_identified) & set(baseline_identified))
    category = category_for(llm_answer, baseline_answer, share_a_paper)
    quotes_with_detail = [row for row in llm_rows if row["status"] == "identified" and has_result_detail(row["quote_text"] or "", row["matched_name"] or "")]
    if category == "paper_summary_reported" and not quotes_with_detail:
        category = "paper_summary_reported_no_detail"          # agreed, but no sentence shows a number or test for the outcome
    read_pmids = sorted({row["pmid"] for row in llm_rows}, key=int)
    outcome = f"the registered outcome '{short(outcome_text)}'"
    check = f"paper_outcome_summary:{role}:{position}"

    identified = sorted({row["pmid"] for row in llm_rows if row["status"] == "identified"}, key=int)
    unclear = sorted({row["pmid"] for row in llm_rows if row["status"] == "ambiguous"}, key=int)
    failed = sorted({row["pmid"] for row in llm_rows if row["status"] in ("failed", "no_text")}, key=int)
    completeness = "incomplete" if (any_unread or failed) else "complete"      # separate from the answer: how much was read

    limitations = [ABSTRACT_ONLY, PROVISIONAL]
    per_paper = "; ".join(f"{row['pmid']}: {row['status']}" for row in sorted(llm_rows, key=lambda r: int(r["pmid"])))
    limitations.append(f"The model's answer per paper read: {per_paper}.")
    if baseline_rows and baseline_answer != llm_answer:
        limitations.append(f"The code baseline's roll-up for this outcome is '{baseline_answer}', the model's is '{llm_answer}': for a person to check.")
    if category == "paper_summary_reported_no_detail":
        limitations.append("No supporting sentence gives a number or a test next to the outcome's name, so it may only name the outcome: for a person to check.")
    if category == "paper_summary_reported_papers_differ":
        limitations.append(f"Both systems identified this outcome, but in different papers: the model in {papers_text(llm_identified)}, the code baseline in {papers_text(baseline_identified)}: for a person to check.")
    if completeness == "incomplete" and llm_answer == "identified":
        limitations.append("Not every listed paper was read or usable, so more papers could report this outcome.")
    if failed:
        errors = ", ".join(f"{row['pmid']} ({row['error_code'] or row['status']})" for row in llm_rows if row["status"] in ("failed", "no_text"))
        limitations.append(f"The model's answer could not be used for paper(s): {errors}.")
    if unread["skipped"]:
        limitations.append(f"{len(unread['skipped'])} other listed paper(s) were not read because of the per-trial paper limit (PMID {papers_text(unread['skipped'])}).")
    if unread["new"]:
        limitations.append(f"Listed paper(s) stored after this match run were not read (PMID {papers_text(unread['new'])}): run match-papers again.")
    if unread["changed"]:
        limitations.append(f"The abstract text of paper(s) {papers_text(unread['changed'])} changed after this match run: run match-papers again.")
    if first.get("model"):
        limitations.append(f"Read by {first['model']} (prompt {first['prompt_hash']}); code checked every quote, its position and the paper type.")

    base = {"nct_id": nct_id, "check": check, "comparison_arm": "abstract", "match_method": "semantic", "source_run_id": source_run_id,
            "evidence_completeness": completeness}

    if category == "unknown":
        reasons = []
        if failed:
            reasons.append(f"the model's answer could not be used for paper(s) {papers_text(failed)}")
        if any_unread:
            reasons.append("some listed papers were not read")
        return Finding(
            **base, category="unknown",
            claim=f"Whether {outcome} is reported in the checked paper abstracts is unknown: {' and '.join(reasons)}.",
            limitations=limitations,
        )

    measure_id = f"{nct_id}:{first['registry_hash'][:12]}:{outcome_path(role, position, 'measure')}"
    evidence = [measure_id]
    if category.startswith("paper_summary_reported"):
        for row in llm_rows:
            if row["status"] == "identified":
                evidence.append(abstract_evidence_id(nct_id, row["paper_raw_hash"], row["pmid"], row["quote_start"], row["quote_end"]))
        limitations.append("The quote was verified by code to be in the abstract; whether the sentence really reports the result is for a person to confirm.")
        claim = (f"The model identified evidence that may report a result for {outcome} in the abstract of paper(s) {papers_text(identified)} "
                 f"(read {len(read_pmids)} paper(s): {papers_text(read_pmids)}).")
    elif category == "paper_summary_unclear":
        for row in llm_rows:
            if row["status"] == "ambiguous":
                evidence.append(abstract_evidence_id(nct_id, row["paper_raw_hash"], row["pmid"]))
        claim = (f"The model could not match {outcome} exactly to a result in the abstract of paper(s) {papers_text(unclear)}; "
                 f"the scope, time frame or measure may differ (read {len(read_pmids)} paper(s)).")
    else:
        for row in llm_rows:
            evidence.append(abstract_evidence_id(nct_id, row["paper_raw_hash"], row["pmid"]))
        claim = (f"A result for {outcome} was not identified in the available text (abstracts) of the {len(read_pmids)} paper(s) read "
                 f"({papers_text(read_pmids)}) by the model.")
    return Finding(**base, category=category, claim=claim, evidence_ids=list(dict.fromkeys(evidence)), limitations=limitations)


def check_paper_summary(conn: psycopg.Connection, nct_id: str) -> list[Finding]:
    """One finding per registered outcome from the newest match run, highest review risk first.

    Returns an empty list when the trial has no stored match run: check_paper_outcomes already
    reports that as one 'unknown' finding.
    """
    rows = get_paper_matches(conn, nct_id)
    if not rows:
        return []
    run_id = rows[0]["match_run_id"]
    skipped = [paper["pmid"] for paper in get_skipped_papers(conn, nct_id, run_id)]
    new_pmids, changed_pmids = papers_changed_since_run(conn, nct_id, rows, skipped)
    unread = {"skipped": skipped, "new": new_pmids, "changed": changed_pmids}

    by_outcome: dict[tuple, list[dict]] = {}
    for row in rows:
        by_outcome.setdefault((row["outcome_role"], row["outcome_position"]), []).append(row)

    built = []
    for key, outcome_rows in by_outcome.items():
        finding = summary_finding(nct_id, outcome_rows, unread, run_id)
        if finding.category in SUMMARY_HIGH_RISK:
            rank = 0
        elif finding.category in SUMMARY_LOW_RISK:
            rank = 2
            if finding.evidence_completeness == "incomplete":
                rank = 1                                      # agreed, but not every paper was read
        else:
            rank = 1                                          # unknown
        built.append((rank, key, finding))
    built.sort(key=lambda item: (item[0], item[1][0], item[1][1]))
    return [finding for _, _, finding in built]

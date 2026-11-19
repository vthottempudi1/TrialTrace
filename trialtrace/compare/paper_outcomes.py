##############################################################################
# trialtrace/compare/paper_outcomes.py
#
# PURPOSE: Turn the stored paper-match predictions (table paper_matches, written by match_papers.py)
#          into Findings, so the report and the review queue can show them. One finding per
#          registered outcome per paper. Question 5b, paper (abstract) arm.
#
# RULES:   - every result is PROVISIONAL until a person reviews it (each finding says so)
#          - "not identified" is worded "not identified in the available text (abstract)":
#            it never says the outcome is missing from the full paper
#          - differences between the two systems, unclear answers and failures are high review
#            priority; agreement is low priority
#          - a trial with no stored match run gets ONE "unknown" finding, never a silent "no"
#          - each finding cites the registered outcome and the abstract text the systems used
#
# USE:     findings = check_paper_outcomes(conn, "NCT04644237")
##############################################################################

import psycopg

from trialtrace.contracts import Evidence, Finding
from trialtrace.db.repository import get_latest_snapshot, get_paper_matches, get_papers_for_trial, get_skipped_papers
from trialtrace.compare.paper_agreement import compare_predictions
from trialtrace.parse.registry_parser import outcome_path

ABSTRACT_ONLY = ("Abstract only: 'not identified' means the result was not found in the available text (abstract); "
                 "it does not mean the full paper omits the outcome.")
PROVISIONAL = "Provisional: not yet reviewed by a person."

# how the two systems' agreement becomes a finding category
CATEGORY = {
    "llm_identified": "paper_reports_outcome",
    "llm_not_identified": "paper_outcome_not_identified",
    "agree_identified": "paper_reports_outcome",
    "agree_not_identified": "paper_outcome_not_identified",
    "llm_only": "paper_systems_disagree",
    "baseline_only": "paper_systems_disagree",
    "different": "paper_systems_disagree",
    "llm_unclear": "paper_outcome_unclear",
    "llm_failed": "paper_check_failed",
    "llm_evidence_invalid": "paper_check_failed",
    "no_text": "unknown",
}

# categories that need no decision unless the reviewer asks for everything
LOW_RISK_CATEGORIES = {"paper_reports_outcome", "paper_outcome_not_identified"}

RISK_ORDER = {"high": 0, "medium": 1, "low": 2}


def abstract_evidence_id(nct_id: str, raw_hash: str, pmid: str, start: int | None = None, end: int | None = None) -> str:
    """The evidence ID of a paper's whole abstract, or of a quoted span of it.

    The PMID is part of the ID: one downloaded file (one raw hash) holds every paper of a trial.
    """
    base = f"{nct_id}:{raw_hash[:12]}:pmid{pmid}:abstract"
    if start is None:
        return base
    return f"{base}[{start}:{end}]"


def evidence_for_paper(nct_id: str, stored: dict, rows: list[dict]) -> list[Evidence]:
    """Evidence rows for one paper: its whole abstract, and each distinct quote the systems used.

    `stored` is the paper as returned by get_papers_for_trial; `rows` are the match rows for that paper.
    """
    common = {
        "nct_id": nct_id, "content_hash": stored["raw_hash"], "source": "pubmed",
        "source_url": stored["source_url"], "retrieved_at": stored["retrieved_at"],
    }
    pmid = stored["pmid"]
    items = [Evidence(evidence_id=abstract_evidence_id(nct_id, stored["raw_hash"], pmid), field_path=f"pmid{pmid}.abstract",
                      value=stored["abstract"], **common)]
    seen = set()
    for row in rows:
        if row.get("quote_text") is None:
            continue
        key = (row["quote_start"], row["quote_end"])
        if key in seen:
            continue
        seen.add(key)
        items.append(Evidence(
            evidence_id=abstract_evidence_id(nct_id, stored["raw_hash"], pmid, *key),
            field_path=f"pmid{pmid}.abstract[{key[0]}:{key[1]}]", value=row["quote_text"], **common,
        ))
    return items


def short(text: str, limit: int = 90) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def claim_for(category: str, agreement: str, outcome_text: str, pmid: str, baseline: dict | None, llm: dict) -> str:
    """One plain sentence. It describes; it never judges."""
    where = f"the abstract of paper {pmid}"
    outcome = f"the registered outcome '{short(outcome_text)}'"
    if baseline is None:
        if category == "paper_reports_outcome":
            return f"The model identified evidence that may report a result for {outcome} in {where}."
        if category == "paper_outcome_not_identified":
            return f"The model did not identify a result for {outcome} in {where}; this does not establish absence from the full paper."
        if category == "paper_check_failed":
            return f"The model's answer about {outcome} in {where} could not be used ({llm['error_code']}); review is required."
    if category == "paper_reports_outcome":
        return f"Both the model and the code baseline found a result for {outcome} in {where}."
    if category == "paper_outcome_not_identified":
        return f"A result for {outcome} was not identified in the available text (abstract) of paper {pmid} by the model or the baseline."
    if category == "paper_outcome_unclear":
        return f"The model could not match {outcome} exactly to a result in {where}; the scope, time frame or measure may differ."
    if category == "paper_check_failed":
        return (f"The model's answer about {outcome} in paper {pmid} could not be used ({llm['error_code']}); "
                f"the baseline answer is '{baseline['status']}' and is only provisional.")
    if agreement == "llm_only":
        return f"The model found a result for {outcome} in {where}; the code baseline did not."
    if agreement == "baseline_only":
        return f"The code baseline found a result for {outcome} in {where}; the model did not."
    return f"The model and the code baseline gave different answers about {outcome} in {where}."


def papers_changed_since_run(conn: psycopg.Connection, nct_id: str, rows: list[dict], skipped_pmids: list[str]) -> tuple[list[str], list[str]]:
    """(new PMIDs, changed PMIDs): stored papers that the newest match run did not see, or saw with different abstract text.

    New = a listed paper with an abstract that the run neither read nor recorded as skipped.
    Changed = a paper the run read whose abstract text is different in a newer stored download.
    """
    read_hash = {row["pmid"]: row["paper_raw_hash"] for row in rows}
    versions: dict[str, list[dict]] = {}
    for paper in get_papers_for_trial(conn, nct_id):                  # newest download first
        versions.setdefault(paper["pmid"], []).append(paper)

    new_pmids = []
    changed_pmids = []
    for pmid, papers in versions.items():
        newest = papers[0]
        if newest["status"] != "listed" or not newest["abstract"]:
            continue
        if pmid not in read_hash:
            if pmid not in skipped_pmids:
                new_pmids.append(pmid)
            continue
        if newest["raw_hash"] != read_hash[pmid]:
            old = [paper for paper in papers if paper["raw_hash"] == read_hash[pmid]]
            if old and old[0]["abstract"] != newest["abstract"]:
                changed_pmids.append(pmid)
    return sorted(new_pmids, key=int), sorted(changed_pmids, key=int)


def finding_for_pair(nct_id: str, baseline: dict | None, llm: dict, latest_registry_hash: str | None,
                     skipped_pmids: list[str] | None = None, new_pmids: list[str] | None = None,
                     changed_pmids: list[str] | None = None) -> tuple[Finding, str]:
    """One Finding for one outcome in one paper, and its review risk (high, medium, low)."""
    agreement, risk, why = compare_predictions(baseline["status"] if baseline else None, llm["status"], llm["error_code"])
    category = CATEGORY[agreement]
    role, position, pmid, raw_hash = llm["outcome_role"], llm["outcome_position"], llm["pmid"], llm["paper_raw_hash"]
    check = f"paper_outcome:{role}:{position}:{pmid}"

    if category == "unknown":
        return Finding(
            nct_id=nct_id, check=check, category="unknown", comparison_arm="abstract", match_method="semantic",
            source_run_id=llm["match_run_id"],
            claim=f"Whether the registered outcome '{short(llm['outcome_text'])}' is reported in paper {pmid} is unknown.",
            limitations=[why, ABSTRACT_ONLY],
        ), risk

    # evidence: the registered outcome, plus the abstract text each system relied on (or the whole abstract)
    evidence = [f"{nct_id}:{llm['registry_hash'][:12]}:{outcome_path(role, position, 'measure')}"]
    quoted = False
    for row in (llm, baseline):
        if row is not None and row.get("quote_text") is not None:
            evidence.append(abstract_evidence_id(nct_id, raw_hash, pmid, row["quote_start"], row["quote_end"]))
            quoted = True
    if not quoted:
        evidence.append(abstract_evidence_id(nct_id, raw_hash, pmid))

    limitations = [ABSTRACT_ONLY, PROVISIONAL, why]
    if llm.get("model"):
        limitations.append(f"Read by {llm['model']} (prompt {llm['prompt_hash']}); code checked the quote, its position and the paper type.")
    if llm.get("reason"):
        limitations.append(f"Note: {llm['reason']}.")
    if skipped_pmids:
        limitations.append(f"{len(skipped_pmids)} other listed paper(s) were not read because of the per-trial paper limit "
                           f"(PMID {', '.join(skipped_pmids)}); this finding does not cover them.")
    if new_pmids:
        limitations.append(f"Listed paper(s) stored after this match run were not read (PMID {', '.join(new_pmids)}): "
                           "run match-papers again to include them.")
    if changed_pmids:
        limitations.append(f"The abstract text of paper(s) {', '.join(changed_pmids)} changed after this match run: "
                           "run match-papers again to read the new text.")
    if latest_registry_hash is not None and latest_registry_hash != llm["registry_hash"]:
        limitations.append("The registered outcome comes from an older saved version of the record than the newest one.")

    finding = Finding(
        nct_id=nct_id, check=check, category=category, comparison_arm="abstract", match_method="semantic",
        source_run_id=llm["match_run_id"],
        claim=claim_for(category, agreement, llm["outcome_text"], pmid, baseline, llm),
        evidence_ids=list(dict.fromkeys(evidence)), limitations=limitations,
    )
    return finding, risk


def check_paper_outcomes(conn: psycopg.Connection, nct_id: str) -> list[Finding]:
    """Findings for one trial from its newest stored match run, highest review risk first.

    No stored run gives one 'unknown' finding: not checked yet is never read as 'nothing reported'.
    """
    rows = get_paper_matches(conn, nct_id)
    if not rows:
        return [Finding(
            nct_id=nct_id, check="paper_outcomes", category="unknown", comparison_arm="abstract", match_method="semantic",
            claim="Whether the registered outcomes are reported in linked paper abstracts has not been checked.",
            limitations=["No paper-match run is stored for this trial. Run: python -m trialtrace match-papers.", ABSTRACT_ONLY],
        )]

    snapshot = get_latest_snapshot(conn, nct_id)
    latest_hash = snapshot.content_hash if snapshot else None
    skipped_pmids = [paper["pmid"] for paper in get_skipped_papers(conn, nct_id, rows[0]["match_run_id"])]
    new_pmids, changed_pmids = papers_changed_since_run(conn, nct_id, rows, skipped_pmids)

    pairs: dict[tuple, dict] = {}
    for row in rows:
        key = (row["outcome_role"], row["outcome_position"], row["pmid"], row["paper_raw_hash"])
        pairs.setdefault(key, {})[row["system"]] = row

    built = []
    for key, by_system in pairs.items():
        if "llm" not in by_system:
            continue                                        # a code-only row cannot stand in for a model answer
        finding, risk = finding_for_pair(nct_id, by_system.get("baseline"), by_system["llm"], latest_hash, skipped_pmids, new_pmids, changed_pmids)
        built.append((RISK_ORDER[risk], key, finding))
    built.sort(key=lambda item: (item[0], item[1][0], item[1][1], item[1][2]))
    return [finding for _, _, finding in built]

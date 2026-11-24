##############################################################################
# trialtrace/evidence_expansion/agent/agent_ladder.py
#
# PURPOSE: The agent. For one unresolved outcome it climbs a short ladder of sources,
#          and stops as soon as it has a verified answer, runs out of rungs, or hits
#          the budget. Unlike the baseline it:
#            - gives up at once on vague outcomes (no fetches),
#            - picks the first rung from the likely cause,
#            - asks the model for NEW search words when the NCT search found nothing useful,
#            - stops early on a verified "identified".
#          It reuses the baseline's check_one_paper, so the comparison and quote check are the same.
#
# USE:     result = resolve_outcome(settings, outcome, trial_title, linked_papers, sources, budget)
##############################################################################

from trialtrace.config import Settings
from trialtrace.evidence_expansion.agent.ask_model import classify_cause, reformulate_query
from trialtrace.evidence_expansion.agent.budget import Budget
from trialtrace.evidence_expansion.baseline.expand_outcome import RANK, ExpansionResult, Tools, check_one_paper

LADDERS = {
    "text": ["linked_fulltext", "companion_nct", "companion_query"],
    "paper": ["companion_nct", "companion_query", "linked_fulltext"],
}


def keep_better(best: ExpansionResult | None, new: ExpansionResult | None) -> ExpansionResult | None:
    """The answer with the higher rank wins."""
    if new is None:
        return best
    if best is None:
        return new
    if RANK[new.status] > RANK[best.status]:
        return new
    return best


def read_papers(outcome, papers_with_source, tools: Tools, budget: Budget) -> ExpansionResult | None:
    """Compare the outcome with each paper (full text if open access, else the abstract of companions)."""
    best = None
    for item in papers_with_source:
        paper = item["paper"]
        if budget.is_used_up():
            return best
        budget.add_call("fetch")
        text = tools.get_text(paper.pmid)
        if text is not None:
            source_name = "linked_fulltext" if item["found_by"] == "linked" else "companion_fulltext"
            source_text = text
        elif item["found_by"] != "linked" and paper.abstract:
            source_name = "companion_abstract"
            source_text = paper.abstract
        else:
            continue
        if budget.is_used_up():
            return best
        budget.add_call("llm")
        result = check_one_paper(outcome, paper, source_text, source_name, item["found_by"], tools)
        best = keep_better(best, result)
        if best is not None and best.status == "identified":
            return best                                  # verified evidence found: stop early
    return best


def resolve_outcome(settings: Settings, outcome, trial_title, linked_papers, sources, budget: Budget) -> ExpansionResult:
    """Climb the ladder. `sources` has get_text(pmid), companions(nct) and search(query)."""
    tools = Tools(get_text=sources.get_text, find_companions=lambda: [], match=sources.match, count=lambda kind: None)
    linked_titles = [paper.title or "" for paper in linked_papers]

    # 1. Plan: why is it unresolved?
    cause = classify_cause(settings, outcome, trial_title, linked_titles)
    if cause == "vague":
        return ExpansionResult(status="unresolved", note="abstained: registry outcome too vague to match")

    # 2. Climb the ladder.
    best = None
    for rung in LADDERS[cause]:
        if budget.is_used_up():
            break
        if rung == "linked_fulltext":
            items = [{"paper": paper, "found_by": "linked"} for paper in linked_papers]
        elif rung == "companion_nct":
            if budget.already_tried(f"nct:{outcome.nct_id}"):
                continue
            budget.add_call("fetch")
            items = sources.companions(outcome.nct_id, trial_title, [paper.pmid for paper in linked_papers],
                                       only="nct_search")
        else:   # companion_query: new words from the model, never a repeat
            budget.add_call("llm")
            query = reformulate_query(settings, outcome, trial_title, linked_titles, sorted(budget.tried_queries))
            if not query or budget.already_tried(query):
                continue
            budget.add_call("fetch")
            items = sources.search(query, [paper.pmid for paper in linked_papers])
        best = keep_better(best, read_papers(outcome, items, tools, budget))
        if best is not None and best.status == "identified":
            break

    # 3. Decide: upgrade, or keep the old status and say why.
    if best is None:
        if budget.is_used_up():
            return ExpansionResult(status="unresolved", note="unresolved, budget reached")
        return ExpansionResult(status="unresolved", note="no verified evidence found")
    return best

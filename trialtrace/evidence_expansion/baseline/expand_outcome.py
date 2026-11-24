##############################################################################
# trialtrace/evidence_expansion/baseline/expand_outcome.py
#
# PURPOSE: The NON-agent baseline. For one unresolved outcome, ALWAYS do the same steps:
#            1. read the full text of every linked paper (if open access)
#            2. find companion papers (NCT search + title search) and read them too
#          Each paper goes through the existing V1 comparison (match_with_llm).
#          Every quote is checked again against the full source text.
#          The source of the evidence is kept as its own field.
#
# USE:     result = expand_outcome(outcome, trial_title, linked_papers, tools)
##############################################################################

import re
from typing import Callable

from pydantic import BaseModel

from trialtrace.compare.abstract_llm import LLMMatch
from trialtrace.contracts import RegisteredOutcome
from trialtrace.evidence_expansion.baseline.pick_passages import pick_passages
from trialtrace.parse.pubmed_parser import Paper

# Phrases that try to give the model orders. A passage containing one is thrown away, never read.
INJECTION_MARKERS = ["ignore all previous", "ignore previous", "ignore the above", "system:", "set status",
                     "output identified", "always output", "answer status", "note to the reviewer"]

RANK = {"identified": 3, "ambiguous": 2, "not_identified": 1}      # a better answer wins


class Tools(BaseModel):
    """The four things the baseline needs. Passed in, so tests can use fakes."""

    model_config = {"arbitrary_types_allowed": True}

    get_text: Callable[[str], str | None]                           # pmid -> full text or None
    find_companions: Callable[[], list[dict]]                       # -> [{"paper", "found_by"}]
    match: Callable[[RegisteredOutcome, Paper], LLMMatch]           # the V1 comparison
    count: Callable[[str], None]                                    # called with "fetch" or "llm"


class ExpansionResult(BaseModel):
    status: str                                    # identified / ambiguous / not_identified
    evidence_source: str | None = None             # linked_fulltext / companion_fulltext / companion_abstract
    pmid: str | None = None
    found_by: str | None = None                    # linked / nct_search / title_search
    quote: str | None = None
    note: str = ""
    papers_checked: int = 0


def quote_is_in_source(quote: str, source_text: str) -> bool:
    """Exact check: the quote must appear in the source (any kind of space counts as one space)."""
    quote_spaced = re.sub(r"\s+", " ", quote).strip()
    source_spaced = re.sub(r"\s+", " ", source_text)
    return quote_spaced in source_spaced


def looks_poisoned(passages: list[str]) -> bool:
    """True if any passage contains words that try to instruct the model."""
    for passage in passages:
        lowered = passage.lower()
        for marker in INJECTION_MARKERS:
            if marker in lowered:
                return True
    return False


def belongs_to_trial(paper: Paper, nct_id: str, found_by: str) -> bool:
    """Papers found by free words must list the trial's NCT ID in their PubMed record. Others pass."""
    if found_by in ("title_search", "query_search"):
        return nct_id in paper.listed_nct_ids
    return True


def has_number(quote: str) -> bool:
    """A result needs a number or a test; a sentence with no digit is only a description."""
    for character in quote:
        if character.isdigit():
            return True
    return False


def check_one_paper(outcome, paper, source_text, evidence_source, found_by, tools) -> ExpansionResult | None:
    """Compare the outcome with the best passages of one source text. None if the model call failed."""
    if not belongs_to_trial(paper, outcome.nct_id, found_by):
        return None                                # a paper that is not tied to this trial is never evidence
    passages = pick_passages(source_text, outcome.measure_text)
    if not passages:
        return ExpansionResult(status="not_identified", evidence_source=evidence_source, pmid=paper.pmid,
                               found_by=found_by, note="no passage mentions the outcome words")
    if looks_poisoned(passages):
        return None                                # retrieved text is data; text that gives orders is not trusted
    passage_paper = Paper(pmid=paper.pmid, title=paper.title, abstract="\n\n".join(passages))
    tools.count("llm")
    match = tools.match(outcome, passage_paper)
    if match.status not in RANK:
        return None                                # failed or no_text: we do not know, so we do not count it

    quote_text = None
    if match.quote is not None:
        quote_text = match.quote.text
        if not quote_is_in_source(quote_text, source_text):
            return None                            # a quote that fails the check is thrown away
    status = match.status
    if status == "identified" and not has_number(quote_text or ""):
        status = "ambiguous"                       # named but no number or test: not a reported result
    return ExpansionResult(status=status, evidence_source=evidence_source, pmid=paper.pmid,
                           found_by=found_by, quote=quote_text)


def expand_outcome(outcome, trial_title, linked_papers, tools) -> ExpansionResult:
    """Run the fixed steps and return the best result across all papers."""
    results = []

    # 1. Full text of the linked papers.
    for paper in linked_papers:
        tools.count("fetch")
        text = tools.get_text(paper.pmid)
        if text is None:
            continue
        result = check_one_paper(outcome, paper, text, "linked_fulltext", "linked", tools)
        if result is not None:
            results.append(result)

    # 2. Companion papers: full text if open access, otherwise their abstract.
    tools.count("fetch")
    for item in tools.find_companions():
        paper = item["paper"]
        tools.count("fetch")
        text = tools.get_text(paper.pmid)
        if text is not None:
            result = check_one_paper(outcome, paper, text, "companion_fulltext", item["found_by"], tools)
        elif paper.abstract:
            result = check_one_paper(outcome, paper, paper.abstract, "companion_abstract", item["found_by"], tools)
        else:
            result = None
        if result is not None:
            results.append(result)

    # 3. Best answer wins. Nothing found means we keep "not identified" with a clear note.
    if not results:
        return ExpansionResult(status="unresolved", note="no readable source gave an answer")
    best = results[0]
    for result in results:
        if RANK[result.status] > RANK[best.status]:
            best = result
    best.papers_checked = len(results)
    return best

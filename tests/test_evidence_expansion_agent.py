##############################################################################
# tests/test_evidence_expansion_agent.py
#
# PURPOSE: Check the agent's rules with fakes (no internet, no model):
#          budget stop, no repeated query, give up on vague outcomes, early stop.
##############################################################################

from trialtrace.compare.abstract_baseline import TextQuote
from trialtrace.compare.abstract_llm import LLMMatch
from trialtrace.contracts import RegisteredOutcome
from trialtrace.evidence_expansion.agent import agent_ladder
from trialtrace.evidence_expansion.agent.budget import Budget
from trialtrace.parse.pubmed_parser import Paper

HASH = "b" * 64
OUTCOME = RegisteredOutcome(nct_id="NCT01234567", content_hash=HASH, role="primary", position=0, measure_text="Heart rate")
LINKED = [Paper(pmid="1", title="A linked paper", abstract="x")]
QUOTE = "Heart rate fell by 5 bpm."


class FakeSources:
    """Stands in for the cache: full text for pmid 1, one companion paper, one query result."""

    def __init__(self, text_by_pmid, status="identified"):
        self.text_by_pmid = text_by_pmid
        self.status = status
        self.searches = []

    def get_text(self, pmid):
        return self.text_by_pmid.get(pmid)

    def companions(self, nct_id, title, linked_pmids, only=None):
        return [{"paper": Paper(pmid="2", title="Companion", abstract=QUOTE), "found_by": "nct_search"}]

    def search(self, query, linked_pmids):
        self.searches.append(query)
        return []

    def match(self, outcome, paper):
        if self.status == "identified":
            return LLMMatch(status="identified", quote=TextQuote(text=QUOTE, start=0, end=len(QUOTE)))
        return LLMMatch(status=self.status)


def run(monkeypatch, cause, sources, query="new words", max_calls=15):
    monkeypatch.setattr(agent_ladder, "classify_cause", lambda *args: cause)
    monkeypatch.setattr(agent_ladder, "reformulate_query", lambda *args: query)
    budget = Budget(max_calls=max_calls)
    result = agent_ladder.resolve_outcome(None, OUTCOME, "Title", LINKED, sources, budget)
    return result, budget


def test_vague_outcome_is_abandoned_without_any_fetch(monkeypatch):
    result, budget = run(monkeypatch, "vague", FakeSources({}))
    assert result.status == "unresolved"
    assert budget.calls == 0


def test_stops_early_when_evidence_is_verified(monkeypatch):
    result, budget = run(monkeypatch, "text", FakeSources({"1": QUOTE}))
    assert result.status == "identified"
    assert result.evidence_source == "linked_fulltext"
    assert budget.calls == 2                       # one fetch, one comparison, nothing more


def test_paper_cause_starts_with_companion_search(monkeypatch):
    result, budget = run(monkeypatch, "paper", FakeSources({}))
    assert result.evidence_source == "companion_abstract"
    assert result.found_by == "nct_search"


def test_budget_stops_the_agent(monkeypatch):
    sources = FakeSources({"1": "Nothing relevant here."}, status="not_identified")
    result, budget = run(monkeypatch, "text", sources, max_calls=2)
    assert budget.calls <= 2


def test_same_query_is_never_repeated():
    budget = Budget()
    assert budget.already_tried("heart rate trial") is False
    assert budget.already_tried("Heart Rate Trial ") is True


def test_nothing_found_keeps_old_status(monkeypatch):
    result, budget = run(monkeypatch, "text", FakeSources({}, status="not_identified"))
    assert result.status in ("unresolved", "not_identified")

##############################################################################
# tests/test_review_paper.py
#
# PURPOSE: Prove the review step for paper findings: a reviewer can answer "unclear" (with a reason,
#          checked by the code AND the database), the queue is ordered by priority with disagreements,
#          unclear answers and failed checks first, the reviewer sees the evidence TEXT (the quote), each
#          paper finding records the match run it came from, and a review never changes the model's
#          original predictions. A fake model is used; every test is rolled back.
##############################################################################

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import psycopg
import pytest

from trialtrace.checks.paper_link import check_paper_link
from trialtrace.config import Settings
from trialtrace.contracts import Finding, OutcomeExtraction, RegisteredOutcome, Snapshot
from trialtrace.db.repository import (
    get_paper_matches, get_review_history, get_stored_findings, save_papers, save_review, save_run, save_snapshot,
)
from trialtrace.match_papers import match_trial
from trialtrace.parse.pubmed_parser import Paper
from trialtrace.report import build_report
from trialtrace.review.review import needs_review, record_review, review_priority, review_run, show_finding

NCT = "NCT90000066"
HASH, RAW = "a" * 64, "b" * 64
ABSTRACT = "We enrolled 50 patients. Overall survival (OS) was 12.3 months in the treated arm. Safety was assessed."
QUOTE = "Overall survival (OS) was 12.3 months in the treated arm."
WHEN = datetime(2026, 11, 6, tzinfo=timezone.utc)
RUN = "run-test"


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_CHAT_MODEL", raising=False)
    return Settings(_env_file=None)


class Script:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.shown = []

    def ask(self, question):
        return self.answers.pop(0)

    def show(self, text):
        self.shown.append(text)


class FakeClient:
    def __init__(self, reply):
        message = SimpleNamespace(content=reply)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        self.response = SimpleNamespace(choices=[SimpleNamespace(message=message)], model="gpt-test", usage=usage)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: self.response))


def reply(status="identified", quote=QUOTE):
    return json.dumps({"document": "trial_results", "status": status, "quote": quote, "name": "overall survival", "role": "primary"})


def store(conn, outcomes):
    snapshot = Snapshot(nct_id=NCT, title="T", sponsor="Acme", overall_status="COMPLETED", content_hash=HASH, registry_last_update="2025-11-21",
                        has_results=True, source_url=f"https://clinicaltrials.gov/study/{NCT}", raw_file_path="x", retrieved_at=WHEN)
    extraction = OutcomeExtraction(status="ok", outcomes=[
        RegisteredOutcome(nct_id=NCT, content_hash=HASH, role="primary", position=i, measure_text=t, time_frame="5 years") for i, t in enumerate(outcomes)])
    save_snapshot(conn, snapshot, [], extraction)
    paper = Paper(pmid="111", title="Paper", abstract=ABSTRACT, listed_nct_ids=[NCT])
    save_papers(conn, [paper], [check_paper_link(paper, NCT)], RAW, "x.xml", "https://example.org/efetch", WHEN)


def paper_run(conn, settings, outcomes=("Overall Survival (OS)", "Time to death"), run_id="match-1"):
    """Match and build a report run: one low-priority (both found) and one high-priority (systems disagree) finding."""
    store(conn, outcomes)
    match_trial(conn, settings, NCT, run_id, client=FakeClient(reply()), sleep=lambda s: None)
    report = build_report(conn, date(2026, 11, 7), RUN)
    save_run(conn, RUN, report.as_of, report.all_findings())
    return [s for s in get_stored_findings(conn, RUN) if s.finding.check.startswith("paper_outcome")]


def finding_id(stored):
    return stored.finding_id


# --- "unclear" --------------------------------------------------------------------------------------------------

def test_an_unclear_answer_is_saved_with_its_reason(conn, settings):
    [*_, any_finding] = paper_run(conn, settings)
    record_review(conn, any_finding.finding_id, "unclear", "Dr Rao", reason="The abstract reports only a subgroup")
    [row] = get_review_history(conn, any_finding.finding_id)
    assert row["decision"] == "unclear" and row["reason"] == "The abstract reports only a subgroup"


def test_an_unclear_answer_without_a_reason_is_refused_by_the_code(conn, settings):
    [*_, any_finding] = paper_run(conn, settings)
    with pytest.raises(ValueError, match="unclear answer needs a reason"):
        record_review(conn, any_finding.finding_id, "unclear", "Dr Rao")


def test_the_database_also_refuses_an_unclear_answer_without_a_reason(conn, settings):
    [*_, any_finding] = paper_run(conn, settings)
    with pytest.raises(psycopg.errors.CheckViolation):
        save_review(conn, any_finding.finding_id, "unclear", "Dr Rao", reason=None)


def test_an_unclear_review_counts_as_decided_and_shows_in_the_stored_finding(conn, settings):
    [*_, any_finding] = paper_run(conn, settings)
    record_review(conn, any_finding.finding_id, "unclear", "Dr Rao", reason="Cannot tell")
    again = {s.finding_id: s for s in get_stored_findings(conn, RUN)}[any_finding.finding_id]
    assert again.review_decision == "unclear" and not needs_review(again, include_plain_facts=True)


def test_the_loop_records_unclear_with_the_reason_and_counts_it(conn, settings):
    paper_run(conn, settings)
    script = Script("u", "Only a subgroup is reported", "q")
    summary = review_run(conn, RUN, "Dr Rao", ask=script.ask, show=script.show)
    assert summary["unclear"] == 1 and summary["approved"] == 0
    assert any("unclear 1" in line for line in script.shown)


# --- priority and order ----------------------------------------------------------------------------------------------

def test_priorities_for_each_kind_of_finding():
    def finding(category):
        from trialtrace.contracts import StoredFinding
        return StoredFinding(finding_id="f", run_id="r", finding=Finding(nct_id=NCT, check="c", category=category, claim="x", evidence_ids=["e"]))
    assert review_priority(finding("paper_systems_disagree")) == 0
    assert review_priority(finding("paper_outcome_unclear")) == 0
    assert review_priority(finding("paper_check_failed")) == 0
    assert review_priority(finding("reported_outcome_differences")) == 1
    assert review_priority(finding("paper_reports_outcome")) == 2
    assert review_priority(finding("results_available")) == 2


def test_the_queue_shows_the_high_priority_finding_first_and_hides_agreement_by_default(conn, settings):
    paper_run(conn, settings)
    script = Script("q")
    review_run(conn, RUN, "Dr Rao", ask=script.ask, show=script.show)
    text = "\n".join(script.shown)
    assert "1 finding(s) need a decision" in text and "high priority first" in text
    assert "paper_summary_reported_disputed  (priority: high)" in text          # the outcome-level summary is the review unit
    assert "paper_summary_reported  " not in text and "paper_systems_disagree" not in text


def test_everything_in_the_queue_comes_in_priority_order_when_all_is_asked_for(conn, settings):
    paper_run(conn, settings)
    script = Script("s", "s", "s", "s", "s", "s", "s", "s", "s", "s", "s", "s")
    review_run(conn, RUN, "Dr Rao", ask=script.ask, show=script.show, include_plain_facts=True)
    priorities = [line.split("(priority: ")[1].rstrip(")") for line in script.shown if "(priority: " in line]
    assert priorities == sorted(priorities, key={"high": 0, "normal": 1, "low": 2}.get)


# --- what the reviewer sees ----------------------------------------------------------------------------------------------

def test_the_reviewer_reads_the_quote_itself_not_only_its_id(conn, settings):
    from trialtrace.db.repository import get_evidence_by_ids
    findings = paper_run(conn, settings, outcomes=("Overall Survival (OS)",))
    [stored] = [f for f in findings if f.finding.check.startswith("paper_outcome:")]
    shown = []
    show_finding(stored, 1, 1, shown.append, get_evidence_by_ids(conn, stored.finding.evidence_ids))
    text = "\n".join(shown)
    assert f'"{QUOTE}"' in text and "Evidence: " in text
    assert "Built from match run match-1" in text and "original answers are kept unchanged" in text


def test_a_long_evidence_text_is_cut_for_the_screen(conn, settings):
    from trialtrace.review.review import EVIDENCE_PREVIEW_CHARS
    from trialtrace.contracts import Evidence, StoredFinding
    long_text = "x" * (EVIDENCE_PREVIEW_CHARS + 50)
    evidence = {"e": Evidence(evidence_id="e", nct_id=NCT, content_hash=RAW, source="pubmed", source_url="https://example.org/x",
                              field_path="abstract", value=long_text, retrieved_at=WHEN)}
    stored = StoredFinding(finding_id="f", run_id="r", finding=Finding(nct_id=NCT, check="c", category="paper_systems_disagree", claim="x", evidence_ids=["e"]))
    shown = []
    show_finding(stored, 1, 1, shown.append, evidence)
    assert ("x" * EVIDENCE_PREVIEW_CHARS + "...") in "\n".join(shown) and long_text not in "\n".join(shown)


# --- the audit link: original predictions and corrections stay apart -------------------------------------------------------

def test_every_paper_finding_records_the_match_run_it_was_built_from(conn, settings):
    findings = paper_run(conn, settings)
    assert {f.finding.source_run_id for f in findings} == {"match-1"}


def test_other_findings_have_no_source_run(conn, settings):
    paper_run(conn, settings)
    others = [s for s in get_stored_findings(conn, RUN) if not s.finding.check.startswith("paper_outcome")]
    assert others and all(s.finding.source_run_id is None for s in others)


def test_a_review_never_changes_the_models_original_predictions(conn, settings):
    findings = paper_run(conn, settings)
    before = get_paper_matches(conn, NCT, "match-1")
    for stored in findings:
        record_review(conn, stored.finding_id, "rejected", "Dr Rao", reason="Checked the abstract: wrong")
    assert get_paper_matches(conn, NCT, "match-1") == before             # the predictions are exactly as the model made them
    assert all(get_review_history(conn, f.finding_id)[0]["decision"] == "rejected" for f in findings)

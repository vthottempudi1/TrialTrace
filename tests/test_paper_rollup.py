##############################################################################
# tests/test_paper_rollup.py
#
# PURPOSE: Prove the outcome-level roll-up (one finding per registered outcome across the papers read):
#          the rule for identified / unclear / unknown / not identified, "disputed" when the model and the
#          code baseline roll up differently, unread papers (limit, new, changed) turn "not identified" into
#          "unknown" and are named, the supporting quotes are cited, "not identified" is always worded as the
#          abstracts only, summaries are the default review unit, and the report puts them first.
#          A fake model that answers each call in turn is used; every test is rolled back.
##############################################################################

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from trialtrace.checks.paper_link import check_paper_link
from trialtrace.compare.paper_outcomes import abstract_evidence_id
from trialtrace.compare.paper_rollup import SUMMARY_HIGH_RISK, SUMMARY_LOW_RISK, category_for, check_paper_summary, has_result_detail, rollup_statuses, summary_finding
from trialtrace.config import Settings
from trialtrace.contracts import OutcomeExtraction, RegisteredOutcome, Snapshot
from trialtrace.db.repository import get_evidence, get_stored_findings, save_papers, save_run, save_snapshot
from trialtrace.match_papers import match_trial
from trialtrace.parse.pubmed_parser import Paper
from trialtrace.report import build_report, render_text
from trialtrace.review.review import VERDICT_WORDS, needs_review, review_priority

NCT = "NCT90000088"
HASH = "a" * 64
WHEN = datetime(2026, 11, 6, tzinfo=timezone.utc)
HAS_OS = "We enrolled 50 patients. Overall survival (OS) was 12.3 months in the treated arm. Safety was assessed."
QUOTE = "Overall survival (OS) was 12.3 months in the treated arm."
NO_OS = "We enrolled 50 patients. Safety was assessed. Quality of life improved."


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_CHAT_MODEL", raising=False)
    return Settings(_env_file=None)


class TurnClient:
    """A fake model that answers each call in turn (the order is: papers by PMID, then outcomes)."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        text = self.replies.pop(0)
        message = SimpleNamespace(content=text)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], model="gpt-test", usage=usage)


def says(status, quote=QUOTE, document="trial_results"):
    return json.dumps({"document": document, "status": status, "quote": quote, "name": "overall survival", "role": "primary"})


def store(conn, papers, outcomes=("Overall Survival (OS)",)):
    """papers: {pmid: abstract}."""
    snapshot = Snapshot(nct_id=NCT, title="T", sponsor="Acme", overall_status="COMPLETED", content_hash=HASH, registry_last_update="2025-11-21",
                        has_results=True, source_url=f"https://clinicaltrials.gov/study/{NCT}", raw_file_path="x", retrieved_at=WHEN)
    extraction = OutcomeExtraction(status="ok", outcomes=[
        RegisteredOutcome(nct_id=NCT, content_hash=HASH, role="primary", position=i, measure_text=t, time_frame="5 years") for i, t in enumerate(outcomes)])
    save_snapshot(conn, snapshot, [], extraction)
    add_papers(conn, papers, "b" * 64)


def add_papers(conn, papers, raw_hash, when=WHEN):
    items = [Paper(pmid=pmid, title=f"Paper {pmid}", abstract=text, listed_nct_ids=[NCT]) for pmid, text in papers.items()]
    save_papers(conn, items, [check_paper_link(p, NCT) for p in items], raw_hash, "x.xml", "https://example.org/efetch", when)


def run(conn, settings, client, run_id="match-1"):
    match_trial(conn, settings, NCT, run_id, client=client, sleep=lambda s: None)
    return check_paper_summary(conn, NCT)


# --- the rule ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("statuses,unread,answer", [
    (["identified"], False, "identified"),
    (["not_identified", "identified", "failed"], True, "identified"),         # one found result wins, even with failures or unread papers
    (["not_identified", "ambiguous"], False, "unclear"),
    (["ambiguous", "failed"], True, "unclear"),
    (["not_identified", "failed"], False, "unknown"),                          # a failed paper: we cannot say it was not reported
    (["not_identified", "no_text"], False, "unknown"),
    (["not_identified", "not_identified"], True, "unknown"),                   # an unread paper: the same
    (["not_identified", "not_identified"], False, "not_identified"),
    (["not_identified"], False, "not_identified"),
])
def test_the_roll_up_rule(statuses, unread, answer):
    assert rollup_statuses(statuses, unread) == answer


@pytest.mark.parametrize("llm,baseline,category", [
    ("identified", "identified", "paper_summary_reported"),
    ("identified", "not_identified", "paper_summary_reported_disputed"),
    ("identified", "unclear", "paper_summary_reported_disputed"),
    ("unclear", "identified", "paper_summary_unclear"),
    ("unclear", "not_identified", "paper_summary_unclear"),
    ("not_identified", "not_identified", "paper_summary_not_identified"),
    ("not_identified", "identified", "paper_summary_not_identified_disputed"),
    ("not_identified", "unclear", "paper_summary_not_identified_disputed"),
    ("unknown", "identified", "unknown"),
])
def test_the_category_marks_a_difference_between_the_systems_as_disputed(llm, baseline, category):
    assert category_for(llm, baseline) == category


def test_both_systems_identified_but_in_no_common_paper_is_papers_differ():
    assert category_for("identified", "identified", share_a_paper=False) == "paper_summary_reported_papers_differ"
    assert category_for("identified", "identified", share_a_paper=True) == "paper_summary_reported"
    assert "paper_summary_reported_papers_differ" in SUMMARY_HIGH_RISK


def test_the_high_and_low_risk_sets_do_not_overlap():
    assert not (SUMMARY_HIGH_RISK & SUMMARY_LOW_RISK)


# --- one outcome across papers ---------------------------------------------------------------------------------

def test_one_finding_per_registered_outcome_not_per_paper(conn, settings):
    store(conn, {"111": HAS_OS, "222": HAS_OS}, outcomes=("Overall Survival (OS)", "Quality of Life"))
    findings = run(conn, settings, TurnClient(says("identified"), says("not_identified"), says("identified"), says("not_identified")))
    assert len(findings) == 2 and {f.check for f in findings} == {"paper_outcome_summary:primary:0", "paper_outcome_summary:primary:1"}


def test_an_outcome_found_in_one_of_two_papers_is_identified_and_names_that_paper(conn, settings):
    store(conn, {"111": NO_OS, "222": HAS_OS})
    [finding] = run(conn, settings, TurnClient(says("not_identified"), says("identified")))
    assert finding.category == "paper_summary_reported"
    assert "paper(s) 222" in finding.claim and "read 2 paper(s)" in finding.claim
    assert any("111: not_identified; 222: identified" in text for text in finding.limitations)


def test_the_cited_evidence_is_the_registered_outcome_and_the_supporting_quote_only(conn, settings):
    store(conn, {"111": NO_OS, "222": HAS_OS})
    [finding] = run(conn, settings, TurnClient(says("not_identified"), says("identified")))
    start = HAS_OS.index(QUOTE)
    quote_id = abstract_evidence_id(NCT, "b" * 64, "222", start, start + len(QUOTE))
    assert quote_id in finding.evidence_ids
    assert any(i.endswith("primaryOutcomes.0.measure") for i in finding.evidence_ids)
    assert len(finding.evidence_ids) == 2                                     # not the negative paper's abstract
    assert any("verified by code" in text and "for a person to confirm" in text for text in finding.limitations)


def test_any_unclear_paper_makes_the_outcome_unclear(conn, settings):
    store(conn, {"111": HAS_OS, "222": NO_OS})
    [finding] = run(conn, settings, TurnClient(says("unclear"), says("not_identified")))
    assert finding.category == "paper_summary_unclear" and "paper(s) 111" in finding.claim


def test_all_papers_not_identified_says_abstracts_only(conn, settings):
    store(conn, {"111": NO_OS, "222": NO_OS}, outcomes=("Time to progression",))      # a name the baseline cannot find either
    [finding] = run(conn, settings, TurnClient(says("not_identified"), says("not_identified")))
    assert finding.category == "paper_summary_not_identified"
    assert "not identified in the available text (abstracts) of the 2 paper(s) read (111, 222)" in finding.claim
    assert any("does not mean the full paper omits the outcome" in text for text in finding.limitations)
    assert abstract_evidence_id(NCT, "b" * 64, "111") in finding.evidence_ids
    assert abstract_evidence_id(NCT, "b" * 64, "222") in finding.evidence_ids      # one file, two papers: each has its own evidence
    stored = {item.evidence_id: item.value for item in get_evidence(conn, NCT, "b" * 64)}
    assert stored[abstract_evidence_id(NCT, "b" * 64, "111")] == NO_OS
    assert stored[abstract_evidence_id(NCT, "b" * 64, "222")] == NO_OS


def test_a_failed_paper_makes_not_identified_unknown_and_names_the_error(conn, settings):
    store(conn, {"111": NO_OS, "222": HAS_OS}, outcomes=("Quality of Life",))
    [finding] = run(conn, settings, TurnClient(says("not_identified"), says("identified", quote="an invented sentence")))
    assert finding.category == "unknown" and "222" in finding.claim
    assert any("quote_not_found" in text for text in finding.limitations)


# --- disputed ------------------------------------------------------------------------------------------------------

def test_a_difference_between_the_models_and_the_baselines_roll_up_is_disputed(conn, settings):
    store(conn, {"111": HAS_OS})                                               # the baseline finds "overall survival" in the abstract
    [finding] = run(conn, settings, TurnClient(says("not_identified")))
    assert finding.category == "paper_summary_not_identified_disputed"
    assert any("baseline's roll-up for this outcome is 'identified'" in text for text in finding.limitations)


def test_agreement_is_not_disputed(conn, settings):
    store(conn, {"111": HAS_OS})
    [finding] = run(conn, settings, TurnClient(says("identified")))
    assert finding.category == "paper_summary_reported"


# --- papers that were not read -----------------------------------------------------------------------------------------

def test_a_paper_left_out_by_the_limit_makes_not_identified_unknown(conn, settings):
    store(conn, {"111": NO_OS, "222": NO_OS}, outcomes=("Quality of Life",))
    settings.paper_reader_max_papers = 1
    [finding] = run(conn, settings, TurnClient(says("not_identified")))
    assert finding.category == "unknown" and "some listed papers were not read" in finding.claim
    assert any("not read because of the per-trial paper limit (PMID 222)" in text for text in finding.limitations)


def test_an_identified_outcome_stays_identified_when_other_papers_were_not_read(conn, settings):
    store(conn, {"111": HAS_OS, "222": NO_OS})
    settings.paper_reader_max_papers = 1
    [finding] = run(conn, settings, TurnClient(says("identified")))
    assert finding.category == "paper_summary_reported"
    assert any("PMID 222" in text for text in finding.limitations)


def test_a_paper_stored_after_the_run_is_named_and_makes_not_identified_unknown(conn, settings):
    store(conn, {"111": NO_OS}, outcomes=("Quality of Life",))
    run(conn, settings, TurnClient(says("not_identified")))
    add_papers(conn, {"333": NO_OS}, "d" * 64, when=datetime(2026, 11, 9, tzinfo=timezone.utc))
    [finding] = check_paper_summary(conn, NCT)
    assert finding.category == "unknown" and any("stored after this match run were not read (PMID 333)" in t for t in finding.limitations)


# --- the trial level --------------------------------------------------------------------------------------------------------

def test_a_trial_never_matched_has_no_summary_findings(conn):
    store(conn, {"111": HAS_OS})
    assert check_paper_summary(conn, NCT) == []


def test_the_highest_risk_summary_comes_first(conn, settings):
    store(conn, {"111": HAS_OS}, outcomes=("Overall Survival (OS)", "Quality of Life"))
    findings = run(conn, settings, TurnClient(says("identified"), says("not_identified")))
    assert [f.category for f in findings] == ["paper_summary_reported", "paper_summary_not_identified"]     # both agree: by position


def test_a_disputed_summary_comes_before_an_agreed_one(conn, settings):
    store(conn, {"111": HAS_OS}, outcomes=("Quality of Life", "Overall Survival (OS)"))
    findings = run(conn, settings, TurnClient(says("not_identified"), says("not_identified")))       # QoL agrees, OS is disputed
    assert [f.category for f in findings] == ["paper_summary_not_identified_disputed", "paper_summary_not_identified"]


def test_every_summary_records_the_match_run_it_came_from(conn, settings):
    store(conn, {"111": HAS_OS})
    findings = run(conn, settings, TurnClient(says("identified")), "match-77")
    assert {f.source_run_id for f in findings} == {"match-77"} and findings[0].comparison_arm == "abstract"


def test_no_claim_uses_a_verdict_word(conn, settings):
    store(conn, {"111": HAS_OS, "222": NO_OS}, outcomes=("Overall Survival (OS)", "Quality of Life", "Pain"))
    for finding in run(conn, settings, TurnClient(*[says("not_identified")] * 6)):
        assert not VERDICT_WORDS.search(finding.claim)


# --- the report and the review queue -------------------------------------------------------------------------------------------

def report_findings(conn):
    report = build_report(conn, date(2026, 11, 7), "run-1")
    save_run(conn, "run-1", report.as_of, report.all_findings())
    return report, get_stored_findings(conn, "run-1")


def test_the_report_lists_the_summaries_before_the_per_paper_detail(conn, settings):
    store(conn, {"111": HAS_OS})
    run(conn, settings, TurnClient(says("identified")))
    report, _ = report_findings(conn)
    checks = [f.check for f in report.trials[0].findings]
    assert checks.index("paper_outcome_summary:primary:0") < checks.index("paper_outcome:primary:0:111")
    assert "paper_outcome_summary:primary:0" in render_text(report)


def test_the_summary_is_the_default_review_unit_and_the_detail_comes_with_all(conn, settings):
    store(conn, {"111": HAS_OS})
    run(conn, settings, TurnClient(says("not_identified")))                                          # disputed
    _, stored = report_findings(conn)
    summary = [s for s in stored if s.finding.check.startswith("paper_outcome_summary:")][0]
    detail = [s for s in stored if s.finding.check.startswith("paper_outcome:")][0]
    assert needs_review(summary) and not needs_review(detail) and needs_review(detail, include_plain_facts=True)


def test_an_agreed_summary_is_low_priority_and_only_in_the_queue_when_everything_is_asked_for(conn, settings):
    store(conn, {"111": HAS_OS})
    run(conn, settings, TurnClient(says("identified")))
    _, stored = report_findings(conn)
    summary = [s for s in stored if s.finding.check.startswith("paper_outcome_summary:")][0]
    assert summary.finding.category == "paper_summary_reported"
    assert not needs_review(summary) and needs_review(summary, include_plain_facts=True)


# --- papers that differ, completeness, wording ------------------------------------------------------------------------

def match_row(system, pmid, status):
    return {"system": system, "pmid": pmid, "status": status, "outcome_role": "primary", "outcome_position": 0,
            "outcome_text": "Overall Survival", "registry_hash": "c" * 64, "paper_raw_hash": "d" * 64,
            "quote_start": 0, "quote_end": 5, "quote_text": "Overall survival was 12.3 months.", "matched_name": "Overall survival", "error_code": None, "model": None, "prompt_hash": None}


def test_both_systems_identifying_the_outcome_in_different_papers_is_flagged():
    rows = [match_row("llm", "111", "not_identified"), match_row("llm", "222", "identified"),
            match_row("baseline", "111", "identified"), match_row("baseline", "222", "not_identified")]
    unread = {"skipped": [], "new": [], "changed": []}
    finding = summary_finding(NCT, rows, unread, "match-1")
    assert finding.category == "paper_summary_reported_papers_differ"
    assert any("model in 222, the code baseline in 111" in text for text in finding.limitations)
    assert finding.category in SUMMARY_HIGH_RISK


def test_both_systems_sharing_one_paper_is_not_flagged():
    rows = [match_row("llm", "111", "identified"), match_row("llm", "222", "not_identified"),
            match_row("baseline", "111", "identified"), match_row("baseline", "222", "identified")]
    unread = {"skipped": [], "new": [], "changed": []}
    assert summary_finding(NCT, rows, unread, "match-1").category == "paper_summary_reported"


def test_a_complete_reading_is_marked_complete(conn, settings):
    store(conn, {"111": HAS_OS})
    [finding] = run(conn, settings, TurnClient(says("identified")))
    assert finding.evidence_completeness == "complete"


def test_an_agreed_result_with_an_unread_paper_is_incomplete_and_not_low_priority(conn, settings):
    store(conn, {"111": HAS_OS, "222": NO_OS})
    settings.paper_reader_max_papers = 1
    [finding] = run(conn, settings, TurnClient(says("identified")))
    assert finding.category == "paper_summary_reported" and finding.evidence_completeness == "incomplete"
    assert any("more papers could report this outcome" in text for text in finding.limitations)
    _, stored = report_findings(conn)
    summary = [s for s in stored if s.finding.check.startswith("paper_outcome_summary:")][0]
    assert summary.finding.evidence_completeness == "incomplete"               # saved and read back
    assert needs_review(summary)


def test_the_claim_does_not_say_a_result_was_reported(conn, settings):
    store(conn, {"111": HAS_OS})
    [finding] = run(conn, settings, TurnClient(says("identified")))
    assert "evidence that may report a result" in finding.claim


# --- supporting sentence without a result -------------------------------------------------------------------------------

LIST_SENTENCE = ("There was no difference between arms in weight (estimated mean difference at 3 months = 0.2 kg, 95% CI: -0.3 to 0.7), "
                 "insulin resistance, perceived risk, or physical activity at 3 or 6 months.")


@pytest.mark.parametrize("sentence,name,expected", [
    (LIST_SENTENCE, "in weight", True),                       # the numbers sit in the brackets right after the name
    (LIST_SENTENCE, "insulin resistance", False),             # named in the list, no result of its own
    (LIST_SENTENCE, "physical activity", False),              # "at 3 or 6 months" is a time point, not a result
    ("Calorie and fat intake were lower in the CR+G arm at 3 months (p's <= 0.05).", "Calorie and fat intake", True),
    ("Median duration of response was 16.8 months (95% CI, 6.4 to NE).", "duration of response", True),   # a time as the result
    ("Health-related quality of life was preserved with no adverse effects.", "Health-related quality of life", False),
    ("Survival improved significantly with treatment.", "Survival", True),
    ("The trial measured survival.", "Pain", False),          # the name is not in the sentence
])
def test_result_detail_is_a_number_or_test_next_to_the_name(sentence, name, expected):
    assert has_result_detail(sentence, name) is expected


def test_an_agreed_result_whose_sentence_gives_no_number_is_high_priority_and_says_why(conn, settings):
    no_number = "Overall survival (OS) was reported."
    store(conn, {"111": "We enrolled 50 patients. " + no_number + " Safety was assessed."})
    [finding] = run(conn, settings, TurnClient(says("identified", quote=no_number)))
    assert finding.category == "paper_summary_reported_no_detail"
    assert any("gives a number or a test" in text for text in finding.limitations)
    _, stored = report_findings(conn)
    summary = [s for s in stored if s.finding.check.startswith("paper_outcome_summary:")][0]
    assert needs_review(summary) and review_priority(summary) == 0


def test_an_agreed_result_with_a_number_stays_low_priority(conn, settings):
    store(conn, {"111": HAS_OS})
    [finding] = run(conn, settings, TurnClient(says("identified")))
    assert finding.category == "paper_summary_reported"

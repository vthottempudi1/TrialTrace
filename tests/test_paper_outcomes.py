##############################################################################
# tests/test_paper_outcomes.py
#
# PURPOSE: Prove that stored paper-match predictions become correct, reviewable findings:
#          the right category for each agreement, a plain neutral claim ("not identified in the
#          available text (abstract)", never an omission), evidence that exists in the database
#          (the registered outcome and the abstract quote), highest review risk first, "unknown"
#          for a trial that was never matched, and correct handling in the report and review
#          queue. A fake model is used; every test is rolled back.
##############################################################################

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from trialtrace.checks.paper_link import check_paper_link
from trialtrace.compare.paper_outcomes import abstract_evidence_id, check_paper_outcomes
from trialtrace.config import Settings
from trialtrace.contracts import OutcomeExtraction, RegisteredOutcome, Snapshot
from trialtrace.db.repository import get_evidence, get_stored_findings, save_papers, save_run, save_snapshot
from trialtrace.match_papers import match_trial
from trialtrace.report import build_report, render_text
from trialtrace.review.review import VERDICT_WORDS, needs_review

NCT = "NCT90000055"
HASH = "a" * 64
RAW = "b" * 64
ABSTRACT = "We enrolled 50 patients. The primary end point was overall survival (OS). Safety was assessed."
QUOTE = "The primary end point was overall survival (OS)."
WHEN = datetime(2026, 11, 6, tzinfo=timezone.utc)
AS_OF = date(2026, 11, 7)


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_CHAT_MODEL", raising=False)
    return Settings(_env_file=None)


class FakeClient:
    def __init__(self, reply):
        message = SimpleNamespace(content=reply)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        self.response = SimpleNamespace(choices=[SimpleNamespace(message=message)], model="gpt-test", usage=usage)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: self.response))


def reply(status="identified", quote=QUOTE, name="overall survival", document="trial_results"):
    return json.dumps({"document": document, "status": status, "quote": quote, "name": name, "role": "primary"})


def outcome(position, text, content_hash=HASH):
    return RegisteredOutcome(nct_id=NCT, content_hash=content_hash, role="primary", position=position, measure_text=text, time_frame="5 years")


def store(conn, outcomes=("Overall Survival (OS)",), abstract=ABSTRACT, content_hash=HASH, when=WHEN):
    snapshot = Snapshot(nct_id=NCT, title="T", sponsor="Acme", overall_status="COMPLETED", content_hash=content_hash,
                        registry_last_update="2025-11-21", has_results=True, source_url=f"https://clinicaltrials.gov/study/{NCT}",
                        raw_file_path="x", retrieved_at=when)
    extraction = OutcomeExtraction(status="ok", outcomes=[outcome(i, text, content_hash) for i, text in enumerate(outcomes)])
    save_snapshot(conn, snapshot, [], extraction)
    from trialtrace.parse.pubmed_parser import Paper
    paper = Paper(pmid="111", title="Paper", abstract=abstract, listed_nct_ids=[NCT])
    save_papers(conn, [paper], [check_paper_link(paper, NCT)], RAW, "data/raw/x.xml", "https://example.org/efetch", WHEN)


def run_match(conn, settings, client_reply, run_id="match-1", refresh=False):
    match_trial(conn, settings, NCT, run_id, client=FakeClient(client_reply), sleep=lambda s: None, refresh=refresh)


# --- categories and wording -----------------------------------------------------------------------

def test_both_systems_finding_a_result_is_a_low_priority_provisional_finding(conn, settings):
    store(conn)
    run_match(conn, settings, reply())
    [finding] = check_paper_outcomes(conn, NCT)
    assert finding.check == "paper_outcome:primary:0:111" and finding.category == "paper_reports_outcome"
    assert finding.comparison_arm == "abstract" and finding.match_method == "semantic"
    assert any("Provisional" in text for text in finding.limitations)


def test_not_identified_is_worded_as_the_abstract_only_never_as_an_omission(conn, settings):
    store(conn, outcomes=("Quality of Life",))
    run_match(conn, settings, reply(status="not_identified"))
    [finding] = check_paper_outcomes(conn, NCT)
    assert finding.category == "paper_outcome_not_identified"
    assert "not identified in the available text (abstract)" in finding.claim
    assert any("does not mean the full paper omits the outcome" in text for text in finding.limitations)


def test_the_model_finding_something_the_baseline_did_not_is_a_disagreement(conn, settings):
    store(conn, outcomes=("Time to death",))        # the baseline cannot match this wording; the fake model still claims a result
    run_match(conn, settings, reply())
    [finding] = check_paper_outcomes(conn, NCT)
    assert finding.category == "paper_systems_disagree"
    assert "The model found a result" in finding.claim and "baseline did not" in finding.claim


def test_an_unclear_model_answer_is_its_own_category(conn, settings):
    store(conn)
    run_match(conn, settings, reply(status="unclear"))
    [finding] = check_paper_outcomes(conn, NCT)
    assert finding.category == "paper_outcome_unclear"


def test_a_rejected_model_answer_is_a_failed_check_that_names_the_reason_and_the_provisional_baseline(conn, settings):
    store(conn)
    run_match(conn, settings, reply(quote="an invented sentence"))
    [finding] = check_paper_outcomes(conn, NCT)
    assert finding.category == "paper_check_failed"
    assert "quote_not_found" in finding.claim and "only provisional" in finding.claim


def test_no_claim_uses_a_verdict_word(conn, settings):
    store(conn, outcomes=("Overall Survival (OS)", "Time to death", "Quality of Life"))
    run_match(conn, settings, reply())
    for finding in check_paper_outcomes(conn, NCT):
        assert not VERDICT_WORDS.search(finding.claim)


# --- evidence ---------------------------------------------------------------------------------------

def test_the_evidence_ids_exist_in_the_database_and_hold_the_exact_quote(conn, settings):
    store(conn)
    run_match(conn, settings, reply())
    [finding] = check_paper_outcomes(conn, NCT)
    start = ABSTRACT.index(QUOTE)
    quote_id = abstract_evidence_id(NCT, RAW, "111", start, start + len(QUOTE))
    assert quote_id in finding.evidence_ids
    assert any(i.startswith(f"{NCT}:{HASH[:12]}:protocolSection.outcomesModule.primaryOutcomes.0.measure") for i in finding.evidence_ids)
    stored = {item.evidence_id: item for item in get_evidence(conn, NCT, RAW)}
    assert stored[quote_id].value == QUOTE and stored[quote_id].source == "pubmed"
    assert stored[abstract_evidence_id(NCT, RAW, "111")].value == ABSTRACT                   # the whole abstract is saved too


def test_a_finding_with_no_quote_cites_the_whole_abstract(conn, settings):
    store(conn, outcomes=("Quality of Life",))
    run_match(conn, settings, reply(status="not_identified"))
    [finding] = check_paper_outcomes(conn, NCT)
    assert abstract_evidence_id(NCT, RAW, "111") in finding.evidence_ids


# --- never checked, ordering, old versions ------------------------------------------------------------

def test_a_trial_never_matched_has_one_unknown_finding_not_a_silent_no(conn):
    store(conn)
    [finding] = check_paper_outcomes(conn, NCT)
    assert finding.check == "paper_outcomes" and finding.category == "unknown"
    assert "has not been checked" in finding.claim


def test_the_highest_review_risk_comes_first(conn, settings):
    store(conn, outcomes=("Overall Survival (OS)", "Time to death"))
    run_match(conn, settings, reply())
    categories = [f.category for f in check_paper_outcomes(conn, NCT)]
    assert categories == ["paper_systems_disagree", "paper_reports_outcome"]       # the disagreement first


def test_only_the_newest_match_run_is_shown(conn, settings):
    store(conn)
    run_match(conn, settings, reply(status="not_identified"), "match-1")
    run_match(conn, settings, reply(), "match-2", refresh=True)         # refresh: ask the model again
    [finding] = check_paper_outcomes(conn, NCT)
    assert finding.category == "paper_reports_outcome"


def test_a_newer_registry_version_adds_a_limitation(conn, settings):
    store(conn)
    run_match(conn, settings, reply())
    store(conn, content_hash="c" * 64, when=datetime(2026, 11, 8, tzinfo=timezone.utc))      # the record changed after the match run
    [finding] = check_paper_outcomes(conn, NCT)
    assert any("older saved version" in text for text in finding.limitations)


# --- the review queue and the report --------------------------------------------------------------------

def stored_findings(conn, run_id="run-1"):
    report = build_report(conn, AS_OF, run_id)
    save_run(conn, run_id, AS_OF, report.all_findings())
    pair_level = [s for s in get_stored_findings(conn, run_id) if s.finding.check.startswith("paper_outcome:") or s.finding.check == "paper_outcomes"]
    return report, pair_level


def test_disagreements_are_in_the_queue_and_agreement_only_when_everything_is_asked_for(conn, settings):
    store(conn, outcomes=("Overall Survival (OS)", "Time to death"))
    run_match(conn, settings, reply())
    _, stored = stored_findings(conn)
    by_category = {s.finding.category: s for s in stored}
    assert not needs_review(by_category["paper_systems_disagree"])        # per-paper detail: the outcome summary is the review unit
    assert needs_review(by_category["paper_systems_disagree"], include_plain_facts=True)
    assert needs_review(by_category["paper_reports_outcome"], include_plain_facts=True)


def test_an_unchecked_trial_is_never_in_the_queue(conn):
    store(conn)
    _, stored = stored_findings(conn)
    assert [s.finding.category for s in stored] == ["unknown"] and not needs_review(stored[0], include_plain_facts=True)


def test_the_report_shows_the_paper_findings_and_saves_them_with_their_evidence(conn, settings):
    store(conn)
    run_match(conn, settings, reply())
    report, stored = stored_findings(conn)
    text = render_text(report)
    assert "paper_outcome:primary:0:111" in text and "paper_reports_outcome" in text and "Compared: abstract, matched semantic" in text
    [row] = stored
    assert row.finding.evidence_ids == report.trials[0].findings[-1].evidence_ids
    assert row.finding.comparison_arm == "abstract"


def test_papers_left_out_by_the_limit_are_named_in_every_finding_of_that_run(conn, settings):
    from trialtrace.parse.pubmed_parser import Paper
    store(conn)
    extra = [Paper(pmid=pmid, title=f"Paper {pmid}", abstract=ABSTRACT, listed_nct_ids=[NCT]) for pmid in ("222", "333")]
    save_papers(conn, extra, [check_paper_link(p, NCT) for p in extra], "d" * 64, "data/raw/y.xml", "https://example.org/efetch", WHEN)
    settings.paper_reader_max_papers = 1
    run_match(conn, settings, reply())
    findings = check_paper_outcomes(conn, NCT)
    assert len(findings) == 1
    assert any("2 other listed paper(s) were not read" in text and "222, 333" in text for text in findings[0].limitations)


def test_no_limitation_about_skipped_papers_when_none_were_left_out(conn, settings):
    store(conn)
    run_match(conn, settings, reply())
    [finding] = check_paper_outcomes(conn, NCT)
    assert not any("not read because of the per-trial paper limit" in text for text in finding.limitations)


def store_more_papers(conn, pmids, raw_hash, abstract=ABSTRACT, when=WHEN):
    from trialtrace.parse.pubmed_parser import Paper
    papers = [Paper(pmid=pmid, title=f"Paper {pmid}", abstract=abstract, listed_nct_ids=[NCT]) for pmid in pmids]
    save_papers(conn, papers, [check_paper_link(p, NCT) for p in papers], raw_hash, "data/raw/z.xml", "https://example.org/efetch", when)


def test_a_paper_stored_after_the_match_run_is_named_as_not_read(conn, settings):
    store(conn)
    run_match(conn, settings, reply())
    store_more_papers(conn, ["222"], "d" * 64, when=datetime(2026, 11, 9, tzinfo=timezone.utc))      # found by a later PubMed search
    [finding] = check_paper_outcomes(conn, NCT)
    assert any("stored after this match run were not read (PMID 222)" in text for text in finding.limitations)


def test_a_changed_abstract_after_the_match_run_is_flagged(conn, settings):
    store(conn)
    run_match(conn, settings, reply())
    store_more_papers(conn, ["111"], "d" * 64, abstract=ABSTRACT + " A correction was added.", when=datetime(2026, 11, 9, tzinfo=timezone.utc))
    [finding] = check_paper_outcomes(conn, NCT)
    assert any("abstract text of paper(s) 111 changed" in text for text in finding.limitations)


def test_a_new_download_with_the_same_abstract_text_is_not_flagged(conn, settings):
    store(conn)
    run_match(conn, settings, reply())
    store_more_papers(conn, ["111"], "d" * 64, when=datetime(2026, 11, 9, tzinfo=timezone.utc))      # metadata changed, abstract did not
    [finding] = check_paper_outcomes(conn, NCT)
    assert not any("changed after this match run" in text or "were not read (PMID" in text for text in finding.limitations)


def test_a_paper_the_run_left_out_because_of_the_limit_is_not_called_new(conn, settings):
    store(conn)
    store_more_papers(conn, ["222"], "d" * 64)
    settings.paper_reader_max_papers = 1
    run_match(conn, settings, reply())                                  # 222 is recorded as skipped by the limit
    [finding] = check_paper_outcomes(conn, NCT)
    assert not any("stored after this match run" in text for text in finding.limitations)
    assert any("not read because of the per-trial paper limit" in text for text in finding.limitations)


def test_a_new_match_run_clears_the_staleness_notes(conn, settings):
    store(conn)
    run_match(conn, settings, reply(), "match-1")
    store_more_papers(conn, ["222"], "d" * 64, when=datetime(2026, 11, 9, tzinfo=timezone.utc))
    run_match(conn, settings, reply(), "match-2")
    findings = check_paper_outcomes(conn, NCT)
    assert len(findings) == 2 and not any("stored after this match run" in t for f in findings for t in f.limitations)


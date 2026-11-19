##############################################################################
# tests/test_report.py
#
# PURPOSE: Prove the report covers every stored trial and every check, shows
#          unknown as unknown, carries the "ingested corpus only" label, avoids
#          verdict words, and that saving a run keeps its findings.
#          Uses the test database; every test is rolled back.
##############################################################################

from datetime import date, datetime, timezone

import psycopg
import pytest

from trialtrace import report as report_module
from trialtrace.contracts import Evidence, OutcomeExtraction, RegisteredOutcome, Snapshot
from trialtrace.db.repository import (
    get_latest_run_id, get_stored_findings, save_run, save_snapshot,
)
from trialtrace.report import build_report, new_run_id, render_text

WHEN = datetime(2026, 1, 1, tzinfo=timezone.utc)
AS_OF = date(2026, 11, 2)
PATHS = {
    "date": "protocolSection.statusModule.primaryCompletionDateStruct.date",
    "type": "protocolSection.statusModule.primaryCompletionDateStruct.type",
    "results": "hasResults",
}


def add_trial(conn, nct, *, sponsor="Acme Pharma", has_results=True, completion="2019-05", digit="a"):
    """Store one trial with the evidence the checks need and one outcome."""
    content_hash = digit * 64
    snapshot = Snapshot(
        nct_id=nct, title=f"Study {nct}", sponsor=sponsor, overall_status="COMPLETED", has_results=has_results,
        primary_completion_date=completion, primary_completion_type="ACTUAL" if completion else None,
        content_hash=content_hash, source_url=f"https://clinicaltrials.gov/study/{nct}",
        raw_file_path=f"data/raw/clinicaltrials/{nct}/{content_hash}.json", retrieved_at=WHEN,
    )
    evidence = [
        Evidence(evidence_id=f"{nct}:{content_hash[:12]}:{path}", nct_id=nct, content_hash=content_hash,
                 source="clinicaltrials", source_url=f"https://clinicaltrials.gov/study/{nct}",
                 field_path=path, value="x", retrieved_at=WHEN)
        for path in PATHS.values()
    ]
    outcome = RegisteredOutcome(nct_id=nct, content_hash=content_hash, role="primary", position=0,
                                measure_text="Overall survival")
    save_snapshot(conn, snapshot, evidence, OutcomeExtraction(status="ok", outcomes=[outcome]))


def test_every_stored_trial_gets_all_six_checks(conn):
    add_trial(conn, "NCT90000001")
    add_trial(conn, "NCT90000002", has_results=False)
    report = build_report(conn, AS_OF, "run-test")
    assert [t.nct_id for t in report.trials] == ["NCT90000001", "NCT90000002"]
    for trial in report.trials:
        assert [f.check for f in trial.findings] == ["results_posted", "timeline", "registry_changes", "reported_outcomes", "safety_registry", "paper_outcomes"]
        assert trial.findings[-1].category == "unknown"            # never checked against papers: unknown, not "nothing reported"


def test_the_findings_match_the_stored_facts(conn):
    add_trial(conn, "NCT90000001", has_results=True)
    add_trial(conn, "NCT90000002", has_results=False)
    by_trial = {t.nct_id: {f.check: f.category for f in t.findings} for t in build_report(conn, AS_OF, "r").trials}
    assert by_trial["NCT90000001"]["results_posted"] == "results_available"
    assert by_trial["NCT90000002"]["results_posted"] == "results_not_in_snapshot"
    assert by_trial["NCT90000001"]["timeline"] == "over_12_months"
    assert by_trial["NCT90000001"]["registry_changes"] == "unknown"     # only one stored version


def test_a_trial_without_a_completion_date_is_unknown_not_dropped(conn):
    add_trial(conn, "NCT90000001", completion=None)
    trial = build_report(conn, AS_OF, "r").trials[0]
    assert {f.check: f.category for f in trial.findings}["timeline"] == "unknown"


def test_sponsor_and_pattern_sections_carry_the_corpus_label(conn):
    add_trial(conn, "NCT90000001")
    report = build_report(conn, AS_OF, "r")
    assert report.sponsor_records[0].scope == "in the ingested corpus only"
    assert all(p.scope == "in the ingested corpus only" for p in report.patterns)
    text = render_text(report)
    assert "in the ingested corpus only" in text.split("## Completed trials by sponsor")[1]


def test_the_text_has_each_trial_each_check_and_no_verdict_words(conn):
    add_trial(conn, "NCT90000001")
    text = render_text(build_report(conn, AS_OF, "run-test"))
    assert "# TrialTrace report run-test" in text and "## NCT90000001  Study NCT90000001" in text
    assert "[results_posted]" in text and "[timeline]" in text and "[registry_changes]" in text
    assert "Evidence: NCT90000001:" in text and "Limitation:" in text
    lowered = text.lower()
    assert not any(word in lowered for word in ("violation", "non-compliant", "fraud", "late", "switch"))


def test_one_failing_trial_does_not_cost_the_whole_report(conn, monkeypatch):
    add_trial(conn, "NCT90000001")
    add_trial(conn, "NCT90000002")
    real_timeline = report_module.check_timeline

    def timeline_that_fails_for_one(nct_id, snapshot, evidence, as_of):
        if nct_id == "NCT90000001":
            raise RuntimeError("something unexpected")
        return real_timeline(nct_id, snapshot, evidence, as_of)

    monkeypatch.setattr(report_module, "check_timeline", timeline_that_fails_for_one)
    report = build_report(conn, AS_OF, "r")

    failed, fine = report.trials
    assert [f.check for f in failed.findings] == ["report"] and failed.findings[0].category == "unknown"
    assert "RuntimeError" in failed.findings[0].limitations[0]
    assert len(fine.findings) == 6                                    # the other trial is complete
    assert "could not be built" in render_text(report)


def test_a_trial_that_has_lost_its_stored_version_is_shown_as_unknown(conn, monkeypatch):
    add_trial(conn, "NCT90000001")
    monkeypatch.setattr(report_module, "get_latest_snapshot", lambda conn, nct_id: None)
    report = build_report(conn, AS_OF, "r")
    assert report.trials[0].findings[0].category == "unknown"
    assert "ValueError" in report.trials[0].findings[0].limitations[0]


def test_a_failed_trial_entry_can_be_saved_as_part_of_the_run(conn, monkeypatch):
    add_trial(conn, "NCT90000001")
    monkeypatch.setattr(report_module, "get_latest_snapshot", lambda conn, nct_id: None)
    report = build_report(conn, AS_OF, "run-failed")
    save_run(conn, report.run_id, report.as_of, report.all_findings())
    assert [s.finding.check for s in get_stored_findings(conn, "run-failed")] == ["report"]


def test_a_database_failure_still_stops_the_report(conn, monkeypatch):
    add_trial(conn, "NCT90000001")

    def database_down(nct_id, snapshot, evidence, as_of):
        raise psycopg.OperationalError("connection lost")

    monkeypatch.setattr(report_module, "check_timeline", database_down)
    with pytest.raises(psycopg.OperationalError):
        build_report(conn, AS_OF, "r")


def test_an_empty_database_gives_an_empty_but_valid_report(conn):
    report = build_report(conn, AS_OF, "r")
    assert report.trials == [] and "0 trial(s)" in render_text(report)


def test_saving_a_run_keeps_every_finding_and_the_run_is_the_latest(conn):
    add_trial(conn, "NCT90000001")
    report = build_report(conn, AS_OF, "run-20261102T000000Z")
    save_run(conn, report.run_id, report.as_of, report.all_findings())
    assert get_latest_run_id(conn) == "run-20261102T000000Z"
    stored = get_stored_findings(conn, report.run_id)
    assert len(stored) == 6 and all(s.review_decision is None for s in stored)
    changes = [s for s in stored if s.finding.check == "registry_changes"][0]
    assert changes.finding_id == "run-20261102T000000Z:NCT90000001:registry_changes"
    assert changes.finding.evidence_ids == report.trials[0].findings[2].evidence_ids


def test_a_second_run_does_not_change_the_first(conn):
    add_trial(conn, "NCT90000001")
    first = build_report(conn, AS_OF, "run-a")
    save_run(conn, "run-a", AS_OF, first.all_findings())
    second = build_report(conn, date(2027, 1, 1), "run-b")
    save_run(conn, "run-b", second.as_of, second.all_findings())
    assert len(get_stored_findings(conn, "run-a")) == 6 and len(get_stored_findings(conn, "run-b")) == 6


def test_new_run_ids_look_like_timestamps():
    assert new_run_id().startswith("run-20") and new_run_id().endswith("Z")

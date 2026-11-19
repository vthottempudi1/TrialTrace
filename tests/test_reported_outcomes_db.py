##############################################################################
# tests/test_reported_outcomes_db.py
#
# PURPOSE: Prove the results-arm check works from the database: it reads the newest
#          saved version, never calls "not extracted" a match, its findings keep
#          comparison_arm and match_method when saved, and the report and review
#          queue treat them correctly. Every test is rolled back.
##############################################################################

from datetime import date, datetime, timedelta, timezone

from trialtrace.compare.reported_outcomes import check_reported_outcomes
from trialtrace.contracts import OutcomeExtraction, RegisteredOutcome, ResultsExtraction, ResultsOutcome, Snapshot
from trialtrace.db.repository import get_stored_findings, save_run, save_snapshot
from trialtrace.report import build_report, render_text
from trialtrace.review.review import needs_review

NCT = "NCT90000077"
HASH_A, HASH_B = "a" * 64, "b" * 64
DAY1 = datetime(2026, 11, 2, tzinfo=timezone.utc)


def snapshot(content_hash=HASH_A, when=DAY1):
    return Snapshot(
        nct_id=NCT, title="T", sponsor="Acme", overall_status="COMPLETED", content_hash=content_hash,
        registry_last_update="2025-11-21", has_results=True,
        source_url=f"https://clinicaltrials.gov/study/{NCT}", raw_file_path="x", retrieved_at=when,
    )


def registered(content_hash, *titles):
    return OutcomeExtraction(status="ok", outcomes=[
        RegisteredOutcome(nct_id=NCT, content_hash=content_hash, role="primary", position=i, measure_text=t, time_frame="12 months")
        for i, t in enumerate(titles)])


def reported(content_hash, *titles, time_frame="12 months"):
    return ResultsExtraction(status="ok", outcomes=[
        ResultsOutcome(nct_id=NCT, content_hash=content_hash, position=i, result_type="primary", measure_text=t, time_frame=time_frame)
        for i, t in enumerate(titles)])


def test_matching_outcomes_from_the_database_give_no_differences(conn):
    save_snapshot(conn, snapshot(), [], registered(HASH_A, "OS", "PFS"), reported(HASH_A, "OS", "PFS"))
    result = check_reported_outcomes(conn, NCT)
    assert result.status == "no_differences" and result.captured_at == "2026-11-02"
    assert result.registry_update == "2025-11-21"


def test_a_difference_from_the_database_is_found(conn):
    save_snapshot(conn, snapshot(), [], registered(HASH_A, "OS"), reported(HASH_A, "OS", time_frame="24 months"))
    result = check_reported_outcomes(conn, NCT)
    assert result.status == "differences" and [c.kind for c in result.differences] == ["time_frame_differs"]


def test_only_the_newest_version_is_compared(conn):
    save_snapshot(conn, snapshot(HASH_A, DAY1), [], registered(HASH_A, "OS"), reported(HASH_A, "Different"))
    save_snapshot(conn, snapshot(HASH_B, DAY1 + timedelta(days=2)), [], registered(HASH_B, "OS"), reported(HASH_B, "OS"))
    assert check_reported_outcomes(conn, NCT).status == "no_differences"


def test_a_trial_that_is_not_stored_is_unknown(conn):
    result = check_reported_outcomes(conn, "NCT00000001")
    assert result.status == "unknown" and result.reason == "This trial is not stored."


def test_results_never_extracted_is_unknown_not_a_match(conn):
    save_snapshot(conn, snapshot(), [], registered(HASH_A, "OS"))               # results not passed: unknown
    assert check_reported_outcomes(conn, NCT).status == "unknown"


def test_no_results_posted_is_not_a_match(conn):
    save_snapshot(conn, snapshot(), [], registered(HASH_A, "OS"), ResultsExtraction(status="absent"))
    assert check_reported_outcomes(conn, NCT).status == "no_results_posted"


def test_saved_findings_keep_the_arm_and_the_method(conn):
    save_snapshot(conn, snapshot(), [], registered(HASH_A, "OS"), reported(HASH_A, "OS", time_frame="24 months"))
    finding = check_reported_outcomes(conn, NCT).to_finding()
    save_run(conn, "run-x", date(2026, 11, 4), [finding])
    [stored] = get_stored_findings(conn, "run-x")
    assert stored.finding.comparison_arm == "results_submission" and stored.finding.match_method == "deterministic"
    assert stored.finding.category == "reported_outcome_differences" and stored.finding.evidence_ids


def test_findings_without_an_arm_still_save_as_none(conn):
    save_snapshot(conn, snapshot(), [])
    report = build_report(conn, date(2026, 11, 4), "run-y")
    save_run(conn, "run-y", report.as_of, report.all_findings())
    by_check = {s.finding.check: s.finding for s in get_stored_findings(conn, "run-y")}
    assert by_check["timeline"].comparison_arm is None and by_check["timeline"].match_method is None
    assert by_check["reported_outcomes"].comparison_arm == "results_submission"     # unknown, but still labelled


def test_the_report_shows_the_arm_and_method(conn):
    save_snapshot(conn, snapshot(), [], registered(HASH_A, "OS"), reported(HASH_A, "OS"))
    text = render_text(build_report(conn, date(2026, 11, 4), "run-z"))
    assert "[reported_outcomes] reported_outcomes_match" in text
    assert "Compared: results_submission, matched deterministic" in text


def test_a_match_is_not_queued_for_review_but_a_difference_is(conn):
    save_snapshot(conn, snapshot(HASH_A, DAY1), [], registered(HASH_A, "OS"), reported(HASH_A, "OS"))
    match = check_reported_outcomes(conn, NCT).to_finding()
    save_snapshot(conn, snapshot(HASH_B, DAY1 + timedelta(days=2)), [], registered(HASH_B, "OS"), reported(HASH_B, "OS", time_frame="1 month"))
    difference = check_reported_outcomes(conn, NCT).to_finding()
    save_run(conn, "run-q", date(2026, 11, 4), [match])
    save_run(conn, "run-r", date(2026, 11, 4), [difference])
    assert not needs_review(get_stored_findings(conn, "run-q")[0])
    assert needs_review(get_stored_findings(conn, "run-r")[0])


def test_capture_dates_are_utc_whatever_the_database_time_zone(conn):
    conn.execute("SET LOCAL timezone = 'America/Los_Angeles'")              # 00:30 UTC on the 2nd is still the 1st there
    save_snapshot(conn, snapshot(when=datetime(2026, 11, 2, 0, 30, tzinfo=timezone.utc)), [], registered(HASH_A, "OS"), reported(HASH_A, "OS"))
    assert check_reported_outcomes(conn, NCT).captured_at == "2026-11-02"

##############################################################################
# tests/test_registry_changes_db.py
#
# PURPOSE: Prove question 5a end to end through the database: the right two
#          versions are loaded (including A -> B -> A) and compared.
#          Uses the local database; every test is rolled back.
##############################################################################

from datetime import datetime, timezone


from trialtrace.compare.registry_changes import check_registry_changes
from trialtrace.contracts import OutcomeExtraction, RegisteredOutcome, Snapshot
from trialtrace.db.repository import get_recent_versions, save_snapshot

NCT = "NCT90000077"
HASH_A, HASH_B = "a" * 64, "b" * 64
DAY1, DAY2, DAY3 = (datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc),
                    datetime(2026, 3, 1, tzinfo=timezone.utc))


def save(conn, content_hash, when, time_frame):
    """Save one version of the trial with one primary outcome whose time frame we choose."""
    snapshot = Snapshot(
        nct_id=NCT, title="T", overall_status="COMPLETED", content_hash=content_hash,
        source_url=f"https://clinicaltrials.gov/study/{NCT}", raw_file_path="data/x.json",
        retrieved_at=when, registry_last_update="2026-01-10",
    )
    outcome = RegisteredOutcome(nct_id=NCT, content_hash=content_hash, role="primary", position=0,
                                measure_text="Overall survival", time_frame=time_frame)
    save_snapshot(conn, snapshot, [], OutcomeExtraction(status="ok", outcomes=[outcome]))


def test_recent_versions_are_newest_first_with_their_last_seen_date(conn):
    save(conn, HASH_A, DAY1, "12 months")
    save(conn, HASH_B, DAY2, "24 months")
    versions = get_recent_versions(conn, NCT)
    assert [(s.content_hash, seen) for s, seen in versions] == [(HASH_B, DAY2), (HASH_A, DAY1)]


def test_a_b_a_gives_a_as_newest_and_b_as_previous(conn):
    save(conn, HASH_A, DAY1, "12 months")
    save(conn, HASH_B, DAY2, "24 months")
    save(conn, HASH_A, DAY3, "12 months")
    versions = get_recent_versions(conn, NCT)
    assert [(s.content_hash, seen) for s, seen in versions] == [(HASH_A, DAY3), (HASH_B, DAY2)]


def test_unknown_trial_has_no_versions(conn):
    assert get_recent_versions(conn, "NCT99999999") == []


def test_check_finds_a_time_frame_change(conn):
    save(conn, HASH_A, DAY1, "12 months")
    save(conn, HASH_B, DAY2, "24 months")
    report = check_registry_changes(conn, NCT)
    assert report.status == "changes"
    assert [c.kind for c in report.changes] == ["time_frame_changed"]
    assert report.older_captured == DAY1 and report.newer_captured == DAY2


def test_check_with_one_stored_version_is_unknown_not_no_change(conn):
    save(conn, HASH_A, DAY1, "12 months")
    report = check_registry_changes(conn, NCT)
    assert report.status == "unknown" and "earlier version" in report.reason


def test_check_with_an_unstored_trial_is_unknown(conn):
    assert check_registry_changes(conn, "NCT99999999").status == "unknown"


def test_check_after_a_b_a_compares_b_with_a_in_date_order(conn):
    save(conn, HASH_A, DAY1, "12 months")
    save(conn, HASH_B, DAY2, "24 months")
    save(conn, HASH_A, DAY3, "12 months")
    report = check_registry_changes(conn, NCT)
    assert report.older_captured == DAY2 and report.newer_captured == DAY3     # dates run forward
    change = report.changes[0]
    assert (change.before, change.after) == ("24 months", "12 months")          # B (older) -> A (newer)


def test_check_with_outcomes_never_extracted_is_unknown(conn):
    snapshot = Snapshot(
        nct_id=NCT, title="T", overall_status="COMPLETED", content_hash=HASH_A,
        source_url=f"https://clinicaltrials.gov/study/{NCT}", raw_file_path="data/x.json", retrieved_at=DAY1,
    )
    save_snapshot(conn, snapshot, [])                       # no outcomes passed
    save(conn, HASH_B, DAY2, "24 months")
    report = check_registry_changes(conn, NCT)
    assert report.status == "unknown" and "not extracted" in report.reason

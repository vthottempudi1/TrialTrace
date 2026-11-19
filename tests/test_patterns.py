##############################################################################
# tests/test_patterns.py
#
# PURPOSE: Prove each named pattern returns the right trials, judges a trial
#          by its newest snapshot only, and that a wrong name is an error.
#          Uses the local database; every test is rolled back.
##############################################################################

from datetime import datetime, timezone

import pytest

from trialtrace.checks.patterns import PATTERNS, run_all, run_pattern
from trialtrace.contracts import Snapshot
from trialtrace.db.repository import save_snapshot

WHEN = datetime(2026, 1, 1, tzinfo=timezone.utc)
LATER = datetime(2026, 2, 1, tzinfo=timezone.utc)


def add(conn, nct: str, *, status="COMPLETED", date="2019-05", date_type="ACTUAL",
        sponsor="Acme Pharma", has_results=True, digit="a", when=WHEN) -> None:
    save_snapshot(conn, Snapshot(
        nct_id=nct, title="T", sponsor=sponsor, overall_status=status, has_results=has_results,
        primary_completion_date=date, primary_completion_type=date_type,
        content_hash=digit * 64, source_url=f"https://clinicaltrials.gov/study/{nct}",
        raw_file_path=f"data/raw/clinicaltrials/{nct}/{digit * 64}.json", retrieved_at=when,
    ), [])


def ids(conn, name: str) -> list[str]:
    return [row["nct_id"] for row in run_pattern(conn, name).rows]


def test_completed_with_estimated_date(conn):
    add(conn, "NCT90000001", date_type="ESTIMATED")
    add(conn, "NCT90000002", date_type="ACTUAL")
    add(conn, "NCT90000003", status="RECRUITING", date_type="ESTIMATED")    # not completed
    assert ids(conn, "completed_with_estimated_date") == ["NCT90000001"]


def test_completed_missing_completion_date(conn):
    add(conn, "NCT90000001", date=None, date_type=None)
    add(conn, "NCT90000002", date="2019-05", date_type=None)                # date without its type
    add(conn, "NCT90000003")                                                # complete record
    add(conn, "NCT90000004", status="RECRUITING", date=None, date_type=None)
    assert ids(conn, "completed_missing_completion_date") == ["NCT90000001", "NCT90000002"]


def test_a_trial_is_judged_by_its_newest_snapshot(conn):
    add(conn, "NCT90000001", date_type="ESTIMATED", digit="a", when=WHEN)
    add(conn, "NCT90000001", date_type="ACTUAL", digit="b", when=LATER)     # newer: now ACTUAL
    assert ids(conn, "completed_with_estimated_date") == []


def test_unknown_values_by_field_counts_each_field(conn):
    add(conn, "NCT90000001", sponsor=None, has_results=None)
    add(conn, "NCT90000002", date=None, date_type=None)
    add(conn, "NCT90000003")
    counts = run_pattern(conn, "unknown_values_by_field").rows[0]
    assert counts == {
        "trials": 3, "unknown_sponsor": 1, "unknown_has_results": 1,
        "unknown_completion_date": 1, "unknown_completion_type": 1,
    }


def test_no_matching_trials_gives_an_empty_list_not_an_error(conn):
    assert ids(conn, "completed_with_estimated_date") == []


def test_unknown_pattern_name_is_an_error(conn):
    with pytest.raises(ValueError):
        run_pattern(conn, "no_such_pattern")


def test_run_all_runs_every_pattern_with_the_scope_label(conn):
    results = run_all(conn)
    assert [r.name for r in results] == list(PATTERNS)
    assert all(r.scope == "in the ingested corpus only" for r in results)

##############################################################################
# tests/test_track_record.py
#
# PURPOSE: Prove the per-sponsor counts: one row per sponsor key, each trial
#          counted once, unknown kept apart, non-completed trials excluded.
#          Uses the local database; every test is rolled back.
##############################################################################

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from trialtrace.checks.track_record import SponsorRecord, track_record
from trialtrace.contracts import Snapshot
from trialtrace.db.repository import save_snapshot

WHEN = datetime(2026, 1, 1, tzinfo=timezone.utc)
LATER = datetime(2026, 2, 1, tzinfo=timezone.utc)


def add(conn, nct: str, sponsor: str | None, has_results: bool | None,
        status: str = "COMPLETED", digit: str = "a", when: datetime = WHEN) -> None:
    save_snapshot(conn, Snapshot(
        nct_id=nct, title="T", sponsor=sponsor, overall_status=status, has_results=has_results,
        content_hash=digit * 64, source_url=f"https://clinicaltrials.gov/study/{nct}",
        raw_file_path=f"data/raw/clinicaltrials/{nct}/{digit * 64}.json", retrieved_at=when,
    ), [])


def by_key(conn) -> dict:
    return {r.sponsor_normalized: r for r in track_record(conn)}


def test_counts_per_sponsor(conn):
    add(conn, "NCT90000001", "Acme Pharma", True)
    add(conn, "NCT90000002", "Acme Pharma", False)
    add(conn, "NCT90000003", "Acme Pharma", None)
    row = by_key(conn)["acme pharma"]
    assert (row.completed_trials, row.with_results, row.without_results, row.unknown_results) == (3, 1, 1, 1)


def test_name_variants_are_one_row(conn):
    add(conn, "NCT90000001", "Acme Pharma, Inc.", True)
    add(conn, "NCT90000002", "ACME PHARMA INC", True)
    assert by_key(conn)["acme pharma"].completed_trials == 2


def test_a_trial_with_two_versions_counts_once(conn):
    add(conn, "NCT90000001", "Acme Pharma", False, digit="a", when=WHEN)
    add(conn, "NCT90000001", "Acme Pharma", True, digit="b", when=LATER)     # newer version
    row = by_key(conn)["acme pharma"]
    assert row.completed_trials == 1 and row.with_results == 1 and row.without_results == 0


def test_only_completed_trials_are_counted(conn):
    add(conn, "NCT90000001", "Acme Pharma", True, status="COMPLETED")
    add(conn, "NCT90000002", "Acme Pharma", True, status="RECRUITING")
    assert by_key(conn)["acme pharma"].completed_trials == 1


def test_unknown_sponsor_is_its_own_group(conn):
    add(conn, "NCT90000001", None, True)
    add(conn, "NCT90000002", "Acme Pharma", True)
    rows = by_key(conn)
    assert rows[None].completed_trials == 1 and rows["acme pharma"].completed_trials == 1


def test_every_row_carries_the_scope_label(conn):
    add(conn, "NCT90000001", "Acme Pharma", True)
    assert all(r.scope == "in the ingested corpus only" for r in track_record(conn))


def test_counts_that_do_not_add_up_are_rejected():
    with pytest.raises(ValidationError):
        SponsorRecord(sponsor_normalized="x", example_name="X", completed_trials=3,
                      with_results=1, without_results=1, unknown_results=0)

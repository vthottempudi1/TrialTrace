##############################################################################
# tests/test_results_repository.py
#
# PURPOSE: Prove reported (posted-results) outcomes are saved and read back in
#          order, "never extracted" differs from "none posted", saving twice adds
#          nothing, and each saved version keeps its own. Rolled back after each test.
##############################################################################

from datetime import datetime, timedelta, timezone

from trialtrace.contracts import ResultsExtraction, ResultsOutcome, Snapshot
from trialtrace.db.repository import get_results_outcomes, save_snapshot

NCT = "NCT04644237"
HASH_A, HASH_B = "a" * 64, "b" * 64
DAY1 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def snapshot(content_hash: str, when: datetime = DAY1) -> Snapshot:
    return Snapshot(
        nct_id=NCT, title="Drug X", overall_status="COMPLETED", content_hash=content_hash,
        source_url=f"https://clinicaltrials.gov/study/{NCT}",
        raw_file_path=f"data/raw/clinicaltrials/{NCT}/{content_hash}.json", retrieved_at=when,
    )


def reported(content_hash: str, position: int, title: str, **extra) -> ResultsOutcome:
    return ResultsOutcome(nct_id=NCT, content_hash=content_hash, position=position, measure_text=title, **extra)


def test_reported_outcomes_round_trip_in_order(conn):
    stored = ResultsExtraction(status="ok", outcomes=[
        reported(HASH_A, 0, "Overall survival", result_type="primary", time_frame="24 months",
                 population="Full set", reporting_status="POSTED"),
        reported(HASH_A, 1, "Extra analysis", result_type="post_hoc"),
        reported(HASH_A, 2, "Unknown type"),
    ])
    save_snapshot(conn, snapshot(HASH_A), [], results=stored)
    assert get_results_outcomes(conn, NCT, HASH_A) == stored        # same order; parts not given stay None


def test_never_extracted_is_unknown_not_empty(conn):
    save_snapshot(conn, snapshot(HASH_A), [])
    assert get_results_outcomes(conn, NCT, HASH_A) is None


def test_none_posted_and_empty_list_stay_different(conn):
    save_snapshot(conn, snapshot(HASH_A), [], results=ResultsExtraction(status="absent"))
    save_snapshot(conn, snapshot(HASH_B), [], results=ResultsExtraction(status="ok"))
    assert get_results_outcomes(conn, NCT, HASH_A).status == "absent"
    assert get_results_outcomes(conn, NCT, HASH_B).status == "ok"


def test_saving_twice_does_not_duplicate(conn):
    extraction = ResultsExtraction(status="ok", outcomes=[reported(HASH_A, 0, "OS", result_type="primary")])
    save_snapshot(conn, snapshot(HASH_A), [], results=extraction)
    save_snapshot(conn, snapshot(HASH_A), [], results=extraction)
    assert len(get_results_outcomes(conn, NCT, HASH_A).outcomes) == 1


def test_results_can_be_filled_in_later_but_not_overwritten(conn):
    save_snapshot(conn, snapshot(HASH_A), [])
    first = ResultsExtraction(status="ok", outcomes=[reported(HASH_A, 0, "OS", result_type="primary")])
    save_snapshot(conn, snapshot(HASH_A), [], results=first)                       # fills in the unknown
    save_snapshot(conn, snapshot(HASH_A), [], results=ResultsExtraction(status="absent"))
    assert get_results_outcomes(conn, NCT, HASH_A) == first                         # the stored answer stays


def test_each_version_keeps_its_own_results_through_a_b_a(conn):
    a = ResultsExtraction(status="ok", outcomes=[reported(HASH_A, 0, "Old title", result_type="primary")])
    b = ResultsExtraction(status="ok", outcomes=[reported(HASH_B, 0, "New title", result_type="primary")])
    save_snapshot(conn, snapshot(HASH_A, DAY1), [], results=a)
    save_snapshot(conn, snapshot(HASH_B, DAY1 + timedelta(days=30)), [], results=b)
    save_snapshot(conn, snapshot(HASH_A, DAY1 + timedelta(days=60)), [], results=a)
    assert get_results_outcomes(conn, NCT, HASH_A) == a
    assert get_results_outcomes(conn, NCT, HASH_B) == b


def test_unknown_version_gives_none(conn):
    assert get_results_outcomes(conn, NCT, "f" * 64) is None

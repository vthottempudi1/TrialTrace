##############################################################################
# tests/test_repository.py
#
# PURPOSE: Prove saving is safe to repeat, the latest view picks the right
#          row (including A -> B -> A), and failures are recorded.
#          Uses the local trialtrace database; every test is rolled back,
#          so no data is left behind. Skipped if no database is configured.
##############################################################################

from datetime import datetime, timedelta, timezone


from trialtrace.contracts import Evidence, OutcomeExtraction, RegisteredOutcome, Snapshot
from trialtrace.db.repository import (
    get_evidence, get_latest_snapshot, get_outcomes, recompute_sponsor_keys, save_failure, save_snapshot,
)

NCT = "NCT04644237"
HASH_A, HASH_B = "a" * 64, "b" * 64
DAY1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
DAY2 = DAY1 + timedelta(days=30)
DAY3 = DAY1 + timedelta(days=60)


def snapshot(content_hash: str, when: datetime, title: str = "Drug X") -> Snapshot:
    return Snapshot(
        nct_id=NCT,
        title=title,
        overall_status="COMPLETED",
        content_hash=content_hash,
        source_url=f"https://clinicaltrials.gov/study/{NCT}",
        raw_file_path=f"data/raw/clinicaltrials/{NCT}/{content_hash}.json",
        retrieved_at=when,
    )


def evidence(content_hash: str, when: datetime) -> Evidence:
    return Evidence(
        evidence_id=f"{NCT}:{content_hash[:12]}:hasResults",
        nct_id=NCT,
        content_hash=content_hash,
        source="clinicaltrials",
        source_url=f"https://clinicaltrials.gov/study/{NCT}",
        field_path="hasResults",
        value="true",
        retrieved_at=when,
    )


def test_new_content_is_new_and_same_content_is_not(conn):
    assert save_snapshot(conn, snapshot(HASH_A, DAY1), []) is True
    assert save_snapshot(conn, snapshot(HASH_A, DAY2), []) is False


def test_latest_is_the_newer_version(conn):
    save_snapshot(conn, snapshot(HASH_A, DAY1, title="Old title"), [])
    save_snapshot(conn, snapshot(HASH_B, DAY2, title="New title"), [])
    assert get_latest_snapshot(conn, NCT).title == "New title"


def test_revert_a_b_a_makes_a_latest_again(conn):
    save_snapshot(conn, snapshot(HASH_A, DAY1, title="Version A"), [])
    save_snapshot(conn, snapshot(HASH_B, DAY2, title="Version B"), [])
    save_snapshot(conn, snapshot(HASH_A, DAY3, title="Version A"), [])   # A seen again
    assert get_latest_snapshot(conn, NCT).content_hash == HASH_A


def test_a_b_a_keeps_all_three_sightings_in_order(conn):
    save_snapshot(conn, snapshot(HASH_A, DAY1), [])
    save_snapshot(conn, snapshot(HASH_B, DAY2), [])
    save_snapshot(conn, snapshot(HASH_A, DAY3), [])
    rows = conn.execute(
        "SELECT content_hash FROM observations WHERE nct_id = %s ORDER BY observed_at", (NCT,)
    ).fetchall()
    assert [row[0] for row in rows] == [HASH_A, HASH_B, HASH_A]


def test_snapshot_keeps_its_first_capture_time(conn):
    save_snapshot(conn, snapshot(HASH_A, DAY1), [])
    save_snapshot(conn, snapshot(HASH_A, DAY3), [])               # same content seen again later
    captured = conn.execute(
        "SELECT retrieved_at FROM snapshots WHERE nct_id = %s AND content_hash = %s", (NCT, HASH_A)
    ).fetchone()[0]
    assert captured == DAY1


def test_saving_the_same_fetch_twice_adds_one_observation(conn):
    save_snapshot(conn, snapshot(HASH_A, DAY1), [])
    save_snapshot(conn, snapshot(HASH_A, DAY1), [])
    count = conn.execute("SELECT count(*) FROM observations WHERE nct_id = %s", (NCT,)).fetchone()[0]
    assert count == 1


def test_registry_last_update_is_stored_and_read_back(conn):
    one = snapshot(HASH_A, DAY1).model_copy(update={"registry_last_update": "2025-11-21"})
    save_snapshot(conn, one, [])
    assert get_latest_snapshot(conn, NCT).registry_last_update == "2025-11-21"


def outcome(content_hash: str, role: str, position: int, title: str, **extra) -> RegisteredOutcome:
    return RegisteredOutcome(nct_id=NCT, content_hash=content_hash, role=role, position=position, measure_text=title, **extra)


def test_outcomes_round_trip_in_registry_order(conn):
    stored = OutcomeExtraction(status="ok", outcomes=[
        outcome(HASH_A, "primary", 0, "Overall survival", description="Time to death", time_frame="24 months"),
        outcome(HASH_A, "secondary", 0, "Response rate"),
        outcome(HASH_A, "secondary", 1, "Safety"),
    ])
    save_snapshot(conn, snapshot(HASH_A, DAY1), [], stored)
    assert get_outcomes(conn, NCT, HASH_A) == stored        # same order, parts not given stay None


def test_outcomes_come_back_primary_then_secondary_then_other(conn):
    # Saved in scrambled order on purpose: the order must come from the role, not from saving
    scrambled = OutcomeExtraction(status="ok", outcomes=[
        outcome(HASH_A, "other", 0, "Quality of life"),
        outcome(HASH_A, "secondary", 1, "Safety"),
        outcome(HASH_A, "primary", 0, "Overall survival"),
        outcome(HASH_A, "secondary", 0, "Response rate"),
    ])
    save_snapshot(conn, snapshot(HASH_A, DAY1), [], scrambled)
    order = [(o.role, o.position) for o in get_outcomes(conn, NCT, HASH_A).outcomes]
    assert order == [("primary", 0), ("secondary", 0), ("secondary", 1), ("other", 0)]


def test_never_extracted_is_unknown_not_empty(conn):
    save_snapshot(conn, snapshot(HASH_A, DAY1), [])          # outcomes not passed
    assert get_outcomes(conn, NCT, HASH_A) is None


def test_no_section_and_empty_section_stay_different(conn):
    save_snapshot(conn, snapshot(HASH_A, DAY1), [], OutcomeExtraction(status="absent"))
    save_snapshot(conn, snapshot(HASH_B, DAY2), [], OutcomeExtraction(status="ok"))
    assert get_outcomes(conn, NCT, HASH_A).status == "absent"
    assert get_outcomes(conn, NCT, HASH_B).status == "ok"


def test_saving_outcomes_twice_does_not_duplicate_them(conn):
    stored = OutcomeExtraction(status="ok", outcomes=[outcome(HASH_A, "primary", 0, "Overall survival")])
    save_snapshot(conn, snapshot(HASH_A, DAY1), [], stored)
    save_snapshot(conn, snapshot(HASH_A, DAY3), [], stored)
    assert len(get_outcomes(conn, NCT, HASH_A).outcomes) == 1


def test_outcomes_can_be_filled_in_later_but_not_overwritten(conn):
    first = OutcomeExtraction(status="ok", outcomes=[outcome(HASH_A, "primary", 0, "Overall survival")])
    save_snapshot(conn, snapshot(HASH_A, DAY1), [])                  # extraction had failed
    save_snapshot(conn, snapshot(HASH_A, DAY2), [], first)           # retry worked: filled in
    save_snapshot(conn, snapshot(HASH_A, DAY3), [], OutcomeExtraction(status="absent"))   # must not overwrite
    assert get_outcomes(conn, NCT, HASH_A) == first


def test_each_version_keeps_its_own_outcomes_through_a_b_a(conn):
    a = OutcomeExtraction(status="ok", outcomes=[outcome(HASH_A, "primary", 0, "Overall survival")])
    b = OutcomeExtraction(status="ok", outcomes=[outcome(HASH_B, "primary", 0, "Progression-free survival")])
    save_snapshot(conn, snapshot(HASH_A, DAY1), [], a)
    save_snapshot(conn, snapshot(HASH_B, DAY2), [], b)
    save_snapshot(conn, snapshot(HASH_A, DAY3), [], a)
    assert get_outcomes(conn, NCT, HASH_A) == a
    assert get_outcomes(conn, NCT, HASH_B) == b


def test_unknown_trial_gives_none(conn):
    assert get_latest_snapshot(conn, "NCT99999999") is None


def test_unknown_stays_unknown_after_a_round_trip(conn):
    save_snapshot(conn, snapshot(HASH_A, DAY1), [])
    latest = get_latest_snapshot(conn, NCT)
    assert latest.has_results is None and latest.sponsor is None


def test_registry_evidence_always_has_its_snapshot(conn):
    save_snapshot(conn, snapshot(HASH_A, DAY1), [evidence(HASH_A, DAY1)])
    orphans = conn.execute(
        "SELECT count(*) FROM evidence e WHERE e.source = 'clinicaltrials' AND NOT EXISTS "
        "(SELECT 1 FROM snapshots s WHERE s.nct_id = e.nct_id AND s.content_hash = e.content_hash)"
    ).fetchone()[0]
    assert orphans == 0


def test_sponsor_variants_get_the_same_key(conn):
    one = snapshot(HASH_A, DAY1).model_copy(update={"sponsor": "Acme Pharma, Inc."})
    save_snapshot(conn, one, [])
    key = conn.execute("SELECT sponsor_normalized FROM latest_snapshots WHERE nct_id = %s", (NCT,)).fetchone()[0]
    assert key == "acme pharma"


def test_get_evidence_returns_only_the_requested_version(conn):
    save_snapshot(conn, snapshot(HASH_A, DAY1), [evidence(HASH_A, DAY1)])
    save_snapshot(conn, snapshot(HASH_B, DAY2), [evidence(HASH_B, DAY2)])
    rows = get_evidence(conn, NCT, HASH_A)
    assert [row.content_hash for row in rows] == [HASH_A]
    assert get_evidence(conn, NCT, "c" * 64) == []


def test_recompute_sponsor_keys_fixes_only_stale_keys(conn):
    one = snapshot(HASH_A, DAY1).model_copy(update={"sponsor": "Eli Lilly and Company"})
    save_snapshot(conn, one, [])
    conn.execute("UPDATE snapshots SET sponsor_normalized = 'eli lilly and' WHERE nct_id = %s", (NCT,))   # an old rule's key
    assert recompute_sponsor_keys(conn) == 1
    key = conn.execute("SELECT sponsor_normalized FROM snapshots WHERE nct_id = %s", (NCT,)).fetchone()[0]
    assert key == "eli lilly"
    assert recompute_sponsor_keys(conn) == 0                      # nothing left to fix


def test_unknown_sponsor_has_no_key(conn):
    save_snapshot(conn, snapshot(HASH_A, DAY1), [])      # sponsor is None
    key = conn.execute("SELECT sponsor_normalized FROM latest_snapshots WHERE nct_id = %s", (NCT,)).fetchone()[0]
    assert key is None


def test_failure_is_recorded(conn):
    save_failure(conn, NCT, "fetch", "timeout", 3)
    row = conn.execute(
        "SELECT step, error_code, attempts FROM ingest_failures WHERE nct_id = %s", (NCT,)
    ).fetchone()
    assert row == ("fetch", "timeout", 3)

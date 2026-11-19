##############################################################################
# tests/test_safety_repository.py
#
# PURPOSE: Prove the adverse-event module is saved and read back in order, "never extracted" differs from "absent"
#          and from "empty", missing numbers stay NULL, duplicate rows are kept, saving twice adds nothing,
#          later fill-in never overwrites, each version keeps its own, and the backfill reads saved files
#          without the network. Rolled back after each test.
##############################################################################

import json
from datetime import datetime, timezone

from trialtrace.safety.backfill_safety import backfill_safety
from trialtrace.contracts import (
    SafetyArm, SafetyEvent, SafetyEventCount, SafetyExtraction, Snapshot,
)
from trialtrace.db.repository import get_safety, save_snapshot

NCT = "NCT04644237"
HASH_A, HASH_B = "a" * 64, "b" * 64
DAY1 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def snapshot(content_hash: str, raw_file_path: str | None = None) -> Snapshot:
    return Snapshot(
        nct_id=NCT, title="Drug X", overall_status="COMPLETED", content_hash=content_hash,
        source_url=f"https://clinicaltrials.gov/study/{NCT}",
        raw_file_path=raw_file_path or f"data/raw/clinicaltrials/{NCT}/{content_hash}.json", retrieved_at=DAY1,
    )


def arm(content_hash: str, position: int, **numbers) -> SafetyArm:
    return SafetyArm(nct_id=NCT, content_hash=content_hash, position=position, group_id=f"EG{position:03d}",
                     title=f"Arm {position}", **numbers)


def event(content_hash: str, kind: str, position: int, term, counts=()) -> SafetyEvent:
    return SafetyEvent(nct_id=NCT, content_hash=content_hash, kind=kind, position=position, term=term, counts=list(counts))


def module(content_hash: str) -> SafetyExtraction:
    return SafetyExtraction(
        status="ok", frequency_threshold=5.0, time_frame="up to 47 days", description="TEAE",
        arms=[arm(content_hash, 0, deaths_affected=52, deaths_at_risk=102, serious_affected=0, serious_at_risk=101),
              arm(content_hash, 1, looks_pooled=False)],
        events=[
            event(content_hash, "serious", 0, "Anaemia", [SafetyEventCount(group_id="EG000", affected=2, at_risk=101, events=3),
                                                        SafetyEventCount(group_id="EG001", affected=0)]),
            event(content_hash, "serious", 1, None),
            event(content_hash, "other", 0, "Nausea", [SafetyEventCount(group_id="EG000", affected=7)]),
            event(content_hash, "other", 1, "Nausea", [SafetyEventCount(group_id="EG000", affected=1)]),   # duplicate term
        ],
    )


def test_a_module_round_trips_in_order(conn):
    save_snapshot(conn, snapshot(HASH_A), [], safety=module(HASH_A))
    assert get_safety(conn, NCT, HASH_A) == module(HASH_A)


def test_missing_numbers_stay_none_and_zero_stays_zero(conn):
    save_snapshot(conn, snapshot(HASH_A), [], safety=module(HASH_A))
    stored = get_safety(conn, NCT, HASH_A)
    assert stored.arms[0].serious_affected == 0 and stored.arms[1].deaths_affected is None
    assert stored.events[0].counts[1].at_risk is None and stored.events[1].term is None


def test_duplicate_terms_are_both_kept(conn):
    save_snapshot(conn, snapshot(HASH_A), [], safety=module(HASH_A))
    others = [(e.position, e.counts[0].affected) for e in get_safety(conn, NCT, HASH_A).events if e.kind == "other"]
    assert others == [(0, 7), (1, 1)]


def test_never_extracted_absent_and_empty_are_three_different_answers(conn):
    save_snapshot(conn, snapshot(HASH_A), [])
    assert get_safety(conn, NCT, HASH_A) is None                                    # never extracted = unknown
    save_snapshot(conn, snapshot(HASH_A), [], safety=SafetyExtraction(status="absent"))
    assert get_safety(conn, NCT, HASH_A).status == "absent"
    save_snapshot(conn, snapshot(HASH_B), [], safety=SafetyExtraction(status="ok"))
    empty = get_safety(conn, NCT, HASH_B)
    assert empty.status == "ok" and empty.arms == [] and empty.events == []


def test_saving_twice_does_not_duplicate(conn):
    save_snapshot(conn, snapshot(HASH_A), [], safety=module(HASH_A))
    save_snapshot(conn, snapshot(HASH_A), [], safety=module(HASH_A))
    assert get_safety(conn, NCT, HASH_A) == module(HASH_A)


def test_safety_can_be_filled_in_later_but_not_overwritten(conn):
    save_snapshot(conn, snapshot(HASH_A), [])
    save_snapshot(conn, snapshot(HASH_A), [], safety=module(HASH_A))
    assert get_safety(conn, NCT, HASH_A).status == "ok"
    save_snapshot(conn, snapshot(HASH_A), [], safety=SafetyExtraction(status="absent"))
    assert get_safety(conn, NCT, HASH_A).status == "ok"                              # the first answer stays


def test_each_version_keeps_its_own_module(conn):
    other = SafetyExtraction(status="ok", arms=[arm(HASH_B, 0, deaths_affected=1)])
    save_snapshot(conn, snapshot(HASH_A), [], safety=module(HASH_A))
    save_snapshot(conn, snapshot(HASH_B), [], safety=other)
    assert len(get_safety(conn, NCT, HASH_A).arms) == 2 and len(get_safety(conn, NCT, HASH_B).arms) == 1


def test_unknown_version_gives_none(conn):
    assert get_safety(conn, NCT, HASH_A) is None


# --- backfill from saved raw files (no network) -------------------------------

def record(with_module: bool) -> bytes:
    data = {"protocolSection": {"identificationModule": {"nctId": NCT}}}
    if with_module:
        data["resultsSection"] = {"adverseEventsModule": {
            "frequencyThreshold": "5",
            "eventGroups": [{"id": "EG000", "title": "Drug A", "seriousNumAffected": 3, "seriousNumAtRisk": 50}],
            "seriousEvents": [{"term": "Anaemia", "stats": [{"groupId": "EG000", "numAffected": 3, "numAtRisk": 50}]}]}}
    return json.dumps(data).encode()


def test_backfill_fills_unknown_snapshots_from_saved_files(conn, tmp_path):
    with_path, without_path, bad_path = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "c.json"
    with_path.write_bytes(record(True))
    without_path.write_bytes(record(False))
    bad_path.write_bytes(b"not json")
    hashes = ["1" * 64, "2" * 64, "3" * 64, "4" * 64]
    save_snapshot(conn, snapshot(hashes[0], str(with_path)), [])
    save_snapshot(conn, snapshot(hashes[1], str(without_path)), [])
    save_snapshot(conn, snapshot(hashes[2], str(bad_path)), [])
    save_snapshot(conn, snapshot(hashes[3], str(tmp_path / "gone.json")), [])

    result = backfill_safety(conn)

    assert (result.filled, result.absent, result.parse_error, result.missing_file) == (1, 1, 1, 1)
    assert get_safety(conn, NCT, hashes[0]).events[0].term == "Anaemia"
    assert get_safety(conn, NCT, hashes[1]).status == "absent"
    assert get_safety(conn, NCT, hashes[2]) is None and get_safety(conn, NCT, hashes[3]) is None   # stay unknown


def test_backfill_twice_changes_nothing_more(conn, tmp_path):
    path = tmp_path / "a.json"
    path.write_bytes(record(True))
    save_snapshot(conn, snapshot("1" * 64, str(path)), [])
    backfill_safety(conn)
    second = backfill_safety(conn)
    assert (second.filled, second.absent) == (0, 0)

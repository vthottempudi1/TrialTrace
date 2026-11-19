##############################################################################
# tests/test_safety_registry.py
#
# PURPOSE: Prove the registry-only safety finding (step S4) states the posted numbers with evidence,
#          keeps "not given" apart from 0, labels pooled groups, names the most frequent serious events,
#          and answers "unknown" when there is no data or no stored evidence.
#          The last tests use the test database (rolled back).
##############################################################################

from datetime import datetime, timezone

from trialtrace.safety.backfill_safety import backfill_safety_evidence
from trialtrace.safety.safety_registry import (
    check_safety_registry, cited_values, safety_evidence, top_serious_events,
)
from trialtrace.contracts import SafetyArm, SafetyEvent, SafetyEventCount, SafetyExtraction, Snapshot
from trialtrace.db.repository import get_evidence, get_safety, save_snapshot
from trialtrace.report import build_report
from trialtrace.review.review import PLAIN_FACTS

NCT = "NCT04644237"
HASH = "a" * 64
WHEN = datetime(2026, 1, 1, tzinfo=timezone.utc)
BASE = "resultsSection.adverseEventsModule."


def snapshot() -> Snapshot:
    return Snapshot(
        nct_id=NCT, title="Drug X", overall_status="COMPLETED", content_hash=HASH,
        source_url=f"https://clinicaltrials.gov/study/{NCT}",
        raw_file_path=f"data/raw/clinicaltrials/{NCT}/{HASH}.json", retrieved_at=WHEN,
    )


def arm(position: int, title: str, **numbers) -> SafetyArm:
    return SafetyArm(nct_id=NCT, content_hash=HASH, position=position, group_id=f"EG{position:03d}", title=title, **numbers)


def serious(position: int, term, *affected_per_group) -> SafetyEvent:
    """A serious event row; affected_per_group[i] is the count for group i (at risk is always 100)."""
    counts = [SafetyEventCount(group_id=f"EG{i:03d}", affected=n, at_risk=100) for i, n in enumerate(affected_per_group)]
    return SafetyEvent(nct_id=NCT, content_hash=HASH, kind="serious", position=position, term=term, counts=counts)


def module() -> SafetyExtraction:
    return SafetyExtraction(
        status="ok",
        arms=[arm(0, "Drug A", deaths_affected=52, deaths_at_risk=102, serious_affected=42, serious_at_risk=101),
              arm(1, "Drug B", serious_affected=0, serious_at_risk=50)],          # deaths not given
        events=[serious(0, "Anaemia", 2, 0), serious(1, "Pneumonia", 9, 1), serious(2, "Sepsis", 5, 0),
                serious(3, "Fever", 5, 0), serious(4, "Rash", 1, 0), serious(5, None, 50, 50)],
    )


def finding_for(safety):
    return check_safety_registry(NCT, snapshot(), safety, safety_evidence(safety, WHEN))


def test_the_claim_states_deaths_and_serious_counts_per_group():
    finding = finding_for(module())
    assert finding.category == "safety_registry_summary" and finding.check == "safety_registry"
    assert '52 of 102 in "Drug A"' in finding.claim
    assert '42 of 101 in "Drug A"' in finding.claim and '0 of 50 in "Drug B"' in finding.claim


def test_a_number_not_given_is_said_so_and_never_shown_as_zero():
    finding = finding_for(module())
    assert 'not given in "Drug B"' in finding.claim
    assert "A number the record does not give is unknown, not zero." in finding.limitations


def test_top_serious_events_are_the_three_largest_with_ties_in_registry_order():
    top = top_serious_events(module(), "EG000")
    assert [event.term for event, index, count in top] == ["Pneumonia", "Sepsis", "Fever"]


def test_events_with_no_term_or_a_zero_count_are_not_named():
    top = top_serious_events(module(), "EG001")
    assert [event.term for event, index, count in top] == ["Pneumonia"]         # the unnamed row (50) and the zeros are left out
    assert '"Drug B": Pneumonia (1 of 100)' in finding_for(module()).claim


def test_a_group_with_no_serious_event_above_zero_says_none_listed():
    safety = SafetyExtraction(status="ok", arms=[arm(0, "Drug A", serious_affected=0, serious_at_risk=10)])
    assert '"Drug A": none listed with a count above 0' in finding_for(safety).claim


def test_a_pooled_group_is_labelled_and_never_added_up():
    safety = SafetyExtraction(status="ok", arms=[arm(0, "Drug A", deaths_affected=1, deaths_at_risk=10),
                                                 arm(1, "Total", deaths_affected=1, deaths_at_risk=20, looks_pooled=True)])
    finding = finding_for(safety)
    assert '"Total" (looks pooled)' in finding.claim
    assert any("pooled" in text for text in finding.limitations)


def test_every_cited_value_has_an_evidence_row_at_its_path_in_the_record():
    evidence = safety_evidence(module(), WHEN)
    by_path = {item.field_path: item.value for item in evidence}
    assert by_path[BASE + "eventGroups.0.title"] == "Drug A"
    assert by_path[BASE + "eventGroups.0.deathsNumAffected"] == "52"
    assert by_path[BASE + "eventGroups.1.seriousNumAffected"] == "0"                 # a real 0 is evidence
    assert BASE + "eventGroups.1.deathsNumAffected" not in by_path                    # not given: no evidence
    assert by_path[BASE + "seriousEvents.1.term"] == "Pneumonia"
    assert by_path[BASE + "seriousEvents.1.stats.1.numAffected"] == "1"
    assert BASE + "seriousEvents.4.term" not in by_path                               # Rash is not in any top three
    finding = finding_for(module())
    assert finding.evidence_ids == [item.evidence_id for item in evidence]
    assert len(evidence) == len(cited_values(module())) == len(set(by_path))        # an event in two top lists is cited once


def test_no_stored_evidence_gives_unknown_not_a_claim():
    finding = check_safety_registry(NCT, snapshot(), module(), [])
    assert finding.category == "unknown" and "no stored evidence" in finding.limitations[0]


def test_not_extracted_absent_and_no_groups_are_unknown_with_their_own_reason():
    never = check_safety_registry(NCT, snapshot(), None, [])
    absent = check_safety_registry(NCT, snapshot(), SafetyExtraction(status="absent"), [])
    empty = check_safety_registry(NCT, snapshot(), SafetyExtraction(status="ok"), [])
    assert [f.category for f in (never, absent, empty)] == ["unknown"] * 3
    assert "not extracted" in never.limitations[0]
    assert "does not mean the trial had no side effects" in absent.limitations[0]
    assert "no treatment groups" in empty.limitations[0]
    assert safety_evidence(None, WHEN) == [] and safety_evidence(SafetyExtraction(status="absent"), WHEN) == []


def test_the_summary_is_a_plain_fact_for_review():
    assert "safety_registry_summary" in PLAIN_FACTS


# --- with the database -------------------------------------------------------

def test_the_report_shows_the_safety_finding_with_stored_evidence(conn):
    save_snapshot(conn, snapshot(), safety_evidence(module(), WHEN), safety=module())
    report = build_report(conn, WHEN.date(), "run-test")
    finding = [f for f in report.trials[0].findings if f.check == "safety_registry"][0]
    assert finding.category == "safety_registry_summary"
    stored_ids = {item.evidence_id for item in get_evidence(conn, NCT, HASH)}
    assert set(finding.evidence_ids) <= stored_ids


def test_backfill_adds_the_evidence_once(conn):
    save_snapshot(conn, snapshot(), [], safety=module())                             # stored before S4: no safety evidence
    assert check_safety_registry(NCT, snapshot(), get_safety(conn, NCT, HASH), get_evidence(conn, NCT, HASH)).category == "unknown"
    added = backfill_safety_evidence(conn)
    assert added == len(cited_values(module()))
    assert backfill_safety_evidence(conn) == 0                                       # a second run adds nothing
    finding = check_safety_registry(NCT, snapshot(), get_safety(conn, NCT, HASH), get_evidence(conn, NCT, HASH))
    assert finding.category == "safety_registry_summary"

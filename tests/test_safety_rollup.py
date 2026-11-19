##############################################################################
# tests/test_safety_rollup.py
#
# PURPOSE: Prove the safety roll-up (step S8): one finding per safety quantity across the papers read,
#          the category that most needs a person wins, a quantity only the baseline found is
#          "readers differ", a registry count no reader found is "not in abstract" only when every
#          listed paper was read (otherwise unknown), every claim cites evidence, and the review
#          queue puts differences first and agreement last.
#          No database: the stored rows are written by hand.
##############################################################################

from datetime import datetime, timezone

from trialtrace.safety.safety_rollup import (
    build_trial_safety, quantity_name, registry_evidence, rollup_category, summary_findings,
)
from trialtrace.contracts import SafetyArm, SafetyEvent, SafetyEventCount, SafetyExtraction, Snapshot, StoredFinding
from trialtrace.review.review import VERDICT_WORDS, needs_review, review_priority

NCT = "NCT04644237"
HASH = "a" * 64
PAPER_HASH = "b" * 64


def registry():
    """Two groups: Drug X (6 deaths, 42 serious, of 101) and Placebo (4 deaths, 26 serious, of 50); nausea 30 of 101."""
    arms = []
    for position, (title, deaths, serious, at_risk) in enumerate([("Drug X", 6, 42, 101), ("Placebo", 4, 26, 50)]):
        arms.append(SafetyArm(
            nct_id=NCT, content_hash=HASH, position=position, group_id=f"EG00{position}", title=title,
            deaths_affected=deaths, deaths_at_risk=at_risk, serious_affected=serious, serious_at_risk=at_risk,
        ))
    nausea = SafetyEvent(nct_id=NCT, content_hash=HASH, kind="other", position=0, term="Nausea",
                         counts=[SafetyEventCount(group_id="EG000", affected=30, at_risk=101)])
    return SafetyExtraction(status="ok", frequency_threshold=5.0, arms=arms, events=[nausea])


def model_row(kind="serious", arm="Drug X", affected=None, denominator=None, start=0, **fields):
    row = {
        "kind": kind, "term": None, "arm": arm, "arm_in_quote": True, "affected": affected, "denominator": denominator,
        "percent": None, "grade": None, "treatment_related": False, "cause": None, "all_cause": False, "negated": False,
        "sentence": 3, "quote_text": "a sentence", "quote_start": start, "quote_end": start + 10, "rejected_code": None,
    }
    row.update(fields)
    return row


def baseline_row(kind="death", term=None, numbers=("6",), start=50):
    return {"kind": kind, "term": term, "numbers": list(numbers), "negated": False, "quote_start": start, "quote_end": start + 20}


def reading(system, statements, pmid="111", status="ok"):
    return {"safety_run_id": "safety-test", "pmid": pmid, "paper_raw_hash": PAPER_HASH, "system": system, "status": status,
            "model": "gpt-test" if system == "llm" else None, "prompt_hash": "abc" if system == "llm" else None,
            "statements": statements}


def findings_for(readings, listed=("111",)):
    data = build_trial_safety(NCT, HASH, registry(), readings, list(listed))
    return {finding.check: finding for finding in summary_findings(data)}


def test_quantity_names():
    assert quantity_name("death", None) == "death"
    assert quantity_name("event", "Anemia") == "anaemia"                    # one name for both spellings
    assert quantity_name("event", "Neutrophil count decreased") == "neutropenia"
    assert quantity_name("event", "Hiccups") == "hiccups"


def test_the_category_that_most_needs_a_person_wins():
    assert rollup_category(["safety_consistent", "safety_number_differs", "safety_not_comparable"]) == "safety_number_differs"
    assert rollup_category(["safety_consistent", "safety_not_comparable"]) == "safety_not_comparable"
    assert rollup_category(["safety_consistent", "safety_unclear"]) == "safety_unclear"
    assert rollup_category(["safety_consistent"]) == "safety_consistent"
    assert rollup_category(["unknown"]) == "unknown"


def test_one_finding_per_quantity_with_evidence():
    rows = [
        model_row(affected=42, denominator=101, start=0),                                  # serious, Drug X: agrees
        model_row(affected=26, denominator=50, arm="placebo", start=0),                    # serious, Placebo: agrees
        model_row(kind="death", affected=6, denominator=101, start=20),                    # deaths: agrees
        model_row(kind="event", term="nausea", affected=35, denominator=101, start=40),    # nausea: 35 against 30
    ]
    found = findings_for([reading("llm", rows), reading("baseline", [])])
    assert found["safety_summary:serious"].category == "safety_consistent"
    assert found["safety_summary:death"].category == "safety_consistent"
    assert found["safety_summary:nausea"].category == "safety_number_differs"
    assert len(found) == 3

    serious = found["safety_summary:serious"]
    assert f"{NCT}:{PAPER_HASH[:12]}:pmid111:abstract[0:10]" in serious.evidence_ids                # the quote
    assert f"{NCT}:{HASH[:12]}:resultsSection.adverseEventsModule.eventGroups.0.seriousNumAffected" in serious.evidence_ids
    assert serious.evidence_completeness == "complete"
    assert serious.source_run_id == "safety-test"


def test_one_difference_among_two_papers_decides_the_finding():
    readings = [
        reading("llm", [model_row(affected=42, denominator=101)], pmid="111"),
        reading("llm", [model_row(affected=40, denominator=101)], pmid="222"),
    ]
    found = findings_for(readings, listed=("111", "222"))
    serious = found["safety_summary:serious"]
    assert serious.category == "safety_number_differs"
    assert "111, 222" in serious.claim


def test_rejected_statements_are_not_used():
    rows = [model_row(affected=40, denominator=101, rejected_code="count_not_in_quote")]
    found = findings_for([reading("llm", rows), reading("baseline", [])])
    assert found["safety_summary:serious"].category == "unknown"
    assert any("111" in note for note in found["safety_summary:serious"].limitations)


def test_a_quantity_only_the_baseline_found_is_readers_differ():
    found = findings_for([reading("llm", []), reading("baseline", [baseline_row(kind="death")])])
    death = found["safety_summary:death"]
    assert death.category == "safety_readers_differ"
    assert death.evidence_ids == [f"{NCT}:{PAPER_HASH[:12]}:pmid111:abstract[50:70]"]


def test_another_name_for_an_event_in_the_same_sentence_is_not_a_difference():
    model = [model_row(kind="event", term="infection/fever", affected=5, denominator=101, grade="grade 2", start=40)]
    same_sentence = [baseline_row(kind="event", term="infection", start=45)]
    other_sentence = [baseline_row(kind="event", term="infection", start=200)]
    assert "safety_summary:infection" not in findings_for([reading("llm", model), reading("baseline", same_sentence)])
    found = findings_for([reading("llm", model), reading("baseline", other_sentence)])
    assert found["safety_summary:infection"].category == "safety_readers_differ"


def test_a_baseline_statement_without_a_number_does_not_count():
    found = findings_for([reading("llm", []), reading("baseline", [baseline_row(kind="death", numbers=())])])
    assert found["safety_summary:death"].category == "safety_registry_not_in_abstract"


def test_not_in_abstract_needs_every_listed_paper_read():
    readings = [reading("llm", []), reading("baseline", [])]
    all_read = findings_for(readings, listed=("111",))
    one_unread = findings_for(readings, listed=("111", "222"))
    assert all_read["safety_summary:death"].category == "safety_registry_not_in_abstract"
    assert one_unread["safety_summary:death"].category == "unknown"
    assert one_unread["safety_summary:death"].evidence_completeness == "incomplete"


def test_a_failed_reading_is_unknown_not_nothing_stated():
    found = findings_for([reading("llm", [], status="failed"), reading("baseline", [])])
    assert found["safety_summary:death"].category == "unknown"
    assert found["safety_summary:serious"].category == "unknown"


def test_the_model_only_note_is_added():
    rows = [model_row(affected=42, denominator=101)]
    found = findings_for([reading("llm", rows), reading("baseline", [])])
    assert any("only the model did" in line for line in found["safety_summary:serious"].limitations)


def test_registry_evidence_holds_each_value_once():
    rows = [model_row(affected=42, denominator=101), model_row(affected=42, denominator=101, start=30)]
    data = build_trial_safety(NCT, HASH, registry(), [reading("llm", rows)], ["111"])
    snapshot = Snapshot.model_construct(retrieved_at=datetime(2026, 11, 11, tzinfo=timezone.utc))
    items = registry_evidence(snapshot, data)
    values = {item.field_path.split(".")[-1]: item.value for item in items}
    assert values == {"title": "Drug X", "seriousNumAffected": "42", "seriousNumAtRisk": "101"}


def test_review_queue_order_and_wording():
    rows = [
        model_row(affected=42, denominator=101),                                           # consistent
        model_row(kind="event", term="nausea", affected=35, denominator=101, start=40),    # differs
    ]
    found = findings_for([reading("llm", rows), reading("baseline", [])])

    def stored(finding):
        return StoredFinding(finding_id="f1", run_id="run-test", finding=finding)

    differs = stored(found["safety_summary:nausea"])
    consistent = stored(found["safety_summary:serious"])
    left_out = stored(found["safety_summary:death"])
    assert review_priority(differs) == 0 and needs_review(differs)
    assert review_priority(consistent) == 2 and not needs_review(consistent)        # shown only with --all
    assert review_priority(left_out) == 2 and not needs_review(left_out)
    for finding in found.values():
        assert not VERDICT_WORDS.search(finding.claim + " ".join(finding.limitations))


def test_consistent_but_not_every_paper_read_goes_to_review():
    rows = [model_row(affected=42, denominator=101)]
    found = findings_for([reading("llm", rows), reading("baseline", [])], listed=("111", "222"))
    stored = StoredFinding(finding_id="f1", run_id="run-test", finding=found["safety_summary:serious"])
    assert found["safety_summary:serious"].category == "safety_consistent"
    assert review_priority(stored) == 1 and needs_review(stored)


def test_strict_reporting_routes_unverified_numeric_matches_to_review():
    from trialtrace.safety.safety_assist import comparison_for_report
    data = build_trial_safety(NCT, HASH, registry(), [reading("llm", [model_row(affected=42, denominator=101)])],
                              ["111"], comparison_fn=lambda statement, safety, row: comparison_for_report(statement, safety))
    finding = {item.check: item for item in summary_findings(data)}["safety_summary:serious"]
    stored = StoredFinding(finding_id="strict", run_id="test", finding=finding)
    assert finding.category == "safety_unclear" and needs_review(stored)
    assert not any("only the model did" in note for note in finding.limitations)


def test_unknown_safety_work_is_in_the_default_review_queue():
    from trialtrace.contracts import Finding
    item = StoredFinding(finding_id="missing", run_id="test", finding=Finding(
        nct_id=NCT, check="safety_summary", category="unknown", claim="Safety has not been checked."))
    assert needs_review(item) and review_priority(item) == 0

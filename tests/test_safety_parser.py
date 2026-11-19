##############################################################################
# tests/test_safety_parser.py
#
# PURPOSE: Prove the adverse-event module is read with every number nullable (never 0), duplicate rows kept,
#          threshold and assessment type kept, pooled-looking groups flagged, "no module" reported as absent,
#          and bad entries reported, not dropped.
##############################################################################

import json
from datetime import datetime, timezone

import pytest

from trialtrace.parse.registry_parser import ParseError
from trialtrace.safety.safety_parser import parse_safety, safety_path
from trialtrace.sources.raw_store import StoredRaw

NCT = "NCT04644237"
STORED = StoredRaw(
    nct_id=NCT, url=f"https://clinicaltrials.gov/api/v2/studies/{NCT}",
    retrieved_at=datetime.now(timezone.utc), content_hash="a" * 64,
    raw_file_path=f"data/raw/clinicaltrials/{NCT}/{'a' * 64}.json",
)


def record(module="none", nct=NCT) -> bytes:
    data = {"protocolSection": {"identificationModule": {"nctId": nct}}}
    if module != "none":
        data["resultsSection"] = {"adverseEventsModule": module}
    return json.dumps(data).encode()


def group(group_id="EG000", title="Drug A", **numbers):
    return {"id": group_id, "title": title, **numbers}


def event(term="Anaemia", stats=None, **fields):
    return {"term": term, "stats": stats if stats is not None else [], **fields}


def test_arms_and_events_are_read_with_their_numbers():
    module = {
        "frequencyThreshold": "5", "timeFrame": "up to 47 days", "description": "TEAE definition",
        "eventGroups": [group(title="Drug A", description="5.4 mg/kg", deathsNumAffected=52, deathsNumAtRisk=102,
                              seriousNumAffected=42, seriousNumAtRisk=101, otherNumAffected=101, otherNumAtRisk=101)],
        "seriousEvents": [event("Thrombocytopenia", organSystem="Blood disorders", sourceVocabulary="MedDRA25.1",
                                assessmentType="SYSTEMATIC_ASSESSMENT",
                                stats=[{"groupId": "EG000", "numEvents": 1, "numAffected": 1, "numAtRisk": 101}])],
    }
    result = parse_safety(record(module), STORED)
    [arm] = result.arms
    assert result.status == "ok" and result.frequency_threshold == 5.0
    assert result.time_frame == "up to 47 days" and result.description == "TEAE definition"
    assert (arm.nct_id, arm.content_hash, arm.position, arm.group_id) == (NCT, STORED.content_hash, 0, "EG000")
    assert (arm.deaths_affected, arm.deaths_at_risk) == (52, 102)
    assert (arm.serious_affected, arm.serious_at_risk, arm.other_affected, arm.other_at_risk) == (42, 101, 101, 101)
    [found] = result.events
    assert (found.kind, found.position, found.term) == ("serious", 0, "Thrombocytopenia")
    assert found.organ_system == "Blood disorders" and found.vocabulary == "MedDRA25.1"
    assert found.assessment_type == "systematic"
    assert [(c.group_id, c.affected, c.at_risk, c.events) for c in found.counts] == [("EG000", 1, 101, 1)]


def test_a_missing_number_is_none_not_zero():
    result = parse_safety(record({"eventGroups": [group(seriousNumAffected=0, seriousNumAtRisk=50)]}), STORED)
    [arm] = result.arms
    assert arm.serious_affected == 0                       # a real zero stays zero
    assert arm.deaths_affected is None and arm.deaths_at_risk is None and arm.other_at_risk is None


def test_missing_event_counts_stay_none():
    module = {"eventGroups": [group()], "otherEvents": [event(stats=[{"groupId": "EG000", "numAffected": 3}])]}
    [found] = parse_safety(record(module), STORED).events
    [count] = found.counts
    assert count.affected == 3 and count.at_risk is None and count.events is None


def test_duplicate_terms_are_both_kept_in_order():
    rows = [event("Anorexia", stats=[{"groupId": "EG000", "numAffected": 10}]),
            event("Anorexia", stats=[{"groupId": "EG000", "numAffected": 1}])]
    events = parse_safety(record({"eventGroups": [group()], "otherEvents": rows}), STORED).events
    assert [(e.position, e.counts[0].affected) for e in events] == [(0, 10), (1, 1)]


def test_serious_events_come_first_then_other_each_with_its_own_position():
    module = {"eventGroups": [group()], "seriousEvents": [event("A"), event("B")], "otherEvents": [event("C")]}
    events = parse_safety(record(module), STORED).events
    assert [(e.kind, e.position, e.term) for e in events] == [("serious", 0, "A"), ("serious", 1, "B"), ("other", 0, "C")]


def test_threshold_zero_is_kept_and_a_missing_threshold_is_none():
    assert parse_safety(record({"frequencyThreshold": "0", "eventGroups": []}), STORED).frequency_threshold == 0.0
    assert parse_safety(record({"eventGroups": []}), STORED).frequency_threshold is None


def test_assessment_type_unknown_or_missing_is_none():
    module = {"eventGroups": [group()], "otherEvents": [
        event("A", assessmentType="NON_SYSTEMATIC_ASSESSMENT"), event("B", assessmentType="SOMETHING_NEW"), event("C")]}
    kinds = [e.assessment_type for e in parse_safety(record(module), STORED).events]
    assert kinds == ["non_systematic", None, None]


def test_vocabulary_and_long_terms_are_kept_exactly():
    term = "Chest pain and slightly elevated Troponin I"
    module = {"eventGroups": [group()], "seriousEvents": [event(term, sourceVocabulary="MedDRA 18.1 & 19.0")]}
    [found] = parse_safety(record(module), STORED).events
    assert found.term == term and found.vocabulary == "MedDRA 18.1 & 19.0"


@pytest.mark.parametrize("title,pooled", [("All Stribild", True), ("Total", True), ("Overall population", True),
                                          ("Combined arms", True), ("Drug A", False), ("Allopurinol", False)])
def test_pooled_looking_groups_are_flagged(title, pooled):
    [arm] = parse_safety(record({"eventGroups": [group(title=title)]}), STORED).arms
    assert arm.looks_pooled is pooled


def test_at_risk_zero_is_kept_as_given():
    [arm] = parse_safety(record({"eventGroups": [group(seriousNumAffected=0, seriousNumAtRisk=0)]}), STORED).arms
    assert arm.serious_at_risk == 0


def test_no_module_is_absent_not_no_side_effects():
    result = parse_safety(record(), STORED)
    assert result.status == "absent" and result.arms == [] and result.events == []


def test_a_module_without_groups_or_events_is_ok_and_empty():
    result = parse_safety(record({}), STORED)
    assert result.status == "ok" and result.arms == [] and result.events == []


def test_the_record_must_be_the_expected_trial():
    with pytest.raises(ParseError):
        parse_safety(record({"eventGroups": []}, nct="NCT00000001"), STORED)


@pytest.mark.parametrize("module", [
    [],                                                                   # module is not an object
    {"eventGroups": "x"},                                                 # groups not a list
    {"eventGroups": ["x"]},                                               # group not an object
    {"eventGroups": [{"title": "A"}]},                                    # group without an id
    {"eventGroups": [{"id": "EG000"}]},                                   # group without a title
    {"eventGroups": [group(seriousNumAffected="12")]},                    # number given as text
    {"eventGroups": [group(deathsNumAffected=True)]},                     # true/false is not a number
    {"eventGroups": [group(deathsNumAffected=-1)]},                       # negative count
    {"eventGroups": [group()], "seriousEvents": "x"},                     # events not a list
    {"eventGroups": [group()], "seriousEvents": ["x"]},                   # event not an object
    {"eventGroups": [group()], "seriousEvents": [event(stats=["x"])]},    # stat not an object
    {"eventGroups": [group()], "seriousEvents": [event(stats=[{"numAffected": 1}])]},   # stat without a group id
    {"frequencyThreshold": "five", "eventGroups": []},                    # threshold not a number
])
def test_bad_entries_are_reported_not_dropped(module):
    with pytest.raises(ParseError):
        parse_safety(record(module), STORED)


def test_safety_path_names_the_value_in_the_record():
    assert safety_path("eventGroups", 0, "title") == "resultsSection.adverseEventsModule.eventGroups.0.title"


def test_a_row_without_a_term_is_kept_with_its_note():
    row = {"notes": "Death was reportable as an SAE", "stats": [{"groupId": "EG000", "numAffected": 7, "numAtRisk": 60}]}
    [found] = parse_safety(record({"eventGroups": [group()], "seriousEvents": [row]}), STORED).events
    assert found.term is None and found.notes == "Death was reportable as an SAE" and found.counts[0].affected == 7

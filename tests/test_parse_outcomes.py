##############################################################################
# tests/test_parse_outcomes.py
#
# PURPOSE: Prove registered outcomes are read in order with their text kept
#          exactly, "no section" differs from "empty section", bad entries are
#          reported, and every filled outcome field has evidence.
##############################################################################

import copy
import json
from datetime import datetime, timezone

import pytest

from trialtrace.parse.registry_parser import ParseError, extract_evidence, outcome_path, parse_outcomes
from trialtrace.sources.raw_store import StoredRaw

NCT = "NCT04644237"
STORED = StoredRaw(
    nct_id=NCT, url=f"https://clinicaltrials.gov/api/v2/studies/{NCT}",
    retrieved_at=datetime.now(timezone.utc), content_hash="a" * 64,
    raw_file_path=f"data/raw/clinicaltrials/{NCT}/{'a' * 64}.json",
)

RECORD = {
    "protocolSection": {
        "identificationModule": {"nctId": NCT, "briefTitle": "T"},
        "outcomesModule": {
            "primaryOutcomes": [
                {"measure": "Overall survival", "description": "Time to death", "timeFrame": "24 months"},
            ],
            "secondaryOutcomes": [
                {"measure": "Response rate", "timeFrame": "12 weeks"},
                {"measure": "Safety"},
            ],
        },
    },
}


def body(record: dict) -> bytes:
    return json.dumps(record).encode("utf-8")


def with_module(module) -> bytes:
    record = copy.deepcopy(RECORD)
    if module is None:
        del record["protocolSection"]["outcomesModule"]
    else:
        record["protocolSection"]["outcomesModule"] = module
    return body(record)


def test_outcomes_are_read_in_order_with_roles_and_positions():
    result = parse_outcomes(body(RECORD), STORED)
    assert result.status == "ok"
    assert [(o.role, o.position, o.measure_text) for o in result.outcomes] == [
        ("primary", 0, "Overall survival"), ("secondary", 0, "Response rate"), ("secondary", 1, "Safety"),
    ]


def test_text_is_kept_exactly_and_missing_parts_are_unknown():
    first, second, third = parse_outcomes(body(RECORD), STORED).outcomes
    assert (first.description, first.time_frame) == ("Time to death", "24 months")
    assert (second.description, second.time_frame) == (None, "12 weeks")
    assert (third.description, third.time_frame) == (None, None)


def test_every_outcome_carries_its_trial_and_version():
    assert all(o.nct_id == NCT and o.content_hash == "a" * 64 for o in parse_outcomes(body(RECORD), STORED).outcomes)


def test_no_section_is_absent_not_empty():
    result = parse_outcomes(with_module(None), STORED)
    assert result.status == "absent" and result.outcomes == []


def test_empty_section_is_ok_with_no_outcomes():
    result = parse_outcomes(with_module({}), STORED)
    assert result.status == "ok" and result.outcomes == []


def test_other_outcomes_are_read_not_silently_ignored():
    record = copy.deepcopy(RECORD)
    record["protocolSection"]["outcomesModule"]["otherOutcomes"] = [{"measure": "Quality of life", "timeFrame": "6 months"}]
    result = parse_outcomes(body(record), STORED)
    assert [(o.role, o.position, o.measure_text) for o in result.outcomes][-1] == ("other", 0, "Quality of life")
    paths = {item.field_path for item in extract_evidence(body(record), STORED)}
    assert outcome_path("other", 0, "measure") in paths


def test_only_primary_list_present_is_fine():
    result = parse_outcomes(with_module({"primaryOutcomes": [{"measure": "OS"}]}), STORED)
    assert [(o.role, o.measure_text) for o in result.outcomes] == [("primary", "OS")]


@pytest.mark.parametrize("module", [
    {"primaryOutcomes": [{"description": "no measure"}]},      # entry without a measure
    {"primaryOutcomes": [{"measure": ""}]},                    # empty measure
    {"primaryOutcomes": ["just text"]},                        # entry is not an object
    {"primaryOutcomes": {"measure": "not a list"}},            # list is not a list
    "not an object",                                           # section is not an object
])
def test_malformed_outcomes_raise_parse_error(module):
    with pytest.raises(ParseError):
        parse_outcomes(with_module(module), STORED)


def test_wrong_trial_is_rejected():
    record = copy.deepcopy(RECORD)
    record["protocolSection"]["identificationModule"]["nctId"] = "NCT99999999"
    with pytest.raises(ParseError):
        parse_outcomes(body(record), STORED)


def test_every_filled_outcome_field_has_evidence_and_missing_ones_do_not():
    paths = {item.field_path for item in extract_evidence(body(RECORD), STORED)}
    assert outcome_path("primary", 0, "timeFrame") in paths
    assert outcome_path("secondary", 1, "measure") in paths
    assert outcome_path("secondary", 1, "description") not in paths      # not in the record


def test_an_outcomes_evidence_value_is_its_exact_text():
    items = {i.field_path: i for i in extract_evidence(body(RECORD), STORED)}
    evidence = items[outcome_path("primary", 0, "measure")]
    assert evidence.value == "Overall survival"
    assert evidence.evidence_id == f"{NCT}:{'a' * 12}:{outcome_path('primary', 0, 'measure')}"

##############################################################################
# tests/test_results_parser.py
#
# PURPOSE: Prove reported (posted-results) outcomes are read in order with their
#          text kept exactly, every type maps correctly, "no results posted" is
#          reported as absent, and bad entries are reported, not dropped.
##############################################################################

import json
from datetime import datetime, timezone

import pytest

from trialtrace.parse.registry_parser import ParseError
from trialtrace.parse.results_parser import parse_results_outcomes, results_outcome_path
from trialtrace.sources.raw_store import StoredRaw

NCT = "NCT04644237"
STORED = StoredRaw(
    nct_id=NCT, url=f"https://clinicaltrials.gov/api/v2/studies/{NCT}",
    retrieved_at=datetime.now(timezone.utc), content_hash="a" * 64,
    raw_file_path=f"data/raw/clinicaltrials/{NCT}/{'a' * 64}.json",
)


def record(measures="missing", nct=NCT, with_results=True) -> bytes:
    """A small registry record. measures='missing' leaves the list out of the results module."""
    data = {"protocolSection": {"identificationModule": {"nctId": nct}}}
    if with_results:
        module = {} if measures == "missing" else {"outcomeMeasures": measures}
        data["resultsSection"] = {"outcomeMeasuresModule": module}
    return json.dumps(data).encode()


def measure(title="Overall survival", type_="PRIMARY", **fields):
    return {"type": type_, "title": title, **fields}


def test_a_measure_is_read_with_all_its_fields():
    entry = measure(description="Time to death", timeFrame="24 months",
                    populationDescription="Full analysis set", reportingStatus="POSTED")
    result = parse_results_outcomes(record([entry]), STORED)
    [outcome] = result.outcomes
    assert result.status == "ok"
    assert (outcome.nct_id, outcome.content_hash, outcome.position) == (NCT, STORED.content_hash, 0)
    assert outcome.result_type == "primary" and outcome.measure_text == "Overall survival"
    assert outcome.description == "Time to death" and outcome.time_frame == "24 months"
    assert outcome.population == "Full analysis set" and outcome.reporting_status == "POSTED"


@pytest.mark.parametrize("raw, expected", [
    ("PRIMARY", "primary"),
    ("SECONDARY", "secondary"),
    ("OTHER_PRE_SPECIFIED", "other"),
    ("POST_HOC", "post_hoc"),
])
def test_every_known_type_maps_to_our_name(raw, expected):
    [outcome] = parse_results_outcomes(record([measure(type_=raw)]), STORED).outcomes
    assert outcome.result_type == expected


@pytest.mark.parametrize("raw", ["SOMETHING_NEW", "primary", "", None])
def test_an_unknown_type_is_none_not_a_guess(raw):
    [outcome] = parse_results_outcomes(record([measure(type_=raw)]), STORED).outcomes
    assert outcome.result_type is None


def test_a_missing_type_is_none():
    [outcome] = parse_results_outcomes(record([{"title": "No type given"}]), STORED).outcomes
    assert outcome.result_type is None


def test_the_registry_order_is_kept_across_types():
    titles = ["B", "A", "C"]
    result = parse_results_outcomes(record([measure(t, type_=ty) for t, ty in zip(titles, ["SECONDARY", "PRIMARY", "POST_HOC"])]), STORED)
    assert [(o.position, o.measure_text) for o in result.outcomes] == [(0, "B"), (1, "A"), (2, "C")]


def test_missing_and_empty_parts_are_none():
    [outcome] = parse_results_outcomes(record([measure(description="", timeFrame=None)]), STORED).outcomes
    assert outcome.description is None and outcome.time_frame is None
    assert outcome.population is None and outcome.reporting_status is None


def test_text_is_kept_exactly():
    title = "  Percentage of Participants With ORR (BICR)  "
    [outcome] = parse_results_outcomes(record([measure(title)]), STORED).outcomes
    assert outcome.measure_text == title


def test_no_results_section_is_absent():
    result = parse_results_outcomes(record(with_results=False), STORED)
    assert result.status == "absent" and result.outcomes == []


def test_a_results_module_without_measures_is_absent():
    assert parse_results_outcomes(record("missing"), STORED).status == "absent"


def test_an_empty_measures_list_is_ok_with_no_outcomes():
    result = parse_results_outcomes(record([]), STORED)
    assert result.status == "ok" and result.outcomes == []


def test_a_record_for_another_trial_is_refused():
    with pytest.raises(ParseError):
        parse_results_outcomes(record([measure()], nct="NCT00000001"), STORED)


@pytest.mark.parametrize("bad", ["text", 5, {"title": "x"}])
def test_measures_that_are_not_a_list_raise(bad):
    with pytest.raises(ParseError):
        parse_results_outcomes(record(bad), STORED)


@pytest.mark.parametrize("bad_entry", ["text", 5, None, ["a"]])
def test_an_entry_that_is_not_an_object_raises(bad_entry):
    with pytest.raises(ParseError):
        parse_results_outcomes(record([measure(), bad_entry]), STORED)


@pytest.mark.parametrize("bad_entry", [{"type": "PRIMARY"}, {"type": "PRIMARY", "title": ""}])
def test_an_entry_without_a_title_raises(bad_entry):
    with pytest.raises(ParseError):
        parse_results_outcomes(record([bad_entry]), STORED)


def test_a_results_module_that_is_not_an_object_raises():
    body = json.dumps({"protocolSection": {"identificationModule": {"nctId": NCT}},
                       "resultsSection": {"outcomeMeasuresModule": "oops"}}).encode()
    with pytest.raises(ParseError):
        parse_results_outcomes(body, STORED)


def test_broken_json_raises():
    with pytest.raises(ParseError):
        parse_results_outcomes(b"{not json", STORED)


def test_the_evidence_path_points_at_the_exact_field():
    assert results_outcome_path(3, "title") == "resultsSection.outcomeMeasuresModule.outcomeMeasures.3.title"

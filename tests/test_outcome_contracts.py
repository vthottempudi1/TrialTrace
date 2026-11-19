##############################################################################
# tests/test_outcome_contracts.py
#
# PURPOSE: Prove the outcome models reject bad data, and that the outcome
#          path helper builds the address evidence will use.
##############################################################################

import pytest
from pydantic import ValidationError

from trialtrace.contracts import OutcomeExtraction, RegisteredOutcome
from trialtrace.parse.registry_parser import get_path, outcome_path


def valid_outcome(**changes) -> dict:
    data = {"nct_id": "NCT04644237", "content_hash": "a" * 64, "role": "primary",
            "position": 0, "measure_text": "Overall survival"}
    data.update(changes)
    return data


def test_valid_outcome_is_accepted_with_optional_fields_unknown():
    outcome = RegisteredOutcome(**valid_outcome())
    assert outcome.description is None and outcome.time_frame is None


@pytest.mark.parametrize("change", [
    {"role": "tertiary"}, {"position": -1}, {"measure_text": ""}, {"nct_id": "NCT123"}, {"content_hash": "A" * 64},
])
def test_bad_outcome_is_rejected(change):
    with pytest.raises(ValidationError):
        RegisteredOutcome(**valid_outcome(**change))


def test_absent_section_with_outcomes_is_rejected():
    with pytest.raises(ValidationError):
        OutcomeExtraction(status="absent", outcomes=[RegisteredOutcome(**valid_outcome())])


def test_empty_ok_and_absent_are_different_answers():
    assert OutcomeExtraction(status="ok").status != OutcomeExtraction(status="absent").status


def test_outcome_path_builds_the_evidence_address():
    assert outcome_path("secondary", 3, "timeFrame") == "protocolSection.outcomesModule.secondaryOutcomes.3.timeFrame"


def test_outcome_path_can_be_read_with_get_path():
    record = {"protocolSection": {"outcomesModule": {"primaryOutcomes": [{"measure": "OS"}, {"measure": "PFS"}]}}}
    assert get_path(record, outcome_path("primary", 1, "measure")) == "PFS"

##############################################################################
# tests/test_registry_parser.py
#
# PURPOSE: Prove the parser keeps unknown as unknown, rejects untrustworthy
#          records, and cites evidence only for values that exist.
##############################################################################

import copy
import json
from datetime import datetime, timezone

import pytest

from trialtrace.parse.registry_parser import ParseError, extract_evidence, get_path, parse_study
from trialtrace.sources.raw_store import StoredRaw

NCT = "NCT04644237"

FULL_RECORD = {
    "protocolSection": {
        "identificationModule": {"nctId": NCT, "briefTitle": "Drug X in lung cancer"},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Acme Pharma"}},
        "statusModule": {
            "overallStatus": "COMPLETED",
            "primaryCompletionDateStruct": {"date": "2019-05", "type": "ACTUAL"},
            "lastUpdatePostDateStruct": {"date": "2025-11-21", "type": "ACTUAL"},
        },
    },
    "hasResults": True,
}

STORED = StoredRaw(
    nct_id=NCT,
    url=f"https://clinicaltrials.gov/api/v2/studies/{NCT}",
    retrieved_at=datetime.now(timezone.utc),
    content_hash="a" * 64,
    raw_file_path=f"data/raw/clinicaltrials/{NCT}/{'a' * 64}.json",
)


def body_of(**changes) -> bytes:
    """FULL_RECORD as bytes. Pass dotted-path removals via remove=[...], or top-level changes."""
    record = copy.deepcopy(FULL_RECORD)
    record.update(changes)
    return json.dumps(record).encode("utf-8")


def without(path: list[str]) -> bytes:
    """FULL_RECORD with one nested key deleted."""
    record = copy.deepcopy(FULL_RECORD)
    parent = record
    for key in path[:-1]:
        parent = parent[key]
    del parent[path[-1]]
    return json.dumps(record).encode("utf-8")


LISTED = {"a": [{"x": 1}, {"x": 2}]}


def test_get_path_enters_a_list_by_position():
    assert get_path(LISTED, "a.1.x") == 2


def test_get_path_counts_list_positions_from_zero():
    assert get_path(LISTED, "a.0.x") == 1


@pytest.mark.parametrize("path", ["a.5.x", "a.x", "a.-1.x", "a.1.y", "a.1.x.z"])
def test_get_path_gives_none_for_a_bad_list_step(path):
    assert get_path(LISTED, path) is None


def test_complete_record_parses():
    snap = parse_study(body_of(), STORED)
    assert snap.nct_id == NCT
    assert snap.sponsor == "Acme Pharma"
    assert snap.primary_completion_date == "2019-05"     # no day invented
    assert snap.primary_completion_type == "ACTUAL"
    assert snap.has_results is True
    assert snap.registry_last_update == "2025-11-21"     # kept, not silently dropped


def test_missing_registry_last_update_is_unknown():
    body = without(["protocolSection", "statusModule", "lastUpdatePostDateStruct"])
    assert parse_study(body, STORED).registry_last_update is None


def test_registry_last_update_has_evidence():
    paths = [item.field_path for item in extract_evidence(body_of(), STORED)]
    assert "protocolSection.statusModule.lastUpdatePostDateStruct.date" in paths


def test_missing_has_results_is_unknown():
    record = copy.deepcopy(FULL_RECORD)
    del record["hasResults"]
    assert parse_study(json.dumps(record).encode(), STORED).has_results is None


def test_non_boolean_has_results_is_unknown():
    assert parse_study(body_of(hasResults="yes"), STORED).has_results is None


def test_unknown_completion_type_is_unknown():
    record = copy.deepcopy(FULL_RECORD)
    record["protocolSection"]["statusModule"]["primaryCompletionDateStruct"]["type"] = "ANTICIPATED"
    assert parse_study(json.dumps(record).encode(), STORED).primary_completion_type is None


def test_wrong_nct_id_is_rejected():
    record = copy.deepcopy(FULL_RECORD)
    record["protocolSection"]["identificationModule"]["nctId"] = "NCT99999999"
    with pytest.raises(ParseError):
        parse_study(json.dumps(record).encode(), STORED)


def test_bad_json_is_rejected():
    with pytest.raises(ParseError):
        parse_study(b"<html>not json</html>", STORED)


def test_missing_title_is_a_parse_error_not_a_crash():
    with pytest.raises(ParseError):
        parse_study(without(["protocolSection", "identificationModule", "briefTitle"]), STORED)


def test_evidence_cites_present_values_only():
    body = without(["protocolSection", "sponsorCollaboratorsModule", "leadSponsor", "name"])
    paths = [item.field_path for item in extract_evidence(body, STORED)]
    assert "protocolSection.sponsorCollaboratorsModule.leadSponsor.name" not in paths
    assert "protocolSection.statusModule.overallStatus" in paths


def test_false_has_results_still_gets_evidence():
    # False is a real answer, not an absence
    items = extract_evidence(body_of(hasResults=False), STORED)
    has_results = [i for i in items if i.field_path == "hasResults"]
    assert len(has_results) == 1
    assert has_results[0].value == "false"

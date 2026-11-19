##############################################################################
# tests/test_ingest_registry.py
#
# PURPOSE: Prove the ingest pipeline: stores a trial, records every kind of
#          failure, retries only temporary failures (at most 3 tries), and
#          never loses a snapshot because of a bad outcomes section.
#          A fake network replaces the registry; the database is rolled back;
#          raw files go to a temporary folder.
##############################################################################

import copy
import json

import time

import httpx
import pytest

from trialtrace.compare.registry_changes import check_registry_changes
from trialtrace.config import Settings
from trialtrace.db.repository import get_latest_snapshot, get_outcomes, get_results_outcomes, get_safety
from trialtrace.ingest_registry import MAX_ATTEMPTS, fetch_with_retries, ingest_trial, is_temporary

NCT = "NCT90000088"

RECORD = {
    "protocolSection": {
        "identificationModule": {"nctId": NCT, "briefTitle": "Drug X"},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Acme Pharma, Inc."}},
        "statusModule": {
            "overallStatus": "COMPLETED",
            "primaryCompletionDateStruct": {"date": "2019-05", "type": "ACTUAL"},
            "lastUpdatePostDateStruct": {"date": "2025-11-21"},
        },
        "outcomesModule": {"primaryOutcomes": [{"measure": "Overall survival", "timeFrame": "12 months"}]},
    },
    "hasResults": False,
}


def record_with(**time_frame) -> bytes:
    record = copy.deepcopy(RECORD)
    if time_frame:
        record["protocolSection"]["outcomesModule"]["primaryOutcomes"][0]["timeFrame"] = time_frame["value"]
    return json.dumps(record).encode()


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path)      # raw files go to a temporary folder


class FakeRegistry:
    """Answers each request with the next response in the list (the last one repeats)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = 0

    def __call__(self, request):
        self.requests += 1
        response = self.responses[min(self.requests, len(self.responses)) - 1]
        if isinstance(response, Exception):
            raise response
        return response

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


def run(conn, settings, registry, nct_id=NCT):
    sleeps = []
    with registry.client() as http:
        result = ingest_trial(conn, http, settings, nct_id, sleep=sleeps.append)
    return result, sleeps


def failures(conn):
    return conn.execute("SELECT step, error_code, attempts FROM ingest_failures WHERE nct_id = %s", (NCT,)).fetchall()


# --- success -----------------------------------------------------------------

def test_a_new_trial_is_stored_with_evidence_and_outcomes(conn, settings):
    result, _ = run(conn, settings, FakeRegistry(httpx.Response(200, content=record_with())))
    assert result.status == "new_version" and result.attempts == 1

    snapshot = get_latest_snapshot(conn, NCT)
    assert snapshot.sponsor == "Acme Pharma, Inc." and snapshot.has_results is False
    assert snapshot.registry_last_update == "2025-11-21"
    assert get_outcomes(conn, NCT, snapshot.content_hash).outcomes[0].measure_text == "Overall survival"
    evidence_count = conn.execute("SELECT count(*) FROM evidence WHERE nct_id = %s", (NCT,)).fetchone()[0]
    assert evidence_count > 0
    assert (settings.data_dir / "raw" / "clinicaltrials" / NCT / f"{snapshot.content_hash}.json").exists()


def test_the_same_content_again_is_unchanged_but_still_observed(conn, settings):
    body = httpx.Response(200, content=record_with())
    run(conn, settings, FakeRegistry(body))
    time.sleep(0.05)                      # two fetches in one clock tick would collapse into one observation
    result, _ = run(conn, settings, FakeRegistry(httpx.Response(200, content=record_with())))
    assert result.status == "unchanged"
    observed = conn.execute("SELECT count(*) FROM observations WHERE nct_id = %s", (NCT,)).fetchone()[0]
    assert observed == 2


def test_changed_content_is_a_new_version_and_5a_sees_the_change(conn, settings):
    run(conn, settings, FakeRegistry(httpx.Response(200, content=record_with())))
    result, _ = run(conn, settings, FakeRegistry(httpx.Response(200, content=record_with(value="24 months"))))
    assert result.status == "new_version"
    report = check_registry_changes(conn, NCT)
    assert report.status == "changes" and [c.kind for c in report.changes] == ["time_frame_changed"]


# --- failures are recorded ---------------------------------------------------

def test_not_found_is_recorded_and_not_retried(conn, settings):
    registry = FakeRegistry(httpx.Response(404))
    result, sleeps = run(conn, settings, registry)
    assert result.status == "not_found" and registry.requests == 1 and sleeps == []
    assert failures(conn) == [("fetch", "not_found", 1)]
    assert get_latest_snapshot(conn, NCT) is None


def test_a_server_error_is_retried_up_to_three_times_then_recorded(conn, settings):
    registry = FakeRegistry(httpx.Response(500))
    result, sleeps = run(conn, settings, registry)
    assert result.status == "failed" and result.attempts == MAX_ATTEMPTS == registry.requests
    assert sleeps == [1, 2]                                         # waits grow
    assert failures(conn) == [("fetch", "http_500", 3)]


def test_a_temporary_failure_then_success_records_no_failure(conn, settings):
    registry = FakeRegistry(httpx.Response(503), httpx.Response(429), httpx.Response(200, content=record_with()))
    result, sleeps = run(conn, settings, registry)
    assert result.status == "new_version" and result.attempts == 3 and sleeps == [1, 2]
    assert failures(conn) == []


def test_a_timeout_is_retried(conn, settings):
    registry = FakeRegistry(httpx.ReadTimeout("slow"), httpx.Response(200, content=record_with()))
    result, _ = run(conn, settings, registry)
    assert result.status == "new_version" and result.attempts == 2


def test_a_permanent_error_is_not_retried(conn, settings):
    registry = FakeRegistry(httpx.Response(403))
    result, sleeps = run(conn, settings, registry)
    assert result.attempts == 1 and sleeps == [] and failures(conn) == [("fetch", "http_403", 1)]


def test_an_invalid_id_sends_no_request_and_records_nothing(conn, settings):
    registry = FakeRegistry(httpx.Response(200, content=record_with()))
    result, _ = run(conn, settings, registry, nct_id="NCT123/../x")
    assert result.status == "invalid_id" and registry.requests == 0


def test_an_unreadable_record_is_recorded_and_its_raw_file_is_kept(conn, settings):
    result, _ = run(conn, settings, FakeRegistry(httpx.Response(200, content=b"<html>not json</html>")))
    assert result.status == "failed" and failures(conn) == [("parse", "ParseError", 1)]
    assert get_latest_snapshot(conn, NCT) is None
    assert list((settings.data_dir / "raw" / "clinicaltrials" / NCT).glob("*.json"))      # evidence kept


def test_a_record_for_another_trial_is_rejected(conn, settings):
    record = copy.deepcopy(RECORD)
    record["protocolSection"]["identificationModule"]["nctId"] = "NCT99999999"
    result, _ = run(conn, settings, FakeRegistry(httpx.Response(200, content=json.dumps(record).encode())))
    assert result.status == "failed" and get_latest_snapshot(conn, NCT) is None


def test_bad_outcomes_do_not_lose_the_snapshot(conn, settings):
    record = copy.deepcopy(RECORD)
    record["protocolSection"]["outcomesModule"] = {"primaryOutcomes": [{"description": "no measure"}]}
    result, _ = run(conn, settings, FakeRegistry(httpx.Response(200, content=json.dumps(record).encode())))
    assert result.status == "new_version"
    snapshot = get_latest_snapshot(conn, NCT)
    assert snapshot is not None
    assert get_outcomes(conn, NCT, snapshot.content_hash) is None            # unknown, not "none"
    assert failures(conn) == [("parse", "outcomes_ParseError", 1)]


def test_posted_results_outcomes_are_stored_next_to_the_registered_ones(conn, settings):
    record = copy.deepcopy(RECORD)
    record["hasResults"] = True
    record["resultsSection"] = {"outcomeMeasuresModule": {"outcomeMeasures": [
        {"type": "PRIMARY", "title": "Overall survival", "timeFrame": "18 months"},
        {"type": "POST_HOC", "title": "Extra analysis"},
    ]}}
    run(conn, settings, FakeRegistry(httpx.Response(200, content=json.dumps(record).encode())))
    snapshot = get_latest_snapshot(conn, NCT)
    reported = get_results_outcomes(conn, NCT, snapshot.content_hash)
    assert reported.status == "ok"
    assert [(o.result_type, o.measure_text, o.time_frame) for o in reported.outcomes] == [
        ("primary", "Overall survival", "18 months"), ("post_hoc", "Extra analysis", None)]
    assert get_outcomes(conn, NCT, snapshot.content_hash).outcomes[0].time_frame == "12 months"   # the plan is untouched


def test_a_trial_without_posted_results_is_absent_not_unknown(conn, settings):
    run(conn, settings, FakeRegistry(httpx.Response(200, content=record_with())))
    snapshot = get_latest_snapshot(conn, NCT)
    assert get_results_outcomes(conn, NCT, snapshot.content_hash).status == "absent"


def test_bad_results_do_not_lose_the_snapshot(conn, settings):
    record = copy.deepcopy(RECORD)
    record["resultsSection"] = {"outcomeMeasuresModule": {"outcomeMeasures": [{"type": "PRIMARY"}]}}   # no title
    result, _ = run(conn, settings, FakeRegistry(httpx.Response(200, content=json.dumps(record).encode())))
    assert result.status == "new_version"
    snapshot = get_latest_snapshot(conn, NCT)
    assert get_results_outcomes(conn, NCT, snapshot.content_hash) is None       # unknown, not "none"
    assert get_outcomes(conn, NCT, snapshot.content_hash) is not None           # the plan still saved
    assert failures(conn) == [("parse", "results_ParseError", 1)]


# --- the retry rule on its own -----------------------------------------------

@pytest.mark.parametrize("code, expected", [
    ("timeout", True), ("network_error", True), ("http_429", True), ("http_500", True), ("http_503", True),
    ("http_403", False), ("http_404", False), (None, False),
])
def test_which_errors_are_temporary(code, expected):
    assert is_temporary(code) is expected


def test_fetch_with_retries_gives_up_after_the_limit(settings):
    registry = FakeRegistry(httpx.Response(500))
    sleeps = []
    with registry.client() as http:
        fetch, attempts = fetch_with_retries(http, settings, NCT, sleep=sleeps.append)
    assert fetch.status == "unavailable" and attempts == 3 and sleeps == [1, 2]


def test_the_safety_module_is_stored_with_the_snapshot(conn, settings):
    record = copy.deepcopy(RECORD)
    record["resultsSection"] = {"adverseEventsModule": {
        "frequencyThreshold": "5", "eventGroups": [{"id": "EG000", "title": "Drug A", "seriousNumAffected": 2, "seriousNumAtRisk": 40}],
        "seriousEvents": [{"term": "Anaemia", "stats": [{"groupId": "EG000", "numAffected": 2, "numAtRisk": 40}]}]}}
    run(conn, settings, FakeRegistry(httpx.Response(200, content=json.dumps(record).encode())))
    snapshot = get_latest_snapshot(conn, NCT)
    stored = get_safety(conn, NCT, snapshot.content_hash)
    assert stored.status == "ok" and stored.arms[0].serious_affected == 2 and stored.events[0].term == "Anaemia"


def test_a_record_without_a_safety_module_is_stored_as_absent(conn, settings):
    run(conn, settings, FakeRegistry(httpx.Response(200, content=record_with())))
    snapshot = get_latest_snapshot(conn, NCT)
    assert get_safety(conn, NCT, snapshot.content_hash).status == "absent"


def test_a_bad_safety_module_does_not_lose_the_snapshot(conn, settings):
    record = copy.deepcopy(RECORD)
    record["resultsSection"] = {"adverseEventsModule": {"eventGroups": [{"title": "no id"}]}}
    result, _ = run(conn, settings, FakeRegistry(httpx.Response(200, content=json.dumps(record).encode())))
    assert result.status == "new_version"
    snapshot = get_latest_snapshot(conn, NCT)
    assert get_safety(conn, NCT, snapshot.content_hash) is None                  # unknown, not "none"
    assert get_outcomes(conn, NCT, snapshot.content_hash) is not None
    assert failures(conn) == [("parse", "safety_ParseError", 1)]

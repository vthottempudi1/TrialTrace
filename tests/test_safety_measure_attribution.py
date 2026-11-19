"""Behavioral safeguards for estimates and trial-specific multi-study results."""
import json

import pytest

from trialtrace.safety.safety_llm import reading_from_answer
from trialtrace.safety.safety_compare import compare_statement
from trialtrace.contracts import SafetyArm, SafetyExtraction

TARGET = "NCT12345678"


def read(abstract, quote, target=TARGET, **fields):
    answer = {"document": "trial_results", "statements": [
        {"type": "death", "quote": quote, "percent": 10, **fields}]}
    return reading_from_answer("1", abstract, json.dumps(answer), target_nct_id=target)


def test_reported_estimate_is_preserved_but_cannot_match_a_raw_proportion():
    quote = "The cumulative incidence of death was 10%."
    reading = read(quote, quote)
    statement = reading.statements[0]
    assert statement.measure == "time_to_event"
    safety = SafetyExtraction(status="ok", arms=[SafetyArm(
        nct_id=TARGET, content_hash="a" * 64, position=0, group_id="EG000", title="Drug",
        deaths_affected=10, deaths_at_risk=100)])
    result = compare_statement(statement, safety)
    assert result.category == "safety_not_comparable"
    assert result.reason_code == "time_to_event_estimate"


def test_estimate_cannot_be_invented_for_a_raw_percentage():
    quote = "Deaths occurred in 10% of participants."
    reading = read(quote, quote, measure="time_to_event")
    assert reading.rejected[0].error_code == "estimate_not_supported"


def test_plain_proportion_keeps_its_measure():
    quote = "Deaths occurred in 10% of participants."
    assert read(quote, quote).statements[0].measure == "participant_proportion"


def test_multi_trial_result_with_explicit_local_target_is_retained():
    quote = f"In {TARGET}, deaths occurred in 10% of participants."
    abstract = quote + " Separate studies were NCT23456789 and NCT34567890."
    result = read(abstract, quote, attribution_quote=quote)
    assert len(result.statements) == 1
    assert result.statements[0].attribution_quote == quote


@pytest.mark.parametrize("scope", [None, "fake target evidence", "Deaths occurred in 10% of participants."])
def test_distant_trial_list_does_not_attribute_a_result(scope):
    quote = "Deaths occurred in 10% of participants."
    abstract = quote + f" Trials included {TARGET}, NCT23456789 and NCT34567890."
    result = read(abstract, quote, attribution_quote=scope)
    assert not result.statements
    assert result.rejected[0].error_code == "trial_attribution_unverified"


def test_pooled_result_cannot_use_a_target_mention_as_attribution():
    quote = f"In pooled data including {TARGET}, deaths occurred in 10% of participants."
    result = read(quote + " Another study was NCT23456789.", quote, attribution_quote=quote)
    assert not result.statements


def test_single_other_trial_is_rejected():
    quote = "In NCT23456789, deaths occurred in 10% of participants."
    assert read(quote, quote).rejected[0].error_code == "target_trial_not_in_abstract"


def test_missing_target_in_multi_trial_paper_abstains():
    quote = f"In {TARGET}, deaths occurred in 10% of participants."
    result = read(quote + " Another study was NCT23456789.", quote, target=None, attribution_quote=quote)
    assert not result.statements


def test_multi_trial_text_without_multiple_registry_ids_still_needs_attribution():
    quote = "Deaths occurred in 10% of participants."
    result = read("This analysis covers three trials. " + quote, quote)
    assert not result.statements
    assert result.rejected[0].error_code == "trial_attribution_unverified"


def test_measure_and_attribution_survive_database_round_trip(conn):
    from trialtrace.safety.read_safety import model_statement_row
    from trialtrace.db.repository import save_safety_reading, get_safety_readings
    from trialtrace.safety.safety_rollup import statement_from_row
    quote = f"In {TARGET}, the cumulative incidence of death was 10%."
    abstract = quote + " A separate study was NCT23456789."
    statement = read(abstract, quote, attribution_quote=quote).statements[0]
    save_safety_reading(conn, {"safety_run_id": "measure-test", "nct_id": TARGET,
        "pmid": "1", "paper_raw_hash": "a" * 64, "system": "llm", "status": "ok"},
        [model_statement_row(statement)])
    stored = get_safety_readings(conn, TARGET, "measure-test")[0]["statements"][0]
    recovered = statement_from_row(stored)
    assert recovered.measure == "time_to_event"
    assert recovered.attribution_quote == quote

"""Positive and adversarial checks for the failures exposed by the frozen v3 run."""
import json

import pytest

from trialtrace.safety.safety_llm import reading_from_answer, safety_input_hash
from trialtrace.safety.safety_compare import compare_statement
from trialtrace.safety.safety_identity import check_reading_identity
from trialtrace.contracts import SafetyArm, SafetyExtraction, SafetyEvent, SafetyEventCount
from trialtrace.safety.read_safety import model_statement_row
from trialtrace.safety.safety_rollup import statement_from_row
from trialtrace.db.repository import save_safety_reading, get_safety_readings
from trialtrace.safety.safety_assist import (
    Proposal, Match, Window, candidates_for, event_match_block_reason,
    source_context, suggest_packet, prepare_packet, load_report_comparison,
)
from trialtrace.config import Settings

NCT = "NCT12345678"


def parse(abstract, *entries):
    return reading_from_answer("1", abstract, json.dumps({"document": "trial_results", "statements": entries}), target_nct_id=NCT)


def event(quote, **fields):
    return {"type": "event", "term": "neutropenia", "quote": quote, **fields}


def registry(placebo=False):
    titles = ["Drug", "Placebo"] if placebo else ["Drug"]
    return SafetyExtraction(status="ok", arms=[SafetyArm(
        nct_id=NCT, content_hash="a" * 64, position=i, group_id=f"EG{i}", title=title,
        serious_affected=2, serious_at_risk=100) for i, title in enumerate(titles)])


@pytest.mark.parametrize("phase", ["3", "III", "2/3"])
def test_phase_number_does_not_mean_several_trials(phase):
    quote = "Neutropenia affected 10% of patients."
    abstract = f"Prior phase {phase} trials supported this study. " + quote + f" Registered as {NCT}."
    assert len(parse(abstract, event(quote, percent=10)).statements) == 1


def test_real_multi_trial_text_still_requires_attribution():
    quote = "Neutropenia affected 10% of patients."
    assert parse("We pooled three trials. " + quote, event(quote, percent=10)).rejected[0].error_code == "trial_attribution_unverified"


def test_event_clause_order_ignores_an_introductory_comparator():
    quote = "Compared with placebo, more patients in the Drug group improved, and neutropenia affected 35% (91 of 261) vs 46% (128 of 277)."
    reading = parse(quote, event(quote, arm="Drug group", count=91, denominator=261, percent=35),
                    event(quote, arm="placebo", count=128, denominator=277, percent=46))
    assert len(reading.statements) == 2 and not reading.rejected


def test_direct_swapped_group_numbers_are_still_rejected():
    quote = "Neutropenia affected 10 patients in the Drug group and 20 in the placebo group."
    reading = parse(quote, event(quote, arm="Drug group", count=20), event(quote, arm="placebo group", count=10))
    assert not reading.statements
    assert {s.error_code for s in reading.rejected} == {"order_does_not_match"}


def test_comma_separated_groups_still_reject_swapped_numbers():
    quote = "Neutropenia affected 10 patients in the Drug group, 20 in the placebo group."
    reading = parse(quote, event(quote, arm="Drug group", count=20), event(quote, arm="placebo group", count=10))
    assert not reading.statements
    assert {s.error_code for s in reading.rejected} == {"order_does_not_match"}


def test_an_introductory_arm_mention_does_not_verify_local_binding():
    quote = "Compared with placebo, neutropenia affected 10% of participants."
    found = parse(quote, event(quote, arm="placebo", percent=10)).statements[0]
    assert found.arm_in_quote is False
    assert found.validation_notes


def test_intervening_relative_subgroup_is_not_the_percentage_denominator():
    quote = "One (2%) patient of the three who relapsed died from disease."
    reading = parse(quote, {"type": "death", "quote": quote, "count": 1, "percent": 2, "denominator": 3})
    found = reading.statements[0]
    assert (found.count, found.percent, found.denominator) == (1, 2, None)
    assert found.validation_notes


def test_actual_inconsistent_count_denominator_percent_is_not_repaired():
    quote = "One (2%) of three patients died."
    reading = parse(quote, {"type": "death", "quote": quote, "count": 1, "percent": 2, "denominator": 3})
    assert reading.rejected[0].error_code == "numbers_do_not_fit"


def test_age_restriction_cannot_disappear_when_treatment_arm_matches():
    quote = "Neutropenia affected 10% of AYAs receiving Drug."
    reading = parse(quote, event(quote, arm="Drug", percent=10))
    found = reading.statements[0]
    assert found.population_scope_unverified
    result = compare_statement(found, registry())
    assert result.reason_code == "population_subset_unverified"


def test_population_and_treatment_are_distinct_fields():
    quote = "Neutropenia affected 10% of AYAs receiving Drug."
    found = parse(quote, event(quote, arm="Drug", population="AYAs", percent=10)).statements[0]
    assert found.arm == "Drug" and found.population == "AYAs"
    assert parse(quote, event(quote, population="older adults", percent=10)).rejected[0].error_code == "population_not_in_abstract"


def test_model_cannot_self_approve_a_subgroup_population():
    quote = "Serious adverse events affected 2 of 100 AYAs receiving Drug."
    found = parse(quote, {"type": "serious", "quote": quote, "arm": "Drug", "population": "AYAs",
                         "population_verified": True, "count": 2, "denominator": 100}).statements[0]
    assert compare_statement(found, registry()).reason_code == "population_subset_unverified"
    # Only an explicit caller verification (used by the human-approved path)
    # can clear the population gate; it never erases the original evidence.
    assert compare_statement(found, registry(), population_verified=True).category == "safety_consistent"
    assert found.population == "AYAs"


def test_identity_conflict_is_reviewed_and_retains_evidence(tmp_path):
    quote = "No cases of DRESS were reported."
    abstract = "An open-label study of focal seizures. " + quote
    original = parse(abstract, {"type": "event", "term": "DRESS", "quote": quote, "none_occurred": True})
    checked = check_reading_identity(original, abstract, registry(placebo=True), "PGTC trial")
    assert checked.status == "failed" and checked.error_code == "trial_identity_unverified"
    assert checked.rejected[0].quote.text == quote and not checked.statements
    context = source_context(NCT, "1", "b" * 64, abstract, original.statements[0], registry(placebo=True))
    result = load_report_comparison(tmp_path, context)
    assert result.category == "unknown" and result.reason_code == "trial_identity_unverified"
    settings = Settings(_env_file=None)
    packet = suggest_packet(prepare_packet(context, settings), settings, client=object())
    assert packet["status"] == "abstained" and packet["llm"] is None


def test_population_count_difference_alone_is_not_identity_conflict():
    quote = "Serious adverse events affected 2 of 49 patients."
    original = parse(quote, {"type": "serious", "quote": quote, "count": 2, "denominator": 49})
    assert check_reading_identity(original, quote, registry()).status == "ok"


def test_historical_placebo_cohort_is_not_a_current_placebo_conflict():
    quote = "No serious adverse events occurred."
    abstract = "This open-label study followed prior cohorts. " + quote
    original = parse(abstract, {"type": "serious", "quote": quote, "none_occurred": True})
    safety = registry()
    safety.arms[0].title = "Cohort 1: Placebo/Drug"
    assert check_reading_identity(original, abstract, safety).status == "ok"


def test_placebo_phase_plus_open_label_extension_is_not_an_identity_conflict():
    quote = "No serious adverse events occurred."
    abstract = "A randomized placebo-controlled trial with an open-label extension. " + quote
    original = parse(abstract, {"type": "serious", "quote": quote, "none_occurred": True})
    assert check_reading_identity(original, abstract, registry(placebo=True)).status == "ok"


def test_new_scope_fields_survive_database_round_trip(conn):
    quote = "Neutropenia affected 10% of AYAs receiving Drug."
    original = parse(quote, event(quote, arm="Drug", population="AYAs", percent=10)).statements[0]
    save_safety_reading(conn, {"safety_run_id": "scope-test", "nct_id": NCT, "pmid": "1",
        "paper_raw_hash": "b" * 64, "system": "llm", "status": "ok"}, [model_statement_row(original)])
    recovered = statement_from_row(get_safety_readings(conn, NCT, "scope-test")[0]["statements"][0])
    assert recovered.population == "AYAs" and recovered.population_scope_unverified
    assert recovered.evidence_group == original.evidence_group


def test_total_and_details_share_evidence_group_without_losing_counts():
    quote = "Three patients died, one from pneumonia."
    found = parse(quote, {"type": "death", "quote": quote, "count": 3, "detail_level": "total"},
                  {"type": "death", "quote": quote, "count": 1, "cause": "pneumonia"}).statements
    assert found[0].evidence_group == found[1].evidence_group
    assert [s.count for s in found] == [3, 1]
    assert [s.detail_level for s in found] == ["total", "component"]


def test_registry_identity_context_is_part_of_reuse_key():
    info = {"model": "test", "temperature": None, "prompt_hash": "hash", "schema_version": "v6"}
    assert safety_input_hash("text", info, NCT, registry_safety=registry()) != safety_input_hash(
        "text", info, NCT, registry_safety=registry(placebo=True))


def test_non_serious_cannot_be_relabelled_serious():
    quote = "Non-serious neutropenia affected 10% of participants."
    reading = parse(quote, event(quote, event_scope="serious", percent=10))
    assert reading.rejected[0].error_code == "event_scope_not_supported"


@pytest.mark.parametrize("scope,selected,blocked", [
    ("unspecified", "event:other:0", "event_seriousness_ambiguous"),
    ("serious", "event:other:0", "event_seriousness_mismatch"),
    ("serious", "event:serious:0", None),
    ("non_serious", "event:other:0", None),
])
def test_helper_scope_respects_seriousness_strata(scope, selected, blocked):
    from trialtrace.safety.safety_llm import ModelSafetyStatement
    safety = registry()
    safety.events = [SafetyEvent(nct_id=NCT, content_hash="a" * 64, kind=kind, position=0,
        term="Hypertension", counts=[SafetyEventCount(group_id="EG0", affected=2, at_risk=100)])
        for kind in ("serious", "other")]
    statement = ModelSafetyStatement(kind="event", term="Hypertension", event_scope=scope)
    context = source_context(NCT, "1", "b" * 64, "Hypertension", statement, safety)
    proposal = Proposal(arm=Match(reason="No group needed"),
        event=Match(candidate_id=selected, reason="Test scope"), window=Window(reason="Unknown"))
    assert event_match_block_reason(proposal, context, candidates_for(statement, safety)[0]) == blocked


@pytest.mark.parametrize("kind,expected", [("other", "event_row_non_serious_only"), ("serious", "event_row_serious_only")])
def test_unrestricted_event_cannot_match_a_one_group_row(kind, expected):
    from trialtrace.safety.safety_llm import ModelSafetyStatement
    safety = registry()
    safety.events = [SafetyEvent(nct_id=NCT, content_hash="a" * 64, kind=kind, position=0,
        term="Rash", counts=[SafetyEventCount(group_id="EG0", affected=20, at_risk=100)])]
    statement = ModelSafetyStatement(kind="event", term="rash", event_scope="unspecified")
    context = source_context(NCT, "1", "b" * 64, "rash", statement, safety)
    proposal = Proposal(arm=Match(reason="No group needed"),
        event=Match(candidate_id=f"event:{kind}:0", reason="Same name"), window=Window(reason="Unknown"))
    assert event_match_block_reason(proposal, context, candidates_for(statement, safety)[0]) == expected


def test_explicit_seriousness_can_compare_one_stratum_without_adding_rows():
    quote = "Serious neutropenia affected 2 of 100 patients receiving Drug."
    found = parse(quote, event(quote, arm="Drug", count=2, denominator=100)).statements[0]
    assert found.event_scope == "serious"
    safety = registry()
    safety.events = [SafetyEvent(nct_id=NCT, content_hash="a" * 64, kind=kind, position=0,
        term="neutropenia", counts=[SafetyEventCount(group_id="EG0", affected=count, at_risk=100)])
        for kind, count in (("serious", 2), ("other", 20))]
    result = compare_statement(found, safety)
    assert result.category == "safety_consistent" and result.registry_affected == 2
    assert found.count == 2


def test_any_grade_counts_as_all_grades():
    quote = "The most common adverse events of any grade were nausea (44%) and anaemia (31%)."
    reading = parse(quote, event(quote, term="nausea", event_scope="all", percent=44))
    assert not reading.rejected and reading.statements[0].event_scope == "all"


def test_serious_is_inherited_from_the_lead_phrase_in_a_list():
    quote = "Serious adverse events were reported in 24% of participants, with the most common being febrile neutropenia (4%)."
    reading = parse(quote, event(quote, term="febrile neutropenia", event_scope="serious", percent=4))
    assert not reading.rejected and reading.statements[0].event_scope == "serious"


def test_serious_is_not_inherited_without_a_list_phrase():
    quote = "Serious adverse events were reported in 24% of participants, and febrile neutropenia occurred in 4%."
    reading = parse(quote, event(quote, term="febrile neutropenia", event_scope="serious", percent=4))
    assert reading.rejected[0].error_code == "event_scope_not_supported"


def test_non_serious_wording_blocks_inherited_serious():
    quote = "Serious events occurred in 5%, whereas the most common non-serious event was rash (9%)."
    reading = parse(quote, event(quote, term="rash", event_scope="serious", percent=9))
    assert reading.rejected[0].error_code == "event_scope_not_supported"


def test_group_serious_count_without_a_cause_is_a_total():
    quote = "Serious adverse events occurred in 29 of 120 participants in the Drug group."
    reading = parse(quote, {"type": "serious", "quote": quote, "count": 29, "denominator": 120, "arm": "Drug group"})
    assert reading.statements[0].detail_level == "total"


def test_death_with_a_cause_stays_a_component():
    quote = "Three patients died, one from pneumonia."
    reading = parse(quote, {"type": "death", "quote": quote, "count": 1, "cause": "pneumonia"})
    assert reading.statements[0].detail_level == "component"


def test_events_for_two_groups_listed_in_different_orders_pass_the_order_check():
    quote = ("The most common events with A were fatigue (33 [11%] of 292), rash (31 [11%]), and with B were rash (35 [12%] of 289), "
             "fatigue (23 [8%]).")
    entries = [event(quote, term="fatigue", arm="A", count=33, percent=11), event(quote, term="rash", arm="A", count=31, percent=11),
               event(quote, term="rash", arm="B", count=35, percent=12), event(quote, term="fatigue", arm="B", count=23, percent=8)]
    reading = parse(quote, *entries)
    assert not [r for r in reading.rejected if r.error_code == "order_does_not_match"] and len(reading.statements) == 4


def test_one_of_two_trials_is_not_a_multi_study_abstract():
    quote = "Participants were already enrolled in one of two trials. Hypotension occurred in 127 of 4518 participants receiving Drug."
    reading = parse(quote, event(quote, term="Hypotension", arm="Drug", count=127, denominator=4518))
    assert not [r for r in reading.rejected if r.error_code == "trial_attribution_unverified"]

"""Proposals cannot silently become approved comparisons or alter participant numbers."""

import copy
import json
from types import SimpleNamespace

import pytest

from trialtrace.config import Settings
from trialtrace.contracts import SafetyArm, SafetyEvent, SafetyEventCount, SafetyExtraction
from trialtrace.safety.safety_compare import compare_statement
from trialtrace.safety.safety_llm import ModelSafetyStatement, safety_input_hash
from trialtrace.safety.safety_assist import (
    apply_review, comparison_for_report, fingerprint, load_report_comparison,
    prepare_packet, review_packet, source_context, suggest_packet,
)

NCT = "NCT90000001"
WINDOW = "Safety was collected for 24 weeks."
ALIAS = "T-DXd refers to Trastuzumab Deruxtecan."


@pytest.fixture
def setup():
    statement = ModelSafetyStatement(kind="serious", arm="T-DXd", arm_in_quote=True, count=2, denominator=100)
    safety = SafetyExtraction(status="ok", time_frame=WINDOW, arms=[SafetyArm(
        nct_id=NCT, content_hash="a" * 64, position=0, group_id="EG0", title="Trastuzumab Deruxtecan",
        serious_affected=2, serious_at_risk=100)])
    abstract = ALIAS + " " + WINDOW + " Serious adverse events occurred in 2 of 100 patients receiving T-DXd."
    context = source_context(NCT, "123", "b" * 64, abstract, statement, safety)
    settings = Settings(_env_file=None, paper_reader_model="test-model")
    answer = {
        "arm": {"candidate_id": "arm:EG0", "abstract_quote": ALIAS,
                "registry_quote": "Trastuzumab Deruxtecan", "reason": "The abstract defines the abbreviation."},
        "event": {"candidate_id": None, "reason": "This is not a named event."},
        "window": {"assessment": "same", "abstract_quote": WINDOW, "registry_quote": WINDOW,
                   "reason": "Both explicitly describe collection over 24 weeks."}}
    return context, settings, answer


class FakeClient:
    def __init__(self, answer):
        self.answer, self.calls = answer, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.calls.append(request)
        return SimpleNamespace(model="test-model", choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(self.answer)))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10))


def proposed(setup):
    context, settings, answer = setup
    return suggest_packet(prepare_packet(context, settings), settings, client=FakeClient(answer))


def save_packet(tmp_path, packet):
    path = tmp_path / NCT / "proposal.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(packet), encoding="utf-8")
    return path


def test_evidence_checked_suggestion_does_not_change_report_without_review(setup, tmp_path):
    packet = proposed(setup)
    assert packet["status"] == "ready"
    save_packet(tmp_path, packet)
    result = load_report_comparison(tmp_path, setup[0])
    assert result.category == "safety_unclear" and result.reason_code == "group_name_unresolved"


@pytest.mark.parametrize("change", ["unknown_id", "wrong_kind", "invented_quote", "invented_registry_quote", "count_override"])
def test_invalid_proposals_are_kept_as_failures(setup, change):
    context, settings, answer = setup
    if change == "unknown_id":
        answer["arm"]["candidate_id"] = "arm:invented"
    elif change == "wrong_kind":
        answer["event"] = answer["arm"]
    elif change == "invented_quote":
        answer["arm"]["abstract_quote"] = "An invented explanation."
    elif change == "invented_registry_quote":
        answer["arm"]["registry_quote"] = "An invented registry group."
    else:
        answer["count"] = 0
    packet = suggest_packet(prepare_packet(context, settings), settings, FakeClient(answer))
    assert packet["status"] == "failed" and packet["error_code"]
    assert packet["llm"]["text"]  # The rejected raw answer is retained.


def test_registry_numbers_are_not_sent_for_name_matching(setup):
    context, settings, answer = setup
    client = FakeClient(answer)
    suggest_packet(prepare_packet(context, settings), settings, client)
    prompt = client.calls[0]["messages"][0]["content"]
    assert "serious_affected" not in prompt and "serious_at_risk" not in prompt
    assert "baseline" not in prompt.lower()


def test_review_does_not_bypass_unverified_scope_and_is_append_only(setup, tmp_path):
    packet = proposed(setup)
    path = save_packet(tmp_path, packet)
    original = path.read_bytes()
    review_packet(path, "Test reviewer", "approved", "Abbreviation checked.")
    uncertain = load_report_comparison(tmp_path, setup[0])
    assert uncertain.category == "safety_unclear"
    assert uncertain.reason_code == "comparability_unverified"
    review_packet(path, "Test reviewer", "approved", "Both populations and collection periods checked.",
                  window="same", population="same")
    confirmed = load_report_comparison(tmp_path, setup[0])
    assert confirmed.category == "safety_consistent"
    assert confirmed.paper_arm == "T-DXd" and confirmed.paper_count == 2
    assert confirmed.registry_arm == "Trastuzumab Deruxtecan"
    assert path.read_bytes() == original
    assert len(path.with_suffix(".reviews.jsonl").read_text().splitlines()) == 2


def test_later_rejection_revokes_an_approved_mapping(setup, tmp_path):
    path = save_packet(tmp_path, proposed(setup))
    review_packet(path, "Test reviewer", "approved", "Checked.", "same", "same")
    assert load_report_comparison(tmp_path, setup[0]).category == "safety_consistent"
    review_packet(path, "Test reviewer", "rejected", "Population attribution needs more evidence.")
    assert load_report_comparison(tmp_path, setup[0]).category == "safety_unclear"


def test_stale_abstract_or_registry_invalidates_approval(setup, tmp_path):
    packet = proposed(setup)
    path = save_packet(tmp_path, packet)
    review = review_packet(path, "Test reviewer", "approved", "Checked.", "same", "same")
    changed = copy.deepcopy(setup[0])
    changed["registry"]["arms"][0]["serious_affected"] = 3
    assert apply_review(changed, packet, review).category == "safety_unclear"
    changed = copy.deepcopy(setup[0])
    changed["abstract"] += " New abstract version."
    assert load_report_comparison(tmp_path, changed).category == "safety_unclear"


def test_changed_candidate_catalog_cannot_be_approved(setup, tmp_path):
    packet = proposed(setup)
    packet["candidates"][0]["title"] = "Invented group"
    path = save_packet(tmp_path, packet)
    with pytest.raises(ValueError, match="candidate_list_changed"):
        review_packet(path, "Test reviewer", "approved", "Checked.")


def test_different_reviewed_window_blocks_agreement(setup, tmp_path):
    path = save_packet(tmp_path, proposed(setup))
    review_packet(path, "Test reviewer", "approved", "The quoted periods use different anchors.", "different", "same")
    result = load_report_comparison(tmp_path, setup[0])
    assert result.category == "safety_not_comparable" and result.reason_code == "reviewed_scope_differs"


def test_same_window_needs_quotes_from_both_sources(setup):
    context, settings, answer = setup
    answer["window"]["registry_quote"] = "invented collection period"
    result = suggest_packet(prepare_packet(context, settings), settings, FakeClient(answer))
    assert result["status"] == "failed"


def test_explicit_abstention_cannot_be_approved(setup, tmp_path):
    context, settings, answer = setup
    answer["arm"] = {"candidate_id": None, "reason": "Insufficient evidence."}
    answer["window"] = {"assessment": "unknown", "reason": "Window not established."}
    packet = suggest_packet(prepare_packet(context, settings), settings, FakeClient(answer))
    assert packet["status"] == "abstained"
    with pytest.raises(ValueError):
        review_packet(save_packet(tmp_path, packet), "Test reviewer", "approved", "Checked.")


def test_event_alias_cannot_add_serious_and_non_serious_rows(setup, tmp_path):
    context, settings, _ = setup
    statement = ModelSafetyStatement(kind="event", term="Queasiness", arm="Trastuzumab Deruxtecan",
                                    arm_in_quote=True, count=20, denominator=100)
    safety = SafetyExtraction(**context["registry"])
    safety.events = [SafetyEvent(nct_id=NCT, content_hash="a" * 64, kind=kind, position=0,
                                term="Nausea", counts=[SafetyEventCount(group_id="EG0", affected=n, at_risk=100)])
                     for kind, n in [("serious", 2), ("other", 20)]]
    abstract = "Queasiness affected 20 of 100 patients receiving Trastuzumab Deruxtecan."
    context = source_context(NCT, "123", "b" * 64, abstract, statement, safety)
    answer = {"arm": {"reason": "Group already matches."},
              "event": {"candidate_id": "event:other:0", "abstract_quote": abstract,
                        "registry_quote": "Nausea", "reason": "Proposed equivalent name for review."},
              "window": {"assessment": "unknown", "reason": "No collection period."}}
    packet = suggest_packet(prepare_packet(context, settings), settings, FakeClient(answer))
    path = save_packet(tmp_path, packet)
    assert packet["status"] == "abstained"
    assert packet["proposal"]["event"]["candidate_id"] is None
    assert "event_seriousness_ambiguous" in packet["proposal"]["event"]["reason"]
    with pytest.raises(ValueError, match="Only an evidence-checked"):
        review_packet(path, "Test reviewer", "approved", "Do not narrow broad events.")


def test_equal_numbers_alone_do_not_create_a_confirmed_report(setup):
    context, _, _ = setup
    statement = ModelSafetyStatement(**context["statement"])
    statement.arm = "Trastuzumab Deruxtecan"
    result = comparison_for_report(statement, SafetyExtraction(**context["registry"]))
    assert result.category == "safety_unclear" and result.reason_code == "comparability_unverified"
    assert result.registry_values


def test_target_study_is_part_of_the_extraction_cache_key():
    info = {"model": "test", "temperature": None, "prompt_hash": "x", "schema_version": "v3"}
    assert safety_input_hash("same text", info, NCT) != safety_input_hash("same text", info, "NCT90000002")


def test_reviewed_window_mismatch_precedes_an_unresolved_group(setup, tmp_path):
    context, settings, answer = setup
    answer["arm"] = {"candidate_id": None, "reason": "Cannot establish the treatment group."}
    packet = suggest_packet(prepare_packet(context, settings), settings, FakeClient(answer))
    path = save_packet(tmp_path, packet)
    review_packet(path, "Test reviewer", "approved", "Different collection anchors.", "different", "unknown")
    assert load_report_comparison(tmp_path, context).category == "safety_not_comparable"


def test_cli_makes_baseline_opt_in(monkeypatch):
    from trialtrace import cli
    calls = []
    monkeypatch.setattr(cli, "run_read_safety", lambda *args: calls.append(args))
    cli.main(["read-safety", NCT])
    assert calls[-1][-1] is False
    cli.main(["read-safety", NCT, "--with-baseline"])
    assert calls[-1][-1] is True


# --- found in the safety v2 audit --------------------------------------------------------------------------

def test_a_window_claim_from_an_analysis_time_point_is_set_to_unknown(setup):
    context, settings, answer = setup
    context = copy.deepcopy(context)
    context["abstract"] += " Analysis of pre-specified 2-year clinical outcomes was performed."
    answer["window"]["abstract_quote"] = "Analysis of pre-specified 2-year clinical outcomes was performed."
    answer["window"]["assessment"] = "different"
    packet = suggest_packet(prepare_packet(context, settings), settings, FakeClient(answer))
    window = packet["proposal"]["window"]
    assert packet["status"] == "ready"                              # the group match is still usable
    assert window["assessment"] == "unknown" and window["reason"].startswith("Set to unknown by code")


def test_a_window_claim_with_no_length_of_time_is_set_to_unknown(setup):
    context, settings, answer = setup
    context = copy.deepcopy(context)
    vague = "Serious bleeding during the treatment period occurred in 10 patients."
    context["abstract"] += " " + vague
    answer["window"]["abstract_quote"] = vague
    answer["window"]["assessment"] = "different"
    packet = suggest_packet(prepare_packet(context, settings), settings, FakeClient(answer))
    assert packet["proposal"]["window"]["assessment"] == "unknown"


def test_a_window_claim_about_safety_collection_is_kept(setup):
    packet = proposed(setup)                                        # "Safety was collected for 24 weeks."
    assert packet["proposal"]["window"]["assessment"] == "same"


def test_a_role_name_is_matched_by_the_sentence_that_defines_the_group(setup):
    context, settings, answer = setup
    statement = ModelSafetyStatement(kind="serious", arm="control group", arm_in_quote=True, count=2, denominator=100)
    safety = SafetyExtraction(**context["registry"])
    defining = "Patients were randomly assigned to peginterferon or no therapy."
    abstract = defining + " Serious adverse events occurred in 2 of 100 patients in the control group."
    context = source_context(NCT, "123", "b" * 64, abstract, statement, safety)
    answer["arm"]["abstract_quote"] = defining                      # it does not contain the word "control"
    answer["window"] = {"assessment": "unknown", "reason": "The abstract states no collection period."}
    packet = suggest_packet(prepare_packet(context, settings), settings, FakeClient(answer))
    assert packet["status"] == "ready" and packet["proposal"]["arm"]["candidate_id"] == "arm:EG0"


def test_a_treatment_name_must_still_be_in_the_evidence(setup):
    context, settings, answer = setup
    answer["arm"]["abstract_quote"] = WINDOW                        # a real sentence, but it does not name T-DXd
    packet = suggest_packet(prepare_packet(context, settings), settings, FakeClient(answer))
    assert packet["status"] == "failed" and packet["error_code"] == "source_label_not_in_evidence"

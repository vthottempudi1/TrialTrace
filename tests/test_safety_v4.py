"""New validation must keep source support separate and never invent reviews."""
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals import run_safety_v4 as runner
from evals.prepare_safety_v4 import challenge_cases, lock_candidate
from evals.safety_v4_reference import ReferenceCase, ReferenceStatement, pair_statements, validate_case
from evals.score_safety_v4 import score
from trialtrace.compare.abstract_baseline import TextQuote
from trialtrace.safety.safety_llm import ModelSafetyReading, ModelSafetyStatement
from trialtrace.config import Settings
from trialtrace.contracts import SafetyArm, SafetyExtraction
from trialtrace.safety.safety_assist import Proposal, Match, Window

QUOTE = "Serious adverse events affected 2 of 100 participants receiving Drug."


def gold(**updates):
    data = dict(sentence=1, quote=QUOTE, kind="serious", term=None, arm="Drug", population=None,
        count=2, denominator=100, percent=None, grade=None, treatment_related=False, cause=None,
        all_cause=False, measure="participant_proportion", event_scope="unspecified", detail_level="unspecified",
        claim_family="serious-drug", attribution="target", category="safety_unclear",
        acceptable_arm_ids=["arm:EG0"], acceptable_event_ids=[], window="unknown", population_relation="unknown",
        abstract_window_quote=None, registry_window_quote=None)
    data.update(updates)
    return ReferenceStatement(**data)


def predicted(**updates):
    data = dict(kind="serious", arm="Drug", arm_in_quote=True, count=2, denominator=100,
                sentence=1, quote=TextQuote(text=QUOTE, start=0, end=len(QUOTE)))
    data.update(updates)
    return ModelSafetyStatement(**data)


@pytest.fixture
def packet(tmp_path):
    root = tmp_path / "evaluation"
    (root / "naturalistic").mkdir(parents=True)
    (root / "targeted").mkdir()
    lock_candidate(root)
    all_cases = []
    hashes = {}
    for trial in range(2):
        nct = f"NCT9600000{trial}"
        directory = root / "naturalistic" / nct
        directory.mkdir()
        safety = SafetyExtraction(status="ok", arms=[SafetyArm(nct_id=nct, content_hash="a"*64,
            group_id="EG0", position=0, title="Drug", serious_affected=2, serious_at_risk=100)])
        cases = [{"case_id": f"{nct}-{i}", "nct_id": nct, "pmid": str(1+trial*10+i), "title": "Test",
            "abstract": QUOTE, "abstract_sha256": hashlib.sha256(QUOTE.encode()).hexdigest(),
            "paper_raw_hash": "b"*64, "registry_hash": "a"*64, "registry_title": "Test",
            "strict_holdout": True, "synthetic": False} for i in range(10)]
        runner.write_json(directory / "cases.json", cases)
        runner.write_json(directory / "safety.json", safety.model_dump(mode="json"))
        runner.write_json(directory / "selection.json", {"selected_pmids": [c["pmid"] for c in cases]})
        refs = [ReferenceCase(case_id=c["case_id"], document="trial_results", statements=[gold()]) for c in cases]
        (directory / "references.jsonl").write_text("\n".join(r.model_dump_json() for r in refs), encoding="utf-8")
        hashes.update({str(p.relative_to(root)): runner.digest(p) for p in directory.glob("*.json")})
        all_cases += cases
    runner.write_json(root / "manifest.json", {"naturalistic_cases": 20, "targeted_cases": 0,
        "input_hashes": hashes, "reserve_trials": [], "selected_trials": ["NCT96000000", "NCT96000001"]})
    runner.write_json(root / "blind_subset.json", {"case_ids": [c["case_id"] for c in all_cases]})
    runner.write_json(root / "reference_review.json", {"reviewer": "Test reviewer", "completed": True,
        "reviewed_at": "2026-11-12", "blind_subset_completed": True,
        "blind_subset_labelled_before_ai_drafts": True, "blind_subset_reviewer": "Independent test reviewer",
        "blind_subset_reviewed_at": "2026-11-12"})
    return root


def test_unlabelled_reference_cannot_validate():
    with pytest.raises(ValidationError):
        ReferenceCase(case_id="x", document=None, statements=None)


def test_independent_review_is_required_in_addition_to_reference_acceptance(packet):
    review = runner.read_json(packet / "reference_review.json")
    review["blind_subset_labelled_before_ai_drafts"] = False
    runner.write_json(packet / "reference_review.json", review)
    with pytest.raises(ValueError, match="independent blind"):
        runner.items(packet, require_review=True)


def test_source_fingerprints_are_checked_before_labels(packet):
    path = next((packet / "naturalistic").glob("*/cases.json"))
    path.write_text("[]")
    with pytest.raises(ValueError, match="Prepared source changed"):
        runner.items(packet, require_labels=False)


def test_candidate_cannot_be_tuned_after_new_references_are_seen(packet):
    candidate = runner.read_json(packet / "system_candidate.json")
    candidate["reader"]["prompt_hash"] = "changed-after-labels"
    runner.write_json(packet / "system_candidate.json", candidate)
    with pytest.raises(ValueError, match="pre-label candidate lock"):
        runner.items(packet, require_labels=False)


def test_numeric_verdict_cannot_use_unknown_scope():
    g = gold(category="safety_consistent")
    safety = SafetyExtraction(status="ok", arms=[])
    reference = ReferenceCase(case_id="x", document="trial_results", statements=[g])
    with pytest.raises(ValueError):
        validate_case(reference, {"case_id": "x", "abstract": QUOTE}, safety)


def test_equal_denominators_do_not_prove_population_identity():
    g = gold(population_relation="same")
    safety = SafetyExtraction(status="ok", arms=[SafetyArm(nct_id="NCT96000000", content_hash="a"*64,
        group_id="EG0", position=0, title="Drug", description="All treated participants")])
    reference = ReferenceCase(case_id="x", document="trial_results", statements=[g])
    with pytest.raises(ValueError, match="population relation requires exact quotes"):
        validate_case(reference, {"case_id": "x", "abstract": QUOTE}, safety)


def test_maximum_one_to_one_matching_does_not_lose_an_available_match():
    labels = [gold(kind="event", term="nausea", aliases={"term": ["vomiting"]}), gold(kind="event", term="vomiting")]
    statements = [predicted(kind="event", term="vomiting"), predicted(kind="event", term="nausea")]
    result = pair_statements(labels, statements)
    assert len(result) == 2 and len(set(result.values())) == 2 and result[1] == 0


def test_source_supported_other_trial_statement_is_a_target_error():
    case = {"case_id": "x"}
    reference = ReferenceCase(case_id="x", document="trial_results", statements=[gold(attribution="other", category=None)])
    st = predicted().model_dump(mode="json")
    output = {"x": {"source_reading": {"statements": [st]}, "reading": {"statements": [st], "status": "ok", "rejected": []},
                    "comparisons": [{"category": "safety_consistent"}], "helpers": {}}}
    result = score([{"case": case, "reference": reference}], output)
    assert result["supported_source_predictions"] == 1
    assert result["target_matches"] == 0 and result["accepted_target_predictions"] == 1
    assert result["false_consistent"] == 1 and result["targets"]["zero_false_consistent"] == "not met"


def test_a_quoted_number_with_wrong_arm_is_not_fully_source_supported():
    reference = ReferenceCase(case_id="x", document="trial_results", statements=[gold()])
    st = predicted(arm="Placebo").model_dump(mode="json")
    output = {"x": {"source_reading": {"statements": [st]}, "reading": {"statements": [st], "status": "ok", "rejected": []},
                    "comparisons": [{"category": "safety_unclear"}], "helpers": {}}}
    result = score([{"case": {"case_id": "x"}, "reference": reference}], output)
    assert result["source_inventory_matches"] == 1
    assert result["supported_source_predictions"] == 0 and result["all_fields_correct"] == 0


def test_constructed_sets_have_enough_numeric_cases_and_no_expected_answers():
    cases = challenge_cases()
    assert len(cases) == 40
    assert sum(c["family"] == "agreement" for c, _ in cases) == 10
    assert sum(c["family"] == "disagreement" for c, _ in cases) == 10
    assert all(c["synthetic"] and not c["strict_holdout"] and "expected" not in c for c, _ in cases)


def test_frozen_fake_run_only_runs_once_and_never_approves_matches(packet, monkeypatch):
    calls = []
    settings = Settings(_env_file=None, OPENAI_API_KEY="not-a-real-key")
    monkeypatch.setattr(runner, "Settings", lambda: settings)
    monkeypatch.setattr(runner, "check_unseen", lambda data: None)

    def fake_read(paper, settings, target_nct_id):
        calls.append(paper.pmid)
        return ModelSafetyReading(pmid=paper.pmid, status="ok", document="trial_results", statements=[predicted()])

    def fake_helper(packet, settings):
        proposal = Proposal(arm=Match(reason="Abstain"), event=Match(reason="No named event"), window=Window(reason="Unknown"))
        return {**packet, "status": "abstained", "proposal": proposal.model_dump()}

    monkeypatch.setattr(runner, "read_safety_with_llm", fake_read)
    monkeypatch.setattr(runner, "suggest_packet", fake_helper)
    runner.freeze(packet)
    runner.run(packet)
    result = runner.read_json(packet / "run/scores.json")
    assert len(calls) == 20 and result["naturalistic_strict"]["target_matches"] == 20
    assert result["naturalistic_strict"]["verified_comparisons"] == 0
    with pytest.raises(FileExistsError):
        runner.run(packet)
    assert len(calls) == 20
    runner.score_reviewed(packet)
    reviewed = runner.read_json(next((packet / "run").glob("reviewed_scores-*.json")))
    assert not reviewed["actual_review_files"]
    assert reviewed["naturalistic_strict"]["verified_comparisons"] == 0

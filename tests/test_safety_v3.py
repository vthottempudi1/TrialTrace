import pytest

from evals import run_safety_v3 as evaluation
from trialtrace.safety.safety_llm import ModelSafetyStatement


def test_measure_scoring_catches_an_estimate_reported_as_a_raw_proportion():
    label = {"pmid": "1", "kind": "death", "sentence": 1, "count": None, "percent": 10}
    data = {"NCT12345678": {"cases": [{"pmid": "1", "strict_holdout": True}],
        "labels": [label], "mappings": [{}], "measure_references": [{"measure": "time_to_event"}]}}
    statement = ModelSafetyStatement(kind="death", sentence=1, percent=10)
    outputs = {"NCT12345678:1": {"reading": {"statements": [statement.model_dump()]}}}
    result = evaluation.measure_score(data, outputs, True)
    assert result["measure_correct"] == 0
    assert result["estimates_mistaken_for_raw_proportions"] == 1
    statement.measure = "time_to_event"
    outputs["NCT12345678:1"]["reading"]["statements"] = [statement.model_dump()]
    assert evaluation.measure_score(data, outputs, True)["measure_correct"] == 1


def test_missing_measure_references_block_validation(tmp_path, monkeypatch):
    trial = tmp_path / "trials" / "NCT12345678"
    trial.mkdir(parents=True)
    (trial / "measure_labels.jsonl").write_text("")
    monkeypatch.setattr(evaluation, "_references", lambda *a, **k: (
        {"NCT12345678": {"labels": [{"category": "safety_unclear"}]}}, 1))
    with pytest.raises(ValueError, match="one measure reference"):
        evaluation.references(tmp_path)


def test_known_wrong_proposal_fails_even_with_small_coverage(monkeypatch):
    base = {"helper_correct": 0, "helper_proposals": 1,
            "targets": {"zero_wrong_match_proposals": "insufficient coverage"}}
    monkeypatch.setattr(evaluation, "_score_cohorts", lambda *a: {
        **base, "targets": dict(base["targets"]), "production_coverage": base})
    result = evaluation.score_cohorts({}, {})
    assert result["targets"]["zero_wrong_match_proposals"] == "not met"

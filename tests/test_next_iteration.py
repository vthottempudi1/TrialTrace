"""Attribution, blind sampling and evaluation gates must not manufacture evidence or scores."""

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from evals.audit_registry_wording import wording_pairs
from evals.prepare_safety_v2 import choose_abstracts, choose_trials
from evals.run_safety_v2 import extend, freeze, matches, pair_labels, references, run, score
from trialtrace.safety.safety_llm import ModelSafetyStatement
from trialtrace.contracts import SafetyArm, SafetyExtraction

NCT = "NCT90000001"


@pytest.fixture
def dataset(tmp_path):
    directory = tmp_path / "trials" / NCT
    directory.mkdir(parents=True)
    abstract = "Serious adverse events occurred in 2 of 100 patients receiving Drug X."
    case = {"nct_id": NCT, "pmid": "111", "title": "Trial results", "abstract": abstract,
            "abstract_sha256": hashlib.sha256(abstract.encode()).hexdigest(), "paper_raw_hash": "b" * 64,
            "registry_hash": "a" * 64}
    safety = SafetyExtraction(status="ok", arms=[SafetyArm(nct_id=NCT, content_hash="a" * 64,
        position=0, group_id="EG0", title="Drug X", serious_affected=2, serious_at_risk=100)])
    (directory / "cases.jsonl").write_text(json.dumps(case) + "\n")
    (directory / "safety.json").write_text(safety.model_dump_json())
    (directory / "sheet.md").write_text(abstract)
    (directory / "registry.md").write_text("Drug X")
    (directory / "labels.txt").write_text("111 | 1 | serious | - | Drug X | 2 | 100 | - | - | n | safety_unclear\n")
    mapping = {"label_index": 1, "acceptable_arm_ids": ["arm:EG0"], "acceptable_event_ids": [],
               "window": "unknown", "population": "unknown", "event_aliases": [], "arm_aliases": []}
    (directory / "match_labels.jsonl").write_text(json.dumps(mapping) + "\n")
    (tmp_path / "trials.txt").write_text(NCT)
    (tmp_path / "manifest.json").write_text(json.dumps({"pool_trials": 1, "selected_trials": 1, "per_trial": {NCT: 1}}))
    (tmp_path / "reference_review.json").write_text(json.dumps({"completed": False, "reviewer": None, "reviewed_at": None}))
    return tmp_path


def test_blind_trial_and_abstract_selection_is_content_independent():
    pool = [f"NCT{i:08}" for i in range(60)]
    assert choose_trials(pool) == choose_trials(list(reversed(pool)))
    papers = [{"pmid": str(i), "abstract": "a harm occurred", "status": "listed"} for i in range(10, 20)]
    first = [p["pmid"] for p in choose_abstracts(NCT, papers)]
    for paper in papers:
        paper["abstract"] = "no safety result at all"
    assert first == [p["pmid"] for p in choose_abstracts(NCT, list(reversed(papers)))]


def test_a_wording_candidate_is_not_inferred_across_roles_or_duplicates():
    old = SimpleNamespace(kind="removed", measure_text="Overall survival", before="primary", after=None, evidence_ids=["a"])
    new = SimpleNamespace(kind="added", measure_text="OS", before=None, after="primary", evidence_ids=["b"])
    assert len(wording_pairs(NCT, "registry_versions", [old, new])) == 1
    new.after = "secondary"
    assert wording_pairs(NCT, "registry_versions", [old, new]) == []
    old.kind = new.kind = "ambiguous"
    assert wording_pairs(NCT, "registry_versions", [old, new]) == []


def test_blank_labels_cannot_be_frozen_or_run(dataset):
    (dataset / "trials" / NCT / "labels.txt").write_text("# unlabelled\n")
    with pytest.raises(ValueError, match="every abstract"):
        references(dataset)
    with pytest.raises(ValueError):
        freeze(dataset)
    with pytest.raises(FileNotFoundError):
        run(dataset)
    assert not (dataset / "model_run_started.json").exists()


def test_complete_labels_still_require_actual_reference_review(dataset):
    data, count = references(dataset)
    assert count == 1 and len(data) == 1
    with pytest.raises(ValueError, match="actual independent human review"):
        references(dataset, require_review=True)


def test_known_collection_window_requires_real_source_quotes(dataset):
    path = dataset / "trials" / NCT / "match_labels.jsonl"
    mapping = json.loads(path.read_text())
    mapping["window"] = "same"
    mapping["abstract_window_quote"] = "24 weeks"
    mapping["registry_window_quote"] = "24 weeks"
    path.write_text(json.dumps(mapping))
    with pytest.raises(ValueError, match="exact abstract quote"):
        references(dataset)


def test_equal_counts_cannot_be_gold_consistent_with_unverified_scope(dataset):
    path = dataset / "trials" / NCT / "labels.txt"
    path.write_text(path.read_text().replace("safety_unclear", "safety_consistent"))
    with pytest.raises(ValueError, match="verified scope"):
        references(dataset)


def test_reference_ids_must_exist_in_the_registry(dataset):
    path = dataset / "trials" / NCT / "match_labels.jsonl"
    mapping = json.loads(path.read_text())
    mapping["acceptable_arm_ids"] = ["arm:invented"]
    path.write_text(json.dumps(mapping))
    with pytest.raises(ValueError, match="acceptable_arm_ids"):
        references(dataset)


def test_reference_source_fingerprint_catches_modified_cases(dataset):
    path = dataset / "trials" / NCT / "cases.jsonl"
    relative = path.relative_to(dataset).as_posix()
    manifest = json.loads((dataset / "manifest.json").read_text())
    manifest["source_hashes"] = {relative: hashlib.sha256(path.read_bytes()).hexdigest()}
    (dataset / "manifest.json").write_text(json.dumps(manifest))
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="Prepared source changed"):
        references(dataset)


def test_shared_event_word_does_not_count_as_correct_extraction():
    label = {"kind": "event", "sentence": 1, "term": "postoperative pneumonia", "count": 2, "percent": None}
    mapping = {"event_aliases": []}
    wrong = ModelSafetyStatement(kind="event", sentence=1, term="postoperative respiratory failure", count=2)
    assert not matches(label, mapping, wrong)
    label["term"] = "anaemia"
    mapping["event_aliases"] = ["anemia"]
    assert matches(label, mapping, wrong.model_copy(update={"term": "anemia"}))


def test_statement_matching_remains_one_to_one():
    label = {"kind": "serious", "sentence": 1, "count": 2, "percent": None}
    statement = ModelSafetyStatement(kind="serious", sentence=1, count=2)
    assert pair_labels([label, label], [{}, {}], [statement]) == {0: 0}


def test_absent_predictions_do_not_vacuously_pass_safety_targets(dataset):
    data, _ = references(dataset)
    output = {f"{NCT}:111": {"reading": {"statements": [], "rejected": [], "status": "ok"},
                              "comparisons": [], "helpers": {}}}
    scored = score(data, output)
    assert scored["labels"] == 1 and scored["matched"] == 0
    assert scored["targets"]["zero_false_consistent"] == "not tested"
    assert scored["targets"]["zero_wrong_match_proposals"] == "insufficient coverage"
    assert scored["targets"]["all_differences_routed"] == "insufficient coverage"


def test_same_pmid_for_two_trials_keeps_distinct_results(dataset):
    data, _ = references(dataset)
    other = "NCT90000002"
    data[other] = copy.deepcopy(data[NCT])
    data[other]["cases"][0]["nct_id"] = other
    st = ModelSafetyStatement(kind="serious", sentence=1, count=2, denominator=100, arm="Drug X")
    output = {f"{NCT}:111": {"reading": {"statements": [st.model_dump()], "rejected": [], "status": "ok"},
                              "comparisons": [{"category": "safety_unclear"}], "helpers": {}},
              f"{other}:111": {"reading": {"statements": [], "rejected": [], "status": "ok"},
                                "comparisons": [], "helpers": {}}}
    scored = score(data, output)
    assert scored["labels"] == 2 and scored["matched"] == 1


def test_unmatched_helper_proposals_are_not_excluded_from_error_count(dataset):
    data, _ = references(dataset)
    st = ModelSafetyStatement(kind="death", sentence=1, count=1)
    output = {f"{NCT}:111": {"reading": {"statements": [st.model_dump()], "rejected": [], "status": "ok"},
                "comparisons": [{"category": "safety_unclear"}], "helpers": {"0": {"status": "ready",
                "proposal": {"arm": {"candidate_id": "arm:EG0"}, "event": {"candidate_id": None},
                             "window": {"assessment": "same"}}}}}}
    scored = score(data, output)
    assert scored["helper_proposals"] == 1 and scored["helper_correct"] == 0
    assert scored["helper_window_claims"] == 1 and scored["helper_window_correct"] == 0


def test_a_frozen_set_cannot_be_extended(dataset):
    (dataset / "freeze.json").write_text("{}")
    with pytest.raises(ValueError, match="Cannot extend"):
        extend(dataset)


def test_label_count_extension_preserves_existing_labels_and_saved_order(dataset):
    from trialtrace.contracts import Snapshot
    other = "NCT90000002"
    snapshot = Snapshot(nct_id=other, title="Reserve", overall_status="COMPLETED", content_hash="c" * 64,
                        source_url=f"https://clinicaltrials.gov/study/{other}", raw_file_path="unused",
                        retrieved_at="2026-11-11T00:00:00Z")
    safety = SafetyExtraction(status="ok", arms=[SafetyArm(
        nct_id=other, content_hash="c" * 64, position=0, group_id="EG0", title="Drug Y")])
    reserve = {other: {"snapshot": snapshot.model_dump(mode="json"), "safety": safety.model_dump(mode="json"),
                       "papers": [{"pmid": "222", "title": "Reserve paper", "abstract": "No numerical findings.",
                                   "raw_hash": "d" * 64}]}}
    (dataset / "draw.txt").write_text(NCT + "\n" + other + "\n")
    (dataset / "reserve_inputs.json").write_text(json.dumps(reserve))
    (dataset / "manifest.json").write_text(json.dumps(
        {"pool_trials": 2, "selected_trials": 1, "per_trial": {NCT: 1}}))
    label_path = dataset / "trials" / NCT / "labels.txt"
    original = label_path.read_bytes()
    extend(dataset)
    assert label_path.read_bytes() == original
    assert (dataset / "trials.txt").read_text().split() == [NCT, other]
    manifest = json.loads((dataset / "manifest.json").read_text())
    assert manifest["selected_abstracts"] == 2
    assert manifest["extensions"][0]["prior_statement_count"] == 1
    with pytest.raises(ValueError, match="every abstract"):
        references(dataset)  # New cases must be labeled, never auto-filled.


def test_frozen_fake_run_scores_once_without_approving_proposals(dataset, monkeypatch):
    import evals.run_safety_v2 as module
    from trialtrace.safety.safety_llm import ModelSafetyReading
    from trialtrace.config import Settings
    config = Settings(_env_file=None, OPENAI_API_KEY="test-key")
    monkeypatch.setattr(module, "Settings", lambda: config)
    monkeypatch.setattr(module, "assert_still_unseen", lambda data: None)
    (dataset / "reference_review.json").write_text(json.dumps(
        {"completed": True, "reviewer": "Test reviewer", "reviewed_at": "2026-11-11T00:00:00Z"}))
    calls = []

    def read(paper, reader, target_nct_id):
        calls.append(target_nct_id)
        return ModelSafetyReading(pmid=paper.pmid, status="ok", statements=[ModelSafetyStatement(
            kind="serious", sentence=1, count=2, denominator=100, arm="Drug X", arm_in_quote=True)])

    def suggest(packet, settings):
        return {**packet, "status": "abstained", "proposal": {"arm": {"candidate_id": None},
                "event": {"candidate_id": None}, "window": {"assessment": "unknown"}}}

    monkeypatch.setattr(module, "read_safety_with_llm", read)
    monkeypatch.setattr(module, "suggest_packet", suggest)
    freeze(dataset)
    run(dataset)
    scored = json.loads((dataset / "run" / "scores.json").read_text())
    assert calls == [NCT]
    assert scored["matched"] == 1 and scored["helper_abstained"] == 1
    assert scored["verified_comparisons"] == 0
    assert not list(dataset.rglob("*.reviews.jsonl"))
    with pytest.raises(FileExistsError):
        run(dataset)
    assert calls == [NCT]


@pytest.mark.parametrize("same_id", [True, False])
def test_new_model_exposure_invalidates_a_prepared_holdout(dataset, same_id):
    from evals.run_safety_v2 import assert_still_unseen
    data, _ = references(dataset)
    case = data[NCT]["cases"][0]
    class Connection:
        def execute(self, query):
            return [(case["pmid"] if same_id else "222", case["abstract"])]
    with pytest.raises(ValueError, match="Holdout contamination"):
        assert_still_unseen(data, Connection())


def test_registry_unicode_is_preserved_for_window_evidence(dataset):
    path = dataset / "trials" / NCT / "safety.json"
    safety = SafetyExtraction.model_validate_json(path.read_text())
    safety.time_frame = "24 weeks ? from first dose"
    path.write_text(safety.model_dump_json(), encoding="utf-8")
    data, _ = references(dataset)
    assert data[NCT]["safety"].time_frame == "24 weeks ? from first dose"


def test_evaluation_uses_the_production_ten_paper_selector():
    from trialtrace.match_papers import select_papers
    papers = [{"pmid": str(i), "status": "listed", "abstract": "Current"} for i in range(20, 0, -1)]
    papers.insert(0, {"pmid": "5", "status": "listed", "abstract": "Newest version"})
    papers.append({"pmid": "0", "status": "not_confirmed", "abstract": "Unlinked"})
    papers.append({"pmid": "21", "status": "listed", "abstract": None})
    selected, skipped = select_papers(papers, 10)
    assert [paper["pmid"] for paper in selected] == [str(i) for i in range(1, 11)]
    assert selected[4]["abstract"] == "Newest version"
    assert [paper["pmid"] for paper in skipped] == [str(i) for i in range(11, 21)]
    assert choose_abstracts(NCT, papers) == selected


def test_previously_exposed_cases_do_not_count_in_strict_scores(dataset):
    from evals.run_safety_v2 import score_cohorts
    data, _ = references(dataset)
    other = "NCT90000002"
    data[other] = copy.deepcopy(data[NCT])
    data[other]["cases"][0].update(nct_id=other, pmid="222", strict_holdout=False)
    data[other]["labels"][0]["pmid"] = "222"
    st = ModelSafetyStatement(kind="serious", sentence=1, count=2, denominator=100, arm="Drug X")
    outputs = {f"{NCT}:111": {"reading": {"statements": [st.model_dump()], "rejected": [], "status": "ok"},
                               "comparisons": [{"category": "safety_unclear"}], "helpers": {}},
               f"{other}:222": {"reading": {"statements": [], "rejected": [], "status": "ok"},
                                 "comparisons": [], "helpers": {}}}
    result = score_cohorts(data, outputs)
    assert result["labels"] == result["matched"] == 1
    assert result["production_coverage"]["labels"] == 2
    assert result["cohort_counts"]["fully_unseen_trials"] == 1
    assert result["trial_macro"]["recall"]["trials_counted"] == 1


def test_amendment_refuses_to_replace_human_reference_work(dataset):
    from evals.amend_safety_v2_ten_papers import require_unlabelled
    before = (dataset / "trials" / NCT / "labels.txt").read_bytes()
    with pytest.raises(ValueError, match="Reference work already exists"):
        require_unlabelled(dataset)
    assert (dataset / "trials" / NCT / "labels.txt").read_bytes() == before

"""V3 uses the frozen v2 runner with additional independent measure references.

No implicit model calls: requires --check-labels, --extend, --freeze or --run.
"""
import hashlib
import json
from pathlib import Path

from evals import run_safety_v2 as runner

FOLDER = Path("evals/safety_v3")
_references = runner.references
_score_cohorts = runner.score_cohorts
_source_hashes = runner.source_hashes


def references(folder, require_review=False):
    data, total = _references(folder, require_review=require_review)
    for nct, trial in data.items():
        path = folder / "trials" / nct / "measure_labels.jsonl"
        records = runner.read_jsonl(path)
        if len(records) != len(trial["labels"]):
            raise ValueError(f"{nct}: one measure reference required per statement")
        for i, (record, label) in enumerate(zip(records, trial["labels"]), 1):
            if record.get("label_index") != i or record.get("measure") not in (
                    "participant_proportion", "time_to_event"):
                raise ValueError(f"{nct}: invalid measure reference {i}")
            if record["measure"] == "time_to_event" and label["category"] != "safety_not_comparable":
                raise ValueError(f"{nct}: estimates cannot compare with raw registry counts")
        trial["measure_references"] = records
    return data, total


def source_hashes(folder):
    result = _source_hashes(folder)
    for file in (Path(__file__), Path("evals/prepare_safety_v3.py"), Path("evals/acquire_safety_v3.py")):
        result[file.as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    return result


def measure_score(data, outputs, strict_only):
    correct = matched = estimates = estimates_found = estimates_raw = 0
    for nct, trial in data.items():
        for case in trial["cases"]:
            if strict_only and not case.get("strict_holdout", True):
                continue
            indices = [i for i, label in enumerate(trial["labels"]) if label["pmid"] == case["pmid"]]
            labels = [trial["labels"][i] for i in indices]
            mappings = [trial["mappings"][i] for i in indices]
            gold = [trial["measure_references"][i]["measure"] for i in indices]
            output = outputs[f"{nct}:{case['pmid']}"]
            statements = [runner.ModelSafetyStatement(**s) for s in output["reading"]["statements"]]
            pairs = runner.pair_labels(labels, mappings, statements)
            estimates += gold.count("time_to_event")
            for li, si in pairs.items():
                matched += 1
                correct += statements[si].measure == gold[li]
                if gold[li] == "time_to_event":
                    estimates_found += 1
                    estimates_raw += statements[si].measure != "time_to_event"
    return {"matched": matched, "measure_correct": correct, "gold_estimates": estimates,
            "estimates_extracted": estimates_found, "estimates_mistaken_for_raw_proportions": estimates_raw}


def score_cohorts(data, outputs):
    result = _score_cohorts(data, outputs)
    result["measure_scoring"] = {"strict_unseen": measure_score(data, outputs, True),
                                 "production_coverage": measure_score(data, outputs, False)}
    # An observed wrong proposal already fails a zero-error criterion. Coverage
    # cannot turn that failure into the softer "insufficient coverage" result.
    for score in (result, result["production_coverage"]):
        if score["helper_correct"] < score["helper_proposals"]:
            score["targets"]["zero_wrong_match_proposals"] = "not met"
    return result


def main():
    from evals import prepare_safety_v2 as packet
    original_write = packet.write_trial

    def write_with_measures(folder, nct_id, data, replace_sources=False):
        count = original_write(folder, nct_id, data, replace_sources)
        (folder / "measure_labels.jsonl").write_text(
            '# One object per statement: {"label_index":1,"measure":"participant_proportion"}\n'
            '# Use time_to_event for an explicitly reported time-to-event estimate.\n', encoding="utf-8")
        return count

    packet.write_trial = write_with_measures
    runner.FOLDER = FOLDER
    runner.references = references
    runner.source_hashes = source_hashes
    runner.score_cohorts = score_cohorts
    runner.main()


if __name__ == "__main__":
    main()

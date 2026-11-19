"""Replay stored answers through repaired guards; development evidence, no API.

Writes a new directory. Never changes frozen references, scores or raw answers.
"""
import hashlib
import json
import argparse
from pathlib import Path

from evals.run_safety_v3 import references
from evals.run_safety_v2 import pair_labels
from trialtrace.safety.safety_llm import ModelSafetyStatement, reading_from_answer
from trialtrace.safety.safety_identity import check_reading_identity
from trialtrace.contracts import SafetyExtraction
from trialtrace.safety.safety_assist import Proposal, event_match_block_reason, candidates_for, source_context

SOURCE = Path("evals/safety_v3")
OUTPUT = Path("evals/development/safety_v3_guard_replay_v6")


def main(output=OUTPUT):
    data, _ = references(SOURCE, require_review=True)
    output.mkdir(parents=True, exist_ok=False)
    before = after = recovered = lost = blocked_helpers = 0
    case_results, inputs = [], {}
    for nct, trial in data.items():
        for case in trial["cases"]:
            path = SOURCE / "run" / f"{nct}-{case['pmid']}.json"
            inputs[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            old = json.loads(path.read_text(encoding="utf-8"))
            new = reading_from_answer(case["pmid"], case["abstract"], old["reading"]["llm"]["text"], target_nct_id=nct)
            new = check_reading_identity(new, case["abstract"], trial["safety"])
            indices = [i for i, label in enumerate(trial["labels"]) if label["pmid"] == case["pmid"]]
            labels = [trial["labels"][i] for i in indices]
            maps = [trial["mappings"][i] for i in indices]
            original = [ModelSafetyStatement(**s) for s in old["reading"]["statements"]]
            before_pairs = pair_labels(labels, maps, original)
            after_pairs = pair_labels(labels, maps, new.statements)
            if case.get("strict_holdout", True):
                before += len(before_pairs)
                after += len(after_pairs)
                recovered += len(set(after_pairs) - set(before_pairs))
                lost += len(set(before_pairs) - set(after_pairs))
            # Validate original helper proposals against scope safeguards only.
            # Do not invent a new model answer or reuse a stale approval.
            blocks = []
            for si, packet in old["helpers"].items():
                if packet.get("status") not in ("ready", "abstained"):
                    continue
                statement = original[int(si)]
                context = source_context(nct, case["pmid"], case["paper_raw_hash"], case["abstract"], statement, trial["safety"])
                reason = event_match_block_reason(Proposal(**packet["proposal"]), context,
                                                 candidates_for(statement, trial["safety"])[0])
                if reason:
                    blocks.append({"statement_index": int(si), "reason": reason})
            blocked_helpers += len(blocks)
            case_results.append({"nct_id": nct, "pmid": case["pmid"], "reading": new.model_dump(mode="json"),
                                 "blocked_original_helper_proposals": blocks,
                                 "recovered_label_indices": sorted(set(after_pairs) - set(before_pairs)),
                                 "lost_label_indices": sorted(set(before_pairs) - set(after_pairs))})
    summary = {"scope": "Stored-answer guard replay on development data, not a new model evaluation",
               "model_calls": 0, "strict_matched_before": before, "strict_matched_after": after,
               "previously_missed_recovered": recovered, "previously_matched_lost": lost,
               "original_helper_proposals_blocked": blocked_helpers,
               "cases": case_results, "source_answer_hashes": inputs,
               "reader_source_hash": hashlib.sha256(Path("trialtrace/safety/safety_llm.py").read_bytes()).hexdigest()}
    (output / "results.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("cases", "source_answer_hashes")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    main(parser.parse_args().output)

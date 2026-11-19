"""Replay stored v4 answers through the current guards. Development evidence only: no model calls, frozen files untouched."""
import json
import sys
from pathlib import Path

from evals.run_safety_v4 import items, score_sets, read_json, write_json
from trialtrace.safety.safety_identity import check_reading_identity
from trialtrace.safety.safety_llm import reading_from_answer
from trialtrace.safety.safety_assist import comparison_for_report

SOURCE = Path("evals/safety_v4")


def replay(output_name, source=SOURCE):
    data = items(source, require_review=True, check_lock=False)
    old_outputs, new_outputs = {}, {}
    for item in data:
        case, safety = item["case"], item["safety"]
        old = read_json(source / "run" / (case["case_id"] + ".json"))
        old_outputs[case["case_id"]] = old
        raw = reading_from_answer(case["pmid"], case["abstract"], old["source_reading"]["llm"]["text"], target_nct_id=case["nct_id"])
        reading = check_reading_identity(raw, case["abstract"], safety, case["registry_title"])
        # Keep old helper answers only for statements that existed before; new statements get no helper here.
        new_outputs[case["case_id"]] = {"source_reading": raw.model_dump(mode="json"), "reading": reading.model_dump(mode="json"), "helpers": {},
            "comparisons": [comparison_for_report(s, safety).model_dump(mode="json") for s in reading.statements]}
    before = score_sets(data, old_outputs)
    after = score_sets(data, new_outputs)
    out = Path("evals/development") / output_name
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "results.json", {"before": before["naturalistic_strict"], "after": after["naturalistic_strict"],
                                      "constructed_before": before["targeted_constructed"], "constructed_after": after["targeted_constructed"]})
    for name in ("naturalistic_strict", "targeted_constructed"):
        for label, result in (("before", before), ("after", after)):
            r = result[name]
            print(name, label, {k: r[k] for k in r if k in ("target_labels", "target_matches", "accepted_target_predictions", "all_fields_agree")})


if __name__ == "__main__":
    replay(sys.argv[1], Path(sys.argv[2]) if len(sys.argv) > 2 else SOURCE)

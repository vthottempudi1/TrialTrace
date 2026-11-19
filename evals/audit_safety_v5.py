"""Post-run error audit for v5: misses, extras (split by whether a non-target reference covers them). Reads saved answers only."""
import json
from pathlib import Path

from evals.run_safety_v4 import items, read_json, strict
from evals.safety_v4_reference import pair_statements
from trialtrace.safety.safety_llm import ModelSafetyStatement

FOLDER = Path("evals/safety_v5")


def main():
    data = items(FOLDER, require_review=True, check_lock=False)
    audit = {"misses": [], "extras_with_non_target_reference": [], "extras_without_reference": [], "rejected": []}
    for item in strict(data):
        case, reference = item["case"], item["reference"]
        out = read_json(FOLDER / "run" / (case["case_id"] + ".json"))
        accepted = [ModelSafetyStatement(**s) for s in out["reading"]["statements"]]
        target = [s for s in reference.statements if s.attribution == "target"]
        pairs = pair_statements(target, accepted)
        matched = set(pairs.values())
        all_pairs = pair_statements(reference.statements, accepted)
        by_pred = {si: gi for gi, si in all_pairs.items()}
        for gi, gold in enumerate(target):
            if gi not in pairs:
                audit["misses"].append({"case": case["case_id"], "quote": gold.quote[:200], "kind": gold.kind, "term": gold.term, "arm": gold.arm, "count": gold.count, "percent": gold.percent})
        for si, st in enumerate(accepted):
            if si in matched:
                continue
            row = {"case": case["case_id"], "quote": st.quote.text[:200], "kind": st.kind, "term": st.term, "arm": st.arm, "count": st.count, "percent": st.percent}
            if si in by_pred:
                row["reference_attribution"] = reference.statements[by_pred[si]].attribution
                audit["extras_with_non_target_reference"].append(row)
            else:
                audit["extras_without_reference"].append(row)
        for r in out["reading"]["rejected"]:
            audit["rejected"].append({"case": case["case_id"], "code": r.get("error_code"), "quote": (r.get("quote") or {}).get("text", "")[:150]})
    (FOLDER / "run" / "error_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print({k: len(v) for k, v in audit.items()})


if __name__ == "__main__":
    main()

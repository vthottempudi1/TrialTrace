##############################################################################
# evals/venu_unclear_v1.py
#
# PURPOSE: Second part of Venu's blind subset for validation v1 (rule: evals/validation_v1/RULE.md): up to 20 pairs
#          that Claude's draft labelled `unclear`, in the same hash order as the first 30. The sheet shows NO label,
#          so Venu cannot tell which pairs these are. Pairs already in the first 30 are skipped.
#          Writes venu_subset/pairs_unclear.json, VENU_SHEET_2.md and venu_labels_2.txt. Refuses to overwrite.
#
# USE:     python -m evals.venu_unclear_v1
##############################################################################

import json

from evals.prepare_v3 import spans
from evals.run_multipaper import read_cases, read_labels
from evals.venu_subset_v1 import FOLDER, order_key

SIZE = 20


def main() -> None:
    out = FOLDER / "venu_subset"
    if (out / "pairs_unclear.json").exists():
        raise SystemExit("pairs_unclear.json already exists; the second subset is fixed.")
    first = {(p["nct_id"], p["case_id"]) for p in json.loads((out / "pairs.json").read_text(encoding="utf-8"))}
    candidates = []
    for trial_folder in sorted((FOLDER / "natural").iterdir()):
        labels = read_labels(trial_folder / "labels.txt")
        for case in read_cases(trial_folder / "cases.unlabelled.jsonl").values():
            if labels[case["case_id"]]["answer"] == "unclear" and (case["nct_id"], case["case_id"]) not in first:
                candidates.append(case)
    candidates.sort(key=lambda case: order_key(case["nct_id"], case["case_id"]))
    chosen = candidates[:SIZE]

    (out / "pairs_unclear.json").write_text(json.dumps([{"nct_id": c["nct_id"], "case_id": c["case_id"]} for c in chosen], indent=2), encoding="utf-8")
    lines = ["# Validation v1: blind sheet for Venu, part 2 (%d pairs)" % len(chosen), "",
             "Same task and rules as part 1: decide yes / no / unclear for the registered outcome. No label and no model answer is shown.", ""]
    for number, case in enumerate(chosen, start=1):
        registered = case["registered"]
        lines += [f"## {number}. {case['nct_id']} / {case['case_id']}", f"Registered outcome ({registered['role']}): {registered['measure_text']}",
                  f"Time frame: {registered['time_frame'] or 'not given'}", ""]
        for sentence_number, (start, end) in enumerate(spans(case["abstract"]), start=1):
            lines.append(f"[{sentence_number}] {case['abstract'][start:end]}")
        lines.append("")
    (out / "VENU_SHEET_2.md").write_text("\n".join(lines), encoding="utf-8")
    template = ["# One line per pair: nct_id/case_id | answer | role | sentence numbers | note",
                "#   answer: yes / no / unclear      role (only if yes): primary / secondary / not_stated",
                "# Replace each '?' ; keep the | separators."]
    template += [f"{c['nct_id']}/{c['case_id']} | ? | | | " for c in chosen]
    (out / "venu_labels_2.txt").write_text("\n".join(template) + "\n", encoding="utf-8")
    print(f"{len(chosen)} pairs from {len({c['nct_id'] for c in chosen})} trials (of {len(candidates)} unclear candidates)")


if __name__ == "__main__":
    main()

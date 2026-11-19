##############################################################################
# evals/make_spot_check.py
#
# PURPOSE: Pick 30 pairs from the final evaluation for a HUMAN to label blind, so the model-drafted labels can
#          be checked. Stratified by the drafted label (10 yes, 10 unclear, 10 no), seeded, written WITHOUT the
#          drafted label. Output in evals/final/spot_check/: sheet.md (read), labels.txt (fill in), pick.json
#          (which pairs, kept for the comparison later). Refuses to overwrite.
#
# USE:     python -m evals.make_spot_check
##############################################################################

import json
import random
from pathlib import Path

from evals.prepare_v3 import spans
from evals.run_multipaper import read_cases, read_labels

TRIALS = Path(__file__).parent / "final" / "trials"
OUT = Path(__file__).parent / "final" / "spot_check"
SEED = 20261111
PER_ANSWER = 10


def main() -> None:
    if OUT.exists():
        raise SystemExit(f"{OUT} already exists; the pick is fixed.")
    pairs = []
    for folder in sorted(TRIALS.iterdir()):
        labels = read_labels(folder / "labels.txt")
        cases = read_cases(folder / "cases.unlabelled.jsonl")
        for case_id, case in cases.items():
            pairs.append({"id": f"{folder.name}:{case_id}", "answer": labels[case_id]["answer"], "case": case})

    random.seed(SEED)
    chosen = []
    for answer in ("yes", "unclear", "no"):
        group = [pair for pair in pairs if pair["answer"] == answer]
        chosen.extend(random.sample(group, PER_ANSWER))
    random.shuffle(chosen)                                  # the order must not reveal the drafted label

    OUT.mkdir(parents=True)
    lines = ["# Spot-check sheet (30 pairs, drafted labels hidden)", "",
             "Read the abstract only. Same rules as before: yes = the registered outcome's RESULT is reported;",
             "no = not in the abstract; unclear = narrower, pooled, qualitative without a number or test, or a different time point.",
             "Fill in labels.txt (not this file).", ""]
    template = ["# One line per pair: id | answer | role | sentence numbers | note   (same format as before)",
                "# answer: yes / no / unclear;  role and sentences only for yes (role: primary / secondary / not_stated)"]
    for number, pair in enumerate(chosen, start=1):
        case = pair["case"]
        registered = case["registered"]
        lines.append(f"## {number}. {pair['id']}")
        lines.append(f"Outcome ({registered['role']}): {registered['measure_text']}")
        lines.append(f"Time frame: {registered['time_frame'] or 'not given'}")
        lines.append("")
        for sentence_number, (start, end) in enumerate(spans(case["abstract"]), start=1):
            lines.append(f"[{sentence_number}] {case['abstract'][start:end]}")
        lines.append("")
        template.append(f"{pair['id']} | ? | | |")
    (OUT / "sheet.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "labels.txt").write_text("\n".join(template) + "\n", encoding="utf-8")
    (OUT / "pick.json").write_text(json.dumps([p["id"] for p in chosen], indent=1), encoding="utf-8")
    print(f"wrote {len(chosen)} pairs to {OUT}")


if __name__ == "__main__":
    main()

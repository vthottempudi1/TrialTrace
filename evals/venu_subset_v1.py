##############################################################################
# evals/venu_subset_v1.py
#
# PURPOSE: Build the blind sheet for the human subset of validation v1 (rule: evals/validation_v1/RULE.md).
#          The first 30 pairs of the natural set, ordered by SHA-256 of "20261113" + NCT ID + case ID.
#          The order uses no label and no model answer, so it can be made before either exists.
#          Writes venu_subset/pairs.json, venu_subset/VENU_SHEET.md (what Venu reads) and
#          venu_subset/venu_labels.txt (what Venu fills in). Refuses to overwrite.
#
# USE:     python -m evals.venu_subset_v1
##############################################################################

import hashlib
import json
from pathlib import Path

from evals.prepare_v3 import spans
from evals.run_multipaper import read_cases

FOLDER = Path(__file__).parent / "validation_v1"
SEED_TEXT = "20261113"
SIZE = 30


def order_key(nct_id: str, case_id: str) -> str:
    return hashlib.sha256((SEED_TEXT + nct_id + case_id).encode("utf-8")).hexdigest()


def main() -> None:
    out = FOLDER / "venu_subset"
    if out.exists():
        raise SystemExit(f"{out} already exists; the subset is fixed.")
    cases = []
    for trial_folder in sorted((FOLDER / "natural").iterdir()):
        for case in read_cases(trial_folder / "cases.unlabelled.jsonl").values():
            cases.append(case)
    cases.sort(key=lambda case: order_key(case["nct_id"], case["case_id"]))
    chosen = cases[:SIZE]

    out.mkdir()
    (out / "pairs.json").write_text(json.dumps([{"nct_id": c["nct_id"], "case_id": c["case_id"]} for c in chosen], indent=2), encoding="utf-8")
    lines = ["# Validation v1: blind sheet for Venu (30 pairs)", "",
             "Read each abstract and decide for the registered outcome: yes (its RESULT is reported for this trial) / no / unclear.",
             "Rules: evals/rubric.md, including Addendum 2 (worked examples). 'No' means 'not in the abstract'. Do not look anything up",
             "and do not look at any model answer. Write your answers in venu_labels.txt, not here.", ""]
    for number, case in enumerate(chosen, start=1):
        registered = case["registered"]
        lines.append(f"## {number}. {case['nct_id']} / {case['case_id']}")
        lines.append(f"Registered outcome ({registered['role']}): {registered['measure_text']}")
        lines.append(f"Time frame: {registered['time_frame'] or 'not given'}")
        lines.append("")
        for sentence_number, (start, end) in enumerate(spans(case["abstract"]), start=1):
            lines.append(f"[{sentence_number}] {case['abstract'][start:end]}")
        lines.append("")
    (out / "VENU_SHEET.md").write_text("\n".join(lines), encoding="utf-8")

    template = ["# One line per pair: nct_id/case_id | answer | role | sentence numbers | note",
                "#   answer: yes / no / unclear      role (only if yes): primary / secondary / not_stated",
                "#   sentences (only if yes): the RESULT sentence number(s), e.g. 8 or 8,9",
                "# Replace each '?' ; keep the | separators."]
    for case in chosen:
        template.append(f"{case['nct_id']}/{case['case_id']} | ? | | | ")
    (out / "venu_labels.txt").write_text("\n".join(template) + "\n", encoding="utf-8")
    print(f"{len(chosen)} pairs from {len({c['nct_id'] for c in chosen})} trials -> {out}")


if __name__ == "__main__":
    main()

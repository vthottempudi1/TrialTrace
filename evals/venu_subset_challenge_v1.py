##############################################################################
# evals/venu_subset_challenge_v1.py
#
# PURPOSE: Build the blind sheets for the human subset of challenge set v1 (rule: evals/challenge_v1/RULE.md):
#          part 1 = the first 30 pairs of all 151, ordered by SHA-256 of "20261113" + NCT ID + case ID;
#          part 2 = up to 20 further pairs that Claude's draft labelled `unclear`, in the same order.
#          The sheets show NO label, no failure-mode tag and no model answer, so the reviewer cannot tell which
#          pairs are which. Writes venu_subset/ (pairs files, sheets, label templates, PROVENANCE.md).
#          Refuses to overwrite.
#
# USE:     python -m evals.venu_subset_challenge_v1
##############################################################################

import json
from pathlib import Path

from evals.prepare_v3 import spans
from evals.run_multipaper import read_cases, read_labels
from evals.venu_subset_v1 import order_key

FOLDER = Path(__file__).parent / "challenge_v1"
PART1, PART2 = 30, 20
HEADER = ["# Challenge set v1: blind sheet for Venu, %s", "",
          "Decide for each registered outcome: yes (its RESULT is reported for this trial) / no / unclear.",
          "Rules: evals/rubric.md including Addendum 2 and Addendum 3 (in evals/challenge_v1/RULE.md). 'No' means 'not in the abstract'.",
          "Do not look anything up and do not look at any model answer or any earlier label. Write answers in the labels file, not here.", ""]


def sheet(chosen: list[dict], title: str) -> str:
    lines = [line % title if "%s" in line else line for line in HEADER]
    for number, case in enumerate(chosen, start=1):
        registered = case["registered"]
        lines += [f"## {number}. {case['nct_id']} / {case['case_id']}", f"Registered outcome ({registered['role']}): {registered['measure_text']}",
                  f"Time frame: {registered['time_frame'] or 'not given'}", ""]
        for sentence_number, (start, end) in enumerate(spans(case["abstract"]), start=1):
            lines.append(f"[{sentence_number}] {case['abstract'][start:end]}")
        lines.append("")
    return "\n".join(lines)


def template(chosen: list[dict]) -> str:
    lines = ["# One line per pair: nct_id/case_id | answer | role | sentence numbers | note",
             "#   answer: yes / no / unclear      role (only if yes): primary / secondary / not_stated",
             "#   sentences (only if yes): the RESULT sentence number(s), e.g. 8 or 8,9",
             "# Replace each '?' ; keep the | separators."]
    return "\n".join(lines + [f"{c['nct_id']}/{c['case_id']} | ? | | | " for c in chosen]) + "\n"


def main() -> None:
    out = FOLDER / "venu_subset"
    if out.exists():
        raise SystemExit(f"{out} already exists; the subset is fixed.")
    cases, labels = [], {}
    for trial in sorted((FOLDER / "cases").iterdir()):
        trial_labels = read_labels(trial / "labels.txt")
        for case in read_cases(trial / "cases.unlabelled.jsonl").values():
            cases.append(case)
            labels[(case["nct_id"], case["case_id"])] = trial_labels[case["case_id"]]["answer"]
    cases.sort(key=lambda case: order_key(case["nct_id"], case["case_id"]))
    first = cases[:PART1]
    taken = {(c["nct_id"], c["case_id"]) for c in first}
    second = [c for c in cases if labels[(c["nct_id"], c["case_id"])] == "unclear" and (c["nct_id"], c["case_id"]) not in taken][:PART2]

    out.mkdir()
    for name, chosen in (("1", first), ("2", second)):
        (out / f"pairs_{name}.json").write_text(json.dumps([{"nct_id": c["nct_id"], "case_id": c["case_id"]} for c in chosen], indent=2), encoding="utf-8")
        (out / f"VENU_SHEET_{name}.md").write_text(sheet(chosen, f"part {name} ({len(chosen)} pairs)"), encoding="utf-8")
        (out / f"venu_labels_{name}.txt").write_text(template(chosen), encoding="utf-8")
    (out / "PROVENANCE.md").write_text(
        "# Who labelled the Venu subset (fill in; do not leave blank)\n\n"
        "Write exactly what happened, as for the earlier sets: who filled in each line, who reviewed it, and when.\n\n"
        "- Lines filled in by: (Venu / an AI assistant / both)\n- Reviewed by: \n- Reviewed and adopted by Venu: (yes / no)\n"
        "- Date finished: \n- Anything consulted that should not have been (frozen labels, model answers, other sets): \n", encoding="utf-8")
    print(f"part 1: {len(first)} pairs from {len({c['nct_id'] for c in first})} trials; part 2: {len(second)} pairs from {len({c['nct_id'] for c in second})} trials "
          f"(of {sum(1 for v in labels.values() if v == 'unclear')} unclear)")


if __name__ == "__main__":
    main()

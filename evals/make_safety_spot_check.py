##############################################################################
# evals/make_safety_spot_check.py
#
# PURPOSE: Safety evaluation: write the sheet a PERSON labels blind, to check the drafted labels.
#          The sheet never shows the drafted labels or any system answer.
#
# THE SAMPLE (30 abstracts, fixed seed):
#   - all 20 abstracts where the draft found a safety statement
#   - 10 abstracts the draft labelled "none": 5 from the last 11 trials of the draw (read with the
#     narrower filter, see the rubric) and 5 from the other trials
#   They are shuffled, so the sheet does not show which kind an abstract is.
#
# USE:     python -m evals.make_safety_spot_check          # writes evals/safety/spot_check/ (once)
##############################################################################

import random
from pathlib import Path

from evals.run_safety import FOLDER, parse_labels

SEED = 20261112
EMPTY_PER_PART = 5
LAST_TRIALS = 11
OUT = FOLDER / "spot_check"

HEADER = """# Blind spot-check of the safety labels. Fill this in WITHOUT opening any labels.txt.
# For each abstract in sheet.md write one line per safety statement, or "PMID | none":
# PMID | sentence | kind | term | group | count | denominator | percent | grade | related | category
# The rules are in evals/safety/rubric.md (sections 2 and 3). The registry table of each trial is in
# evals/safety/trials/<NCT>/registry.md.
"""


def abstract_block(nct_id: str, pmid: str) -> list[str]:
    """One abstract's numbered sentences, copied from the trial's blind sheet."""
    lines = (FOLDER / "trials" / nct_id / "blind_sheet.md").read_text(encoding="utf-8").splitlines()
    block = []
    inside = False
    for line in lines:
        if line.startswith("## PMID "):
            inside = line.startswith(f"## PMID {pmid}:")
        if inside:
            block.append(line)
    return block


def main() -> None:
    if OUT.exists():
        raise SystemExit("evals/safety/spot_check already exists: the sample is drawn once")
    first = (FOLDER / "trials.txt").read_text(encoding="utf-8").split()
    added = (FOLDER / "trials_extension.txt").read_text(encoding="utf-8").split()
    order = first + added
    with_statements, empty_last, empty_other = [], [], []
    for position, nct_id in enumerate(order):
        labels, empty = parse_labels(FOLDER / "trials" / nct_id / "labels.txt")
        for pmid in sorted({label["pmid"] for label in labels}):
            with_statements.append((nct_id, pmid))
        for pmid in empty:
            if position >= len(order) - LAST_TRIALS:
                empty_last.append((nct_id, pmid))
            else:
                empty_other.append((nct_id, pmid))

    rng = random.Random(SEED)
    sample = with_statements + rng.sample(empty_last, EMPTY_PER_PART) + rng.sample(empty_other, EMPTY_PER_PART)
    rng.shuffle(sample)

    sheet = ["# Safety spot-check: 30 abstracts to label blind", ""]
    for nct_id, pmid in sample:
        sheet.append(f"### Trial {nct_id} (registry table: evals/safety/trials/{nct_id}/registry.md)")
        sheet += abstract_block(nct_id, pmid)
        sheet.append("")
    OUT.mkdir(parents=True)
    (OUT / "sheet.md").write_text("\n".join(sheet), encoding="utf-8")
    (OUT / "human_labels.txt").write_text(HEADER, encoding="utf-8")
    (OUT / "sample.txt").write_text("\n".join(f"{nct_id} {pmid}" for nct_id, pmid in sample) + "\n", encoding="utf-8")
    print(f"Wrote {len(sample)} abstracts to {OUT / 'sheet.md'}; fill in {OUT / 'human_labels.txt'}.")


if __name__ == "__main__":
    main()

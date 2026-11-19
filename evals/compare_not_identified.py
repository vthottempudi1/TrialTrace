##############################################################################
# evals/compare_not_identified.py
# PURPOSE: Human labels of 20 outcomes the model called "not identified" vs my drafted outcome truth.
# USE:     python -m evals.compare_not_identified
##############################################################################

from pathlib import Path

from evals.run_multipaper import read_cases, read_labels, truth_for_outcome

HUMAN = Path(__file__).parent / "human_not_identified_20.txt"
TRIALS = Path(__file__).parent / "final" / "trials"


def main() -> None:
    counts = {}
    for line in HUMAN.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        key, human = [p.strip() for p in line.split("|")]
        nct, role, position = key.split(":")
        labels = read_labels(TRIALS / nct / "labels.txt")
        cases = read_cases(TRIALS / nct / "cases.unlabelled.jsonl")
        answers = [labels[cid]["answer"] for cid, c in cases.items()
                   if c["registered"]["role"] == role and str(c["registered"]["position"]) == position]
        mine = "yes" if "yes" in answers else "unclear" if "unclear" in answers else "no"
        counts[(mine, human)] = counts.get((mine, human), 0) + 1
        if mine != human:
            print("disagree:", key, "drafted", mine, "human", human)
    print(counts)
    print("human says model missed a reported result (yes):", sum(n for (m, h), n in counts.items() if h == "yes"), "of 20")


if __name__ == "__main__":
    main()

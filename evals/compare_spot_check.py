##############################################################################
# evals/compare_spot_check.py
# PURPOSE: Compare the human's 30 spot-check labels with the drafted labels: agreement, Cohen's kappa, disagreements.
# USE:     python -m evals.compare_spot_check
##############################################################################

from pathlib import Path

from evals.run_multipaper import read_labels

HUMAN = Path(__file__).parent / "human_labels_30.txt"
TRIALS = Path(__file__).parent / "final" / "trials"
ANSWERS = ["yes", "unclear", "no"]


def main() -> None:
    drafted_by_trial = {}
    pairs = []
    for line in HUMAN.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        nct, case_id = parts[0].split(":")
        if nct not in drafted_by_trial:
            drafted_by_trial[nct] = read_labels(TRIALS / nct / "labels.txt")
        drafted = drafted_by_trial[nct][case_id]
        pairs.append((parts[0], drafted["answer"], parts[1], parts[4]))

    total = len(pairs)
    same = sum(1 for p in pairs if p[1] == p[2])
    print(f"pairs {total}, agree {same} ({same / total:.0%})")

    table = {a: {b: 0 for b in ANSWERS} for a in ANSWERS}
    for _, mine, theirs, _ in pairs:
        table[mine][theirs] += 1
    print("rows = drafted, columns = human")
    print("          " + "  ".join(f"{b:>7}" for b in ANSWERS))
    for a in ANSWERS:
        print(f"{a:>9} " + "  ".join(f"{table[a][b]:>7}" for b in ANSWERS))

    expected = 0.0
    for a in ANSWERS:
        drafted_share = sum(table[a].values()) / total
        human_share = sum(table[b][a] for b in ANSWERS) / total
        expected += drafted_share * human_share
    kappa = (same / total - expected) / (1 - expected)
    print(f"Cohen's kappa {kappa:.2f}")

    print("disagreements:")
    for pair_id, mine, theirs, note in pairs:
        if mine != theirs:
            print(f"  {pair_id}: drafted {mine}, human {theirs} | {note}")


if __name__ == "__main__":
    main()

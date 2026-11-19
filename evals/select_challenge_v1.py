##############################################################################
# evals/select_challenge_v1.py
#
# PURPOSE: Apply the selection rule of evals/challenge_v1/RULE.md to the drawn and fetched trials: eligible trials
#          whose listed abstracts hit at least two of the four string groups, in draw order, until 150 pairs.
#          Reads stored data only (no model call). Writes selection.json and challenge_trials.txt.
#
# USE:     python -m evals.select_challenge_v1
##############################################################################

import json
from pathlib import Path

import psycopg

from evals.select_validation_v1 import listed_papers_with_abstract, outcome_count
from trialtrace.config import Settings

FOLDER = Path(__file__).parent / "challenge_v1"
TARGET_PAIRS = 150
GROUPS = {
    "A_attribution": ("pooled", "meta-analysis", "secondary analysis", "post hoc", "post-hoc", "two trials", "three trials", "ancillary"),
    "B_subgroup": ("subgroup", "stratified", "among women", "among men", "in patients with", "interaction"),
    "C_time_points": ("at 3 months", "at 6 months", "at 12 months", "at 24 months", "at 1 year", "at 2 years", "at 5 years",
                      "long-term follow-up", "week 12", "week 24"),
    "D_bare_no_difference": ("did not differ", "no significant difference", "no significant differences", "not significantly different",
                             "similar between"),
}


def group_hits(papers: list[dict]) -> list[str]:
    text = " ".join(p["abstract"].lower() for p in papers)
    return [name for name, words in GROUPS.items() if any(word in text for word in words)]


def main() -> None:
    draw = [line.strip() for line in (FOLDER / "draw.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
    chosen, skipped_not_eligible, skipped_not_challenge, pairs_total = [], 0, 0, 0
    with psycopg.connect(Settings().database_url) as conn:
        for nct_id in draw:
            if pairs_total >= TARGET_PAIRS:
                break
            outcomes = outcome_count(conn, nct_id)
            papers = listed_papers_with_abstract(conn, nct_id)
            if len(papers) < 2 or not 2 <= outcomes <= 7 or len(papers) > 5:
                skipped_not_eligible += 1
                continue
            hits = group_hits(papers)
            if len(hits) < 2:
                skipped_not_challenge += 1
                continue
            chosen.append({"nct_id": nct_id, "outcomes": outcomes, "papers": len(papers), "pairs": outcomes * len(papers), "groups": hits})
            pairs_total += outcomes * len(papers)
    (FOLDER / "challenge_trials.txt").write_text("\n".join(t["nct_id"] for t in chosen) + "\n", encoding="utf-8")
    (FOLDER / "selection.json").write_text(json.dumps({"trials": chosen, "pairs": pairs_total, "skipped_not_eligible": skipped_not_eligible,
                                                       "skipped_not_challenge": skipped_not_challenge}, indent=2), encoding="utf-8")
    print(f"selected {len(chosen)} trials, {pairs_total} pairs | not eligible {skipped_not_eligible} | eligible but not a challenge candidate {skipped_not_challenge}")
    for group in GROUPS:
        print(f"  {group}: {sum(1 for t in chosen if group in t['groups'])} trials")


if __name__ == "__main__":
    main()

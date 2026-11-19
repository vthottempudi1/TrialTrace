##############################################################################
# evals/score_overclaim_fix.py
#
# PURPOSE: Score the model-only rerun made after the prompt change (outcome overclaim fix, D89) on the two sets that
#          the fix was developed from (D42 final, D84 check), and compare with the old run of the same pairs.
#          No model call. DEVELOPMENT evidence only: the prompt was changed after seeing these overclaims.
#
# USE:     python -m evals.score_overclaim_fix
##############################################################################

import json
from pathlib import Path

import psycopg

from evals.run_multipaper import (add_up, attach_outcome_numbers, read_cases, read_labels, score_outcomes, score_pairs,
                                  truth_for_outcome)
from trialtrace.compare.paper_rollup import rollup_statuses
from trialtrace.config import Settings
from trialtrace.db.repository import get_paper_matches

HERE = Path(__file__).parent
NEW_RUN_AFTER = "match-20261113T170000Z"          # runs after this id used the new prompt
SETS = (
    ("D42 final", HERE / "final" / "trials", HERE / "runs" / "multipaper-final-20261110T004859Z.json"),
    ("D84 check", HERE / "multipaper_check", HERE / "runs" / "multipaper-check-20261113T160923Z.json"),
)


def llm_rows(conn, nct_id: str, run_id: str, cases: dict) -> list[dict]:
    rows = [dict(row) for row in get_paper_matches(conn, nct_id, run_id) if row["system"] == "llm"]
    attach_outcome_numbers(rows, cases)
    rows = [row for row in rows if row["case_id"] is not None]
    if len(rows) != len(cases):
        raise SystemExit(f"{nct_id}: {len(rows)} stored pairs for {len(cases)} labelled cases")
    return rows


def changes(old_rows: list[dict], new_rows: list[dict], labels: dict, cases: dict) -> list[str]:
    """Pairs whose status changed, with the label."""
    old = {row["case_id"]: row["status"] for row in old_rows}
    lines = []
    for row in new_rows:
        before = old[row["case_id"]]
        if before != row["status"]:
            label = labels[row["case_id"]]
            lines.append(f"{row['case_id']}: {before} -> {row['status']}  (label {label['answer']}: {label['note'][:70]})  "
                         f"{cases[row['case_id']]['registered']['measure_text'][:60]}")
    return lines


def main() -> None:
    summary = {}
    with psycopg.connect(Settings().database_url) as conn:
        seen = set()                      # NCT03566667 is in both sets (found 2026-11-13): count it once
        for name, folder, old_file in SETS:
            old_runs = {t["nct_id"]: t["match_run_id"] for t in json.loads(old_file.read_text(encoding="utf-8"))["trials"]}
            old_pairs, new_pairs, old_outcomes, new_outcomes, all_changes = [], [], [], [], []
            for nct_id, old_run in old_runs.items():
                if nct_id in seen:
                    continue
                seen.add(nct_id)
                labels = read_labels(folder / nct_id / "labels.txt")
                cases = read_cases(folder / nct_id / "cases.unlabelled.jsonl")
                newest = get_paper_matches(conn, nct_id)[0]["match_run_id"]
                if newest <= NEW_RUN_AFTER:
                    raise SystemExit(f"{nct_id}: no run with the new prompt yet")
                old_rows = llm_rows(conn, nct_id, old_run, cases)
                new_rows = llm_rows(conn, nct_id, newest, cases)
                old_pairs.append(score_pairs(old_rows, cases, labels))
                new_pairs.append(score_pairs(new_rows, cases, labels))
                old_outcomes.append(score_outcomes(old_rows, cases, labels))
                new_outcomes.append(score_outcomes(new_rows, cases, labels))
                all_changes += [f"{nct_id} {line}" for line in changes(old_rows, new_rows, labels, cases)]
            summary[name] = {"old": add_up(old_outcomes), "new": add_up(new_outcomes),
                             "old_pairs": add_up(old_pairs), "new_pairs": add_up(new_pairs)}
            print(f"\n===== {name}: outcome level (old prompt -> new prompt)")
            for key in ("outcomes", "truth_yes", "identified_calls", "supported_true_positives", "plain_true_positives"):
                print(f"  {key}: {summary[name]['old'][key]} -> {summary[name]['new'][key]}")
            print("  confusion old:", summary[name]["old"]["confusion"])
            print("  confusion new:", summary[name]["new"]["confusion"])
            print(f"  pair statuses that changed ({len(all_changes)}):")
            for line in all_changes:
                print("   ", line)
    Path(HERE / "runs" / "overclaim_fix_scores.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

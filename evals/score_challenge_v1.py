##############################################################################
# evals/score_challenge_v1.py
#
# PURPOSE: Step 5 of challenge set v1: score the STORED model answers of the one run against the frozen final labels.
#          Model only. No model call. Reports overall pair and outcome results, results by CONFIRMED failure mode
#          (the tags the labeller wrote, not the string screen), the unclear share, every wrong "identified" answer
#          and every missed "yes". Challenge scores are never pooled with validation_v1 and never read as natural
#          prevalence or general accuracy. Refuses to run before the freeze.
#
# USE:     python -m evals.score_challenge_v1
##############################################################################

import json
from pathlib import Path

import psycopg

from evals.challenge_tools import MODE_NAMES, MODES, tally_by_mode
from evals.run_multipaper import add_up, attach_outcome_numbers, quote_is_valid, read_cases, score_outcomes, score_pairs, truth_for_outcome
from evals.run_safety import rate_text
from trialtrace.compare.paper_rollup import rollup_statuses
from trialtrace.config import Settings
from trialtrace.db.repository import get_paper_matches

FOLDER = Path(__file__).parent / "challenge_v1"
ANSWER = {"identified": "yes", "ambiguous": "unclear", "not_identified": "no"}


def main() -> None:
    if not (FOLDER / "freeze.md").exists():
        raise SystemExit("freeze.md is missing: the labels must be frozen before scoring.")
    final = json.loads((FOLDER / "final_labels.json").read_text(encoding="utf-8"))
    trials = sorted(path.name for path in (FOLDER / "cases").iterdir())
    pair_parts, outcome_parts, pairs_all, wrong, missed, runs, bad_quotes, failures = [], [], [], [], [], {}, 0, 0
    with psycopg.connect(Settings().database_url) as conn:
        for nct_id in trials:
            cases = read_cases(FOLDER / "cases" / nct_id / "cases.unlabelled.jsonl")
            labels = {cid: {"answer": final[f"{nct_id}/{cid}"]["final"], "role": "not_stated", "sentences": [], "note": ""} for cid in cases}
            stored = get_paper_matches(conn, nct_id)
            if not stored:
                raise SystemExit(f"{nct_id}: no stored model answers")
            runs[nct_id] = stored[0]["match_run_id"]
            rows = [dict(row) for row in stored if row["system"] == "llm"]
            attach_outcome_numbers(rows, cases)
            rows = [row for row in rows if row["case_id"] is not None]
            if len(rows) != len(cases):
                raise SystemExit(f"{nct_id}: {len(rows)} stored pairs for {len(cases)} cases")
            pair_parts.append(score_pairs(rows, cases, labels))
            outcome_parts.append(score_outcomes(rows, cases, labels))
            by_outcome = {}
            for row in rows:
                key = f"{nct_id}/{row['case_id']}"
                registered = cases[row["case_id"]]["registered"]
                pairs_all.append({"key": key, "label": final[key]["final"], "status": row["status"], "tags": final[key]["tags"]})
                by_outcome.setdefault((row["outcome_role"], row["outcome_position"]), []).append(row)
                if row["status"] == "identified" and final[key]["final"] != "yes":
                    wrong.append({"pair": key, "label": final[key]["final"], "tags": final[key]["tags"], "measure": registered["measure_text"][:90],
                                  "matched_name": row["matched_name"], "quote": row["quote_text"], "valid_quote": quote_is_valid(cases[row["case_id"]], row)})
            for outcome_rows in by_outcome.values():
                truth = truth_for_outcome([labels[row["case_id"]]["answer"] for row in outcome_rows])
                answer = rollup_statuses([row["status"] for row in outcome_rows], unread=False)
                if truth == "yes" and answer != "identified":
                    missed.append({"trial": nct_id, "measure": cases[outcome_rows[0]["case_id"]]["registered"]["measure_text"][:90], "model": answer})
    pairs, outcomes = add_up(pair_parts), add_up(outcome_parts)
    bad_quotes, failures = pairs["bad_quotes"], pairs["failures"]
    by_mode = tally_by_mode(pairs_all)
    unclear_outcomes = sum(count for key, count in outcomes["confusion"].items() if key.endswith("-> unclear"))

    print(f"Challenge set v1 (ENRICHED, scored on its own): {len(trials)} trials, {outcomes['outcomes']} outcomes, {len(pairs_all)} pairs")
    print(f"Outcome level: identified calls {outcomes['identified_calls']}, of which labelled reported {outcomes['plain_true_positives']}; "
          f"reported outcomes found with a valid quote {rate_text(outcomes['supported_true_positives'], outcomes['truth_yes'])}")
    print(f"Wrong identified outcomes: {outcomes['identified_calls'] - outcomes['plain_true_positives']} of {outcomes['identified_calls']}; outcomes called unclear: {unclear_outcomes} of {outcomes['outcomes']}")
    print("Outcome confusion (label -> model):", outcomes["confusion"])
    print(f"Bad quotes {bad_quotes}, failed reads {failures}")
    print("\nBy CONFIRMED failure mode (pairs; modes overlap, so rows are not additive):")
    for mode in MODES:
        row = by_mode[mode]
        print(f"  {MODE_NAMES[mode]}: {row['pairs']} pairs, labelled {row['labelled']}; model called identified {row['model_identified']} "
              f"(wrong {row['wrong_identified']}); of {row['labelled_unclear']} labelled unclear, model said unclear on {row['model_unclear_on_labelled_unclear']}")
    print(f"\nEvery wrong identified pair ({len(wrong)}):")
    for item in wrong:
        print(f"  {item['pair']} label={item['label']} tags={item['tags']} valid_quote={item['valid_quote']}: {item['measure']} | {item['quote']}")
    print(f"\nReported outcomes not called identified ({len(missed)}):")
    for item in missed:
        print(f"  {item['trial']} model={item['model']}: {item['measure']}")

    out = FOLDER / "run"
    out.mkdir(exist_ok=True)
    (out / "scores.json").write_text(json.dumps({"runs": runs, "pairs": pairs, "outcomes": {k: v for k, v in outcomes.items() if k != "details"}, "by_failure_mode": by_mode,
                                                 "wrong_identified": wrong, "missed_yes": missed}, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()

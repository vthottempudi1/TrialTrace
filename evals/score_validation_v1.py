##############################################################################
# evals/score_validation_v1.py
#
# PURPOSE: Score validation v1 (rule: evals/validation_v1/RULE.md) from the STORED model answers of the one run.
#          Model only (the baseline was not run). No model call. Reports the pre-registered checks A to D at
#          outcome level with Wilson 95% ranges, plus pair-level counts, the unclear split and the list of every
#          wrong "identified" and every missed "yes" so they can be audited. Saves evals/validation_v1/run/scores.json.
#          Refuses to run until the freeze record exists.
#
# USE:     python -m evals.score_validation_v1
##############################################################################

import json
from pathlib import Path

import psycopg

from evals.run_multipaper import (add_up, attach_outcome_numbers, quote_is_valid, read_cases, read_labels,
                                  score_outcomes, score_pairs, truth_for_outcome)
from evals.run_safety import rate_text
from trialtrace.compare.paper_rollup import rollup_statuses
from trialtrace.config import Settings
from trialtrace.db.repository import get_paper_matches

FOLDER = Path(__file__).parent / "validation_v1"


def stored_llm_rows(conn, nct_id: str, cases: dict) -> tuple[str, list[dict]]:
    stored = get_paper_matches(conn, nct_id)
    if not stored:
        raise SystemExit(f"{nct_id}: no stored match answers")
    run_id = stored[0]["match_run_id"]
    rows = [dict(row) for row in stored if row["system"] == "llm"]
    attach_outcome_numbers(rows, cases)
    rows = [row for row in rows if row["case_id"] is not None]
    if len(rows) != len(cases):
        raise SystemExit(f"{nct_id}: {len(rows)} stored pairs for {len(cases)} labelled cases")
    return run_id, rows


def outcome_lists(rows: list[dict], cases: dict, labels: dict) -> tuple[list[dict], list[dict]]:
    """Every wrong 'identified' outcome and every 'yes' outcome the model did not call identified."""
    by_outcome = {}
    for row in rows:
        by_outcome.setdefault((row["outcome_role"], row["outcome_position"]), []).append(row)
    wrong, missed = [], []
    for key, outcome_rows in sorted(by_outcome.items()):
        truth = truth_for_outcome([labels[row["case_id"]]["answer"] for row in outcome_rows])
        answer = rollup_statuses([row["status"] for row in outcome_rows], unread=False)
        first = cases[outcome_rows[0]["case_id"]]["registered"]
        item = {"nct_id": outcome_rows[0]["nct_id"], "outcome": f"{key[0]}:{key[1]}", "measure": first["measure_text"][:100],
                "truth": truth, "model": answer}
        if answer == "identified" and truth != "yes":
            item["quotes"] = [row["quote_text"] for row in outcome_rows if row["status"] == "identified"]
            wrong.append(item)
        if truth == "yes" and answer != "identified":
            missed.append(item)
    return wrong, missed


def main() -> None:
    if not (FOLDER / "freeze.md").exists():
        raise SystemExit("freeze.md is missing: labels must be frozen before scoring")
    trials = [line.strip() for line in (FOLDER / "natural_trials.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
    pair_parts, outcome_parts, wrong_all, missed_all, runs = [], [], [], [], {}
    with psycopg.connect(Settings().database_url) as conn:
        for nct_id in trials:
            labels = read_labels(FOLDER / "natural" / nct_id / "labels.txt")
            cases = read_cases(FOLDER / "natural" / nct_id / "cases.unlabelled.jsonl")
            run_id, rows = stored_llm_rows(conn, nct_id, cases)
            runs[nct_id] = run_id
            pair_parts.append(score_pairs(rows, cases, labels))
            outcome_parts.append(score_outcomes(rows, cases, labels))
            wrong, missed = outcome_lists(rows, cases, labels)
            wrong_all += wrong
            missed_all += missed
    pairs = add_up(pair_parts)
    outcomes = add_up(outcome_parts)

    calls, supported, truth_yes = outcomes["identified_calls"], outcomes["supported_true_positives"], outcomes["truth_yes"]
    confusion = outcomes["confusion"]
    unclear_outcomes = sum(count for key, count in confusion.items() if key.endswith("-> unclear"))
    total_outcomes = outcomes["outcomes"]
    print(f"Validation v1, natural set: {len(trials)} trials, {total_outcomes} outcomes, {pairs['yes'] + pairs['no'] + pairs['unclear']} pairs")
    print(f"A. precision of identified (identified outcomes labelled reported): {rate_text(outcomes['plain_true_positives'], calls)}   target >= 90%")
    print(f"B. recall (reported outcomes found with a valid supporting quote):  {rate_text(supported, truth_yes)}   target >= 80%")
    print(f"C. bad quotes {pairs['bad_quotes']}, failed reads {pairs['failures']}   target 0 and 0")
    print(f"D. outcomes the model called unclear: {rate_text(unclear_outcomes, total_outcomes)}")
    print("   confusion (truth -> model):", confusion)
    print(f"Pair level: yes {pairs['yes']} (identified {pairs['yes_identified']}, ambiguous {pairs['yes_ambiguous']}, not_identified {pairs['yes_not_identified']}); "
          f"unclear {pairs['unclear']} (identified {pairs['unclear_identified']}, ambiguous {pairs['unclear_ambiguous']}, not_identified {pairs['unclear_not_identified']}); "
          f"no {pairs['no']} (identified {pairs['no_identified']}, ambiguous {pairs['no_ambiguous']}, not_identified {pairs['no_not_identified']})")
    print(f"\nWrong identified outcomes ({len(wrong_all)}):")
    for item in wrong_all:
        print(f"  {item['nct_id']} {item['outcome']} truth={item['truth']}: {item['measure']} | quotes: {item['quotes']}")
    print(f"\nReported outcomes not called identified ({len(missed_all)}):")
    for item in missed_all:
        print(f"  {item['nct_id']} {item['outcome']} model={item['model']}: {item['measure']}")

    out = FOLDER / "run"
    out.mkdir(exist_ok=True)
    (out / "scores.json").write_text(json.dumps({"runs": runs, "pairs": pairs, "outcomes": {k: v for k, v in outcomes.items() if k != "details"},
                                                 "wrong_identified": wrong_all, "missed_yes": missed_all}, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()

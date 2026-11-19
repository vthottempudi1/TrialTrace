##############################################################################
# evals/audit_overclaims.py
#
# PURPOSE: List every registered outcome the model called "identified" when the labels say it was not reported
#          (truth no or unclear): the outcome, the paper, the model's quote and reason, and the label note.
#          Reads stored answers only (no model call). Development evidence: it shows WHY the model overclaimed.
#
# USE:     python -m evals.audit_overclaims                 (D42 final run and D84 cross-check)
##############################################################################

import json
from pathlib import Path

import psycopg

from evals.run_multipaper import attach_outcome_numbers, read_cases, read_labels, truth_for_outcome
from trialtrace.compare.paper_rollup import rollup_statuses
from trialtrace.config import Settings
from trialtrace.db.repository import get_paper_matches

HERE = Path(__file__).parent
SETS = (
    ("D42 final", HERE / "final" / "trials", HERE / "runs" / "multipaper-final-20261110T004859Z.json"),
    ("D84 check", HERE / "multipaper_check", HERE / "runs" / "multipaper-check-20261113T160923Z.json"),
)


def overclaims_for_trial(conn, folder: Path, nct_id: str, run_id: str) -> list[dict]:
    labels = read_labels(folder / nct_id / "labels.txt")
    cases = read_cases(folder / nct_id / "cases.unlabelled.jsonl")
    rows = [dict(row) for row in get_paper_matches(conn, nct_id, run_id) if row["system"] == "llm"]
    attach_outcome_numbers(rows, cases)
    by_outcome = {}
    for row in rows:
        if row["case_id"] is not None:
            by_outcome.setdefault((row["outcome_role"], row["outcome_position"]), []).append(row)

    found = []
    for key, outcome_rows in by_outcome.items():
        truth = truth_for_outcome([labels[row["case_id"]]["answer"] for row in outcome_rows])
        answer = rollup_statuses([row["status"] for row in outcome_rows], unread=False)
        if answer != "identified" or truth == "yes":
            continue
        for row in outcome_rows:
            if row["status"] != "identified":
                continue
            case = cases[row["case_id"]]
            found.append({
                "nct_id": nct_id, "case_id": row["case_id"], "truth": truth,
                "outcome": case["registered"]["measure_text"], "time_frame": case["registered"]["time_frame"],
                "label": labels[row["case_id"]]["answer"], "label_note": labels[row["case_id"]]["note"],
                "matched_name": row["matched_name"], "quote": row["quote_text"], "reason": row["reason"],
            })
    return found


def main() -> None:
    everything = []
    seen = set()                          # NCT03566667 is in both sets: count it once
    with psycopg.connect(Settings().database_url) as conn:
        for name, folder, result_file in SETS:
            result = json.loads(result_file.read_text(encoding="utf-8"))
            for trial in result["trials"]:
                if trial["nct_id"] in seen:
                    continue
                seen.add(trial["nct_id"])
                for item in overclaims_for_trial(conn, folder, trial["nct_id"], trial["match_run_id"]):
                    item["set"] = name
                    everything.append(item)
    for number, item in enumerate(everything, start=1):
        print(f"--- {number}. [{item['set']}] {item['nct_id']} {item['case_id']}  truth={item['truth']} label={item['label']} ({item['label_note']})")
        print(f"registered: {item['outcome']}  |  {item['time_frame']}")
        print(f"model name: {item['matched_name']}")
        print(f"quote:      {item['quote']}")
        print(f"reason:     {item['reason']}")
    print(f"\n{len(everything)} wrong identified pairs")
    (HERE / "runs" / "overclaim_audit.json").write_text(json.dumps(everything, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

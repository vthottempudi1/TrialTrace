##############################################################################
# evals/make_not_identified_check.py
# PURPOSE: A person checks the outcomes the MODEL called "not identified" (these are not sent to review).
#          Draws 20 such outcomes (seeded) from the 19 final trials; for each, the sheet shows the outcome and
#          EVERY paper abstract read for that trial. The model's and my labels are hidden. Refuses to overwrite.
# USE:     python -m evals.make_not_identified_check
##############################################################################

import json
import random
from pathlib import Path

import psycopg

from evals.prepare_v3 import spans
from evals.run_multipaper import read_cases
from trialtrace.compare.paper_rollup import rollup_statuses
from trialtrace.config import Settings
from trialtrace.db.repository import get_paper_matches

TRIALS = Path(__file__).parent / "final" / "trials"
OUT = Path(__file__).parent / "final" / "not_identified_check"
SEED = 20261112
SIZE = 20


def main() -> None:
    if OUT.exists():
        raise SystemExit(f"{OUT} already exists; the pick is fixed.")
    candidates = []
    with psycopg.connect(Settings().database_url) as conn:
        for folder in sorted(TRIALS.iterdir()):
            nct_id = folder.name
            rows = get_paper_matches(conn, nct_id)
            run_id = rows[0]["match_run_id"]
            by_outcome = {}
            for row in rows:
                if row["match_run_id"] == run_id and row["system"] == "llm":
                    by_outcome.setdefault((row["outcome_role"], row["outcome_position"]), []).append(row["status"])
            for key, statuses in by_outcome.items():
                if rollup_statuses(statuses, False) == "not_identified":
                    candidates.append((nct_id, key))
    random.seed(SEED)
    chosen = random.sample(candidates, SIZE)
    chosen.sort()                                           # sorted by trial so abstracts are not repeated

    OUT.mkdir(parents=True)
    lines = ["# Check of 'not identified' outcomes (20, system answers hidden)", "",
             "For each outcome, read ALL the abstracts of that trial. Does ANY of them report this outcome's RESULT?",
             "yes = a result is reported (number or test for this measure); no = not in any abstract; unclear = narrower / pooled / qualitative / other time point.",
             "Fill in labels.txt: id | answer | note", ""]
    template = ["# id | answer | note     (answer: yes / no / unclear)"]
    for number, (nct_id, key) in enumerate(chosen, start=1):
        cases = read_cases(TRIALS / nct_id / "cases.unlabelled.jsonl")
        mine = [c for c in cases.values() if (c["registered"]["role"], c["registered"]["position"]) == key]
        first = mine[0]["registered"]
        lines.append(f"## {number}. {nct_id} outcome {first['role']} #{first['position']}")
        lines.append(f"Outcome: {first['measure_text']}")
        lines.append(f"Time frame: {first['time_frame'] or 'not given'}")
        lines.append("")
        for case in mine:
            lines.append(f"### PMID {case['paper']['pmid']}")
            for sentence_number, (start, end) in enumerate(spans(case["abstract"]), start=1):
                lines.append(f"[{sentence_number}] {case['abstract'][start:end]}")
            lines.append("")
        template.append(f"{nct_id}:{first['role']}:{first['position']} | ? |")
    (OUT / "sheet.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "labels.txt").write_text("\n".join(template) + "\n", encoding="utf-8")
    print(f"{len(candidates)} model 'not identified' outcomes; wrote {SIZE} to {OUT}")


if __name__ == "__main__":
    main()

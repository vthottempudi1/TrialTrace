##############################################################################
# evals/make_cases_v4.py
#
# PURPOSE: Build 20 FRESH evaluation cases (4 trials x 5 outcomes) from evals/v4/draw.txt.
#          The trials were never used in v1, v2 or v3. The rule is the v2 rule:
#          eligible trial = a `listed` paper with an abstract and at least 5 registered outcomes;
#          the first 4 eligible trials in draw order are used; per trial, primary outcomes first,
#          then a seeded random pick. Writes the cases unlabelled and a blind sheet (v3 sentence numbers).
#          Refuses to overwrite an existing cases file.
#
# USE:     python -m evals.make_cases_v4
##############################################################################

import hashlib
import json
from pathlib import Path

import psycopg

from evals.make_cases import SEED, choose_outcomes
from evals.make_cases_v2 import find_eligible_trials
from evals.prepare_v3 import spans
from trialtrace.config import Settings

V4_DIR = Path(__file__).parent / "v4"
DRAW_FILE = V4_DIR / "draw.txt"
CASES_FILE = V4_DIR / "cases.unlabelled.jsonl"
BLIND_SHEET = V4_DIR / "blind_sheet.md"
TRIALS = 4
CASES_PER_TRIAL = 5


def build_cases(eligible: list[dict]) -> list[dict]:
    cases = []
    for trial in eligible[:TRIALS]:
        abstract = trial["paper"]["abstract"]
        abstract_hash = hashlib.sha256(abstract.encode("utf-8")).hexdigest()
        for outcome in choose_outcomes(trial["outcomes"], CASES_PER_TRIAL, SEED):
            cases.append({
                "case_id": f"t{len(cases) + 1:02d}",
                "split": "held_out",
                "nct_id": trial["nct_id"],
                "registry_content_hash": trial["snapshot"].content_hash,
                "paper": {"pmid": trial["paper"]["pmid"], "raw_hash": trial["paper"]["raw_hash"], "abstract_sha256": abstract_hash},
                "registered": {"role": outcome.role, "position": outcome.position,
                               "measure_text": outcome.measure_text, "time_frame": outcome.time_frame},
                "abstract": abstract,
                "label": None,
            })
    return cases


def write_blind_sheet(cases: list[dict]) -> None:
    lines = ["# V4 blind sheet (no suggestions)", "",
             "Read the abstract only. Decide yes / no / unclear; for yes also the role and the RESULT sentence number.", ""]
    for case in cases:
        registered = case["registered"]
        lines.append(f"## {case['case_id']} ({case['nct_id']}, PMID {case['paper']['pmid']})")
        lines.append(f"Outcome ({registered['role']}): {registered['measure_text']}")
        lines.append(f"Time frame: {registered['time_frame'] or 'not given'}")
        lines.append("")
        for number, (start, end) in enumerate(spans(case["abstract"]), start=1):
            lines.append(f"[{number}] {case['abstract'][start:end]}")
        lines.append("")
    BLIND_SHEET.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    if CASES_FILE.exists():
        raise SystemExit(f"{CASES_FILE} already exists; the cases are frozen.")
    nct_ids = DRAW_FILE.read_text().split()
    with psycopg.connect(Settings().database_url) as conn:
        eligible = find_eligible_trials(conn, nct_ids)
    if len(eligible) < TRIALS:
        raise SystemExit(f"only {len(eligible)} eligible trials, need {TRIALS}")
    cases = build_cases(eligible)
    CASES_FILE.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n", encoding="utf-8")
    write_blind_sheet(cases)
    print(f"Wrote {len(cases)} cases from trials {[t['nct_id'] for t in eligible[:TRIALS]]}")


if __name__ == "__main__":
    main()

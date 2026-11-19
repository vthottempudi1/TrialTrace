##############################################################################
# evals/make_cases.py
#
# PURPOSE: Pick the 15 evaluation cases from the stored data BY RULE, and write them
#          unlabelled to evals/cases.jsonl. A case = one registered outcome + one linked
#          paper's abstract. The choice never looks at what the baseline or any LLM says,
#          so it cannot favour either. The text is copied into the file, so the cases stay
#          frozen even if the database changes.
#          Cases are split BY TRIAL (dev / held_out), so the same trial never sits on both sides.
#          Refuses to overwrite an existing cases file: changing the cases means a new file.
#
# RULES:   - per trial: the lowest-PMID paper whose link is `listed` and which has an abstract
#          - outcomes: primary first, then a seeded random pick of the others, up to the quota
#
# USE:     python -m evals.make_cases
##############################################################################

import hashlib
import json
import random
from pathlib import Path

import psycopg

from trialtrace.config import Settings
from trialtrace.contracts import RegisteredOutcome
from trialtrace.db.repository import get_latest_snapshot, get_outcomes, get_papers_for_trial

CASES_FILE = Path(__file__).parent / "cases.jsonl"
SEED = 2026
PLAN = {                                             # trial -> (split, number of cases)
    "NCT01139775": ("dev", 3),
    "NCT01295177": ("dev", 2),
    "NCT02997202": ("dev", 5),
    "NCT04644237": ("held_out", 4),
    "NCT03376932": ("held_out", 1),
}


def choose_outcomes(outcomes: list[RegisteredOutcome], quota: int, seed: int) -> list[RegisteredOutcome]:
    """Primary outcomes first (in registry order), then a seeded random pick of the rest, up to the quota."""
    primary_outcomes = []
    other_outcomes = []
    for outcome in outcomes:
        if outcome.role == "primary":
            primary_outcomes.append(outcome)
        else:
            other_outcomes.append(outcome)

    # Take primary outcomes first, without exceeding the requested number.
    chosen = primary_outcomes[:quota]
    remaining_slots = quota - len(chosen)

    if remaining_slots > 0:
        # The same seed and input order give the same random selection.
        random_generator = random.Random(seed)
        number_to_pick = min(remaining_slots, len(other_outcomes))
        selected_others = random_generator.sample(other_outcomes, number_to_pick)
        chosen.extend(selected_others)
    return chosen


def build_cases(conn: psycopg.Connection) -> list[dict]:
    """Copy selected outcomes and abstracts from PostgreSQL into unlabelled cases."""
    cases = []
    for nct_id, (split, quota) in PLAN.items():
        # 1. Read the trial's latest saved registry version and its outcomes.
        snapshot = get_latest_snapshot(conn, nct_id)
        extraction = get_outcomes(conn, nct_id, snapshot.content_hash)

        # 2. Keep only listed papers with a non-empty abstract.
        all_papers = get_papers_for_trial(conn, nct_id)
        eligible_papers = []
        for paper in all_papers:
            if paper["status"] == "listed" and paper["abstract"]:
                eligible_papers.append(paper)

        if not eligible_papers or extraction is None:
            raise SystemExit(f"{nct_id}: no listed paper with an abstract, or no registered outcomes")

        # 3. Sort numerically by PMID and use the first paper.
        eligible_papers.sort(key=lambda paper: int(paper["pmid"]))
        selected_paper = eligible_papers[0]
        abstract = selected_paper["abstract"]
        abstract_hash = hashlib.sha256(abstract.encode("utf-8")).hexdigest()

        # 4. Each selected outcome becomes one case, using the same abstract.
        selected_outcomes = choose_outcomes(extraction.outcomes, quota, SEED)
        for outcome in selected_outcomes:
            case = {
                "case_id": f"c{len(cases) + 1:02d}",
                "split": split,
                "nct_id": nct_id,
                "registry_content_hash": snapshot.content_hash,
                "paper": {
                    "pmid": selected_paper["pmid"],
                    "raw_hash": selected_paper["raw_hash"],
                    "abstract_sha256": abstract_hash,
                },
                "registered": {
                    "role": outcome.role,
                    "position": outcome.position,
                    "measure_text": outcome.measure_text,
                    "time_frame": outcome.time_frame,
                },
                "abstract": abstract,
                "label": None,                       # filled in by evals/label.py
            }
            cases.append(case)
    return cases


def main() -> None:
    if CASES_FILE.exists():
        raise SystemExit(f"{CASES_FILE} already exists. The cases are frozen; make a new file name to start over.")
    settings = Settings()
    with psycopg.connect(settings.database_url) as conn:
        cases = build_cases(conn)

    # JSONL means one JSON object per line.
    lines = []
    dev_count = 0
    for case in cases:
        lines.append(json.dumps(case, ensure_ascii=False))
        if case["split"] == "dev":
            dev_count += 1

    file_text = "\n".join(lines) + "\n"
    CASES_FILE.write_text(file_text, encoding="utf-8")
    held_out_count = len(cases) - dev_count
    print(f"Wrote {len(cases)} cases to {CASES_FILE} ({dev_count} dev, {held_out_count} held out).")


if __name__ == "__main__":
    main()

##############################################################################
# evals/make_cases_v2.py
#
# PURPOSE: Build the v2 evaluation cases (70 = 50 dev + 20 held out), unlabelled, from the
#          drawn trials in evals/v2/draw.txt, by the rule in evals/v2/rubric.md section 2.
#          It also writes a labelling sheet (evals/v2/label_sheet.md) to read and answer elsewhere.
#          Refuses to overwrite an existing cases file.
#
# RULES:   - eligible trial: a `listed` paper with an abstract (lowest PMID is used) and at least 5 registered outcomes
#          - eligible trials in draw order: the first 10 are dev, the next 4 are held out
#          - 5 outcomes per trial: primary first, then a seeded random pick (same as v1)
#
# USE:     python -m evals.make_cases_v2
##############################################################################

import hashlib
import json
from pathlib import Path

import psycopg

from evals.make_cases import SEED, choose_outcomes
from trialtrace.compare.abstract_baseline import sentence_spans
from trialtrace.config import Settings
from trialtrace.db.repository import get_latest_snapshot, get_outcomes, get_papers_for_trial

V2_DIR = Path(__file__).parent / "v2"
DRAW_FILE = V2_DIR / "draw.txt"
CASES_FILE = V2_DIR / "cases.jsonl"
SHEET_FILE = V2_DIR / "label_sheet.md"
DEV_TRIALS = 10
HELD_OUT_TRIALS = 4
CASES_PER_TRIAL = 5


def find_eligible_trials(conn: psycopg.Connection, nct_ids: list[str]) -> list[dict]:
    """Eligible trials in draw order, each with its snapshot, outcomes and chosen paper."""
    eligible = []
    for nct_id in nct_ids:
        snapshot = get_latest_snapshot(conn, nct_id)
        if snapshot is None:
            continue
        extraction = get_outcomes(conn, nct_id, snapshot.content_hash)
        if extraction is None or len(extraction.outcomes) < CASES_PER_TRIAL:
            continue
        papers = []
        for paper in get_papers_for_trial(conn, nct_id):
            if paper["status"] == "listed" and paper["abstract"]:
                papers.append(paper)
        if not papers:
            continue
        papers.sort(key=lambda paper: int(paper["pmid"]))
        eligible.append({"nct_id": nct_id, "snapshot": snapshot, "outcomes": extraction.outcomes, "paper": papers[0]})
    return eligible


def build_cases(eligible: list[dict]) -> list[dict]:
    """Turn the first 14 eligible trials into unlabelled cases."""
    cases = []
    for index, trial in enumerate(eligible[: DEV_TRIALS + HELD_OUT_TRIALS]):
        if index < DEV_TRIALS:
            split = "dev"
        else:
            split = "held_out"
        abstract = trial["paper"]["abstract"]
        abstract_hash = hashlib.sha256(abstract.encode("utf-8")).hexdigest()
        for outcome in choose_outcomes(trial["outcomes"], CASES_PER_TRIAL, SEED):
            case = {
                "case_id": f"v{len(cases) + 1:02d}",
                "split": split,
                "nct_id": trial["nct_id"],
                "registry_content_hash": trial["snapshot"].content_hash,
                "paper": {
                    "pmid": trial["paper"]["pmid"],
                    "raw_hash": trial["paper"]["raw_hash"],
                    "abstract_sha256": abstract_hash,
                },
                "registered": {
                    "role": outcome.role,
                    "position": outcome.position,
                    "measure_text": outcome.measure_text,
                    "time_frame": outcome.time_frame,
                },
                "abstract": abstract,
                "label": None,
            }
            cases.append(case)
    return cases


def write_sheet(cases: list[dict]) -> None:
    """A readable sheet: each case with its numbered abstract sentences, to label and send back."""
    lines = [
        "# TrialTrace v2 labelling sheet",
        "",
        "For each case answer from the ABSTRACT ONLY. Reply as one line per case, for example:",
        "`v01: y, primary, 4` or `v02: n` or `v03: u` (y/n/u; if y: role primary/secondary/not_stated, then the sentence number).",
        "`n` = not in the abstract. `u` = unclear: it reports only a narrower or different thing (e.g. grade >=3 events when all events are registered, another time frame, a subgroup).",
        "",
    ]
    for case in cases:
        registered = case["registered"]
        lines.append("---")
        lines.append(f"## {case['case_id']}  ({case['nct_id']}, paper {case['paper']['pmid']})")
        lines.append(f"**Registered outcome ({registered['role']}):** {registered['measure_text']}")
        lines.append(f"**Time frame:** {registered['time_frame'] or 'not given'}")
        lines.append("")
        for number, (start, end) in enumerate(sentence_spans(case["abstract"]), start=1):
            lines.append(f"[{number}] {case['abstract'][start:end]}")
        lines.append("")
    SHEET_FILE.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    if CASES_FILE.exists():
        raise SystemExit(f"{CASES_FILE} already exists. The cases are frozen; use a new file name to start over.")
    nct_ids = DRAW_FILE.read_text().split()
    with psycopg.connect(Settings().database_url) as conn:
        eligible = find_eligible_trials(conn, nct_ids)
    needed = DEV_TRIALS + HELD_OUT_TRIALS
    if len(eligible) < needed:
        raise SystemExit(f"only {len(eligible)} eligible trials, need {needed}: draw and ingest more (rubric section 2)")
    cases = build_cases(eligible)
    lines = [json.dumps(case, ensure_ascii=False) for case in cases]
    CASES_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_sheet(cases)
    dev_count = sum(1 for case in cases if case["split"] == "dev")
    print(f"Wrote {len(cases)} cases ({dev_count} dev, {len(cases) - dev_count} held out) to {CASES_FILE}")
    print(f"Labelling sheet: {SHEET_FILE}")


if __name__ == "__main__":
    main()

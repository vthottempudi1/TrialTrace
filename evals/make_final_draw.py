##############################################################################
# evals/make_final_draw.py
#
# PURPOSE: Draw the trials for the final multi-paper evaluation (written BEFORE anything is ingested or labelled).
#          Pool: completed ClinicalTrials.gov studies with at least 2 references of type RESULT, 2 to 7 listed
#          outcomes (primary + secondary entries), never used in any earlier evaluation case file.
#          The API returns studies in its own order; we read the first PAGES pages (a limit of the pool, not a
#          random draw from the whole registry), then draw with a fixed seed.
#          Writes evals/final/draw.txt (draw order) and evals/final/pool_summary.txt. Refuses to overwrite.
#
# USE:     python -m evals.make_final_draw
##############################################################################

import glob
import json
import random
from pathlib import Path

import httpx

SEED = 20261110
PAGES = 6
PAGE_SIZE = 1000
DRAW_SIZE = 150
URL = "https://clinicaltrials.gov/api/v2/studies"
FINAL_DIR = Path(__file__).parent / "final"


def used_trials() -> set[str]:
    """Every trial in any earlier evaluation case file (they are never reused)."""
    used = set()
    for path in glob.glob(str(Path(__file__).parent / "**" / "*.jsonl"), recursive=True):
        for line in open(path, encoding="utf-8"):
            try:
                case = json.loads(line)
            except ValueError:
                continue
            if isinstance(case, dict) and "case_id" in case and "nct_id" in case:
                used.add(case["nct_id"])
    return used


def fetch_pool() -> list[dict]:
    params = {"filter.overallStatus": "COMPLETED", "query.term": "AREA[ReferenceType]RESULT", "pageSize": PAGE_SIZE,
              "fields": "NCTId|ReferencePMID|ReferenceType|PrimaryOutcomeMeasure|SecondaryOutcomeMeasure"}
    studies = []
    for _ in range(PAGES):
        answer = httpx.get(URL, params=params, timeout=60)
        answer.raise_for_status()
        body = answer.json()
        studies.extend(body["studies"])
        if not body.get("nextPageToken"):
            break
        params["pageToken"] = body["nextPageToken"]
    return studies


def eligible(study: dict) -> bool:
    section = study["protocolSection"]
    references = section.get("referencesModule", {}).get("references", [])
    result_papers = {ref.get("pmid") for ref in references if ref.get("type") == "RESULT" and ref.get("pmid")}
    outcomes = section.get("outcomesModule", {})
    count = len(outcomes.get("primaryOutcomes", [])) + len(outcomes.get("secondaryOutcomes", []))
    return len(result_papers) >= 2 and 2 <= count <= 7


def main() -> None:
    draw_file = FINAL_DIR / "draw.txt"
    if draw_file.exists():
        raise SystemExit(f"{draw_file} already exists; the draw is fixed.")
    used = used_trials()
    pool = fetch_pool()
    candidates = sorted(s["protocolSection"]["identificationModule"]["nctId"] for s in pool if eligible(s))
    candidates = [nct for nct in candidates if nct not in used]
    random.Random(SEED).shuffle(candidates)
    drawn = candidates[:DRAW_SIZE]
    FINAL_DIR.mkdir(exist_ok=True)
    draw_file.write_text("\n".join(drawn) + "\n", encoding="utf-8")
    (FINAL_DIR / "pool_summary.txt").write_text(
        f"seed {SEED}; pages read {PAGES} x {PAGE_SIZE}; studies read {len(pool)}; eligible and unused {len(candidates)}; drawn {len(drawn)}\n",
        encoding="utf-8")
    print(f"studies read {len(pool)}, eligible and unused {len(candidates)}, drawn {len(drawn)} -> {draw_file}")


if __name__ == "__main__":
    main()

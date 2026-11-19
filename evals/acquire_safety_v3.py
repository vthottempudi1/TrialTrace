"""Acquire a predeclared external trial draw, without calling any model.

Run from the repository root. Resuming reuses the saved IDs, never redraws.
"""
import json
import random
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg

from trialtrace.config import Settings
from trialtrace.db.repository import list_trial_ids
from trialtrace.ingest_registry import ingest_trial
from trialtrace.ingest_papers import ingest_papers

FOLDER = Path("evals/safety_v3_acquisition")
SEED = 20261112
PARAMS = {"format": "json", "pageSize": 500, "filter.overallStatus": "COMPLETED",
          "filter.advanced": "AREA[HasResults]true AND AREA[Phase]PHASE3 AND AREA[StudyFirstPostDate]RANGE[MIN,2023-12-31]",
          "fields": "NCTId", "sort": "LastUpdatePostDate:desc"}
RULE = """# Safety v3 acquisition rule

Written before downloading candidate abstracts or inspecting their safety content.
Query ClinicalTrials.gov for completed phase 3 trials with results, first posted by
2023-12-31, ordered by last update descending. Take the first 500 API records,
remove already stored trials, sort IDs, shuffle with seed 20261112 and acquire the
first 60. Retain the exact API response, draw and ingest receipts. No model calls.
Trials with no registry safety or no usable listed abstracts cannot enter the
naturalistic evaluation; exclusions must be reported. No label/output-based redraw.
The naturalistic set uses production paper selection, at most ten per trial.
All prior evaluation PMIDs/text and trials must be excluded from strict validation.
Human review and a new freeze are required before evaluation calls. Existing v2
is development data. Real numeric branches may remain untested; any separately
constructed branch tests must be reported separately, never as natural prevalence.
"""


def main():
    settings = Settings()
    FOLDER.mkdir(exist_ok=True)
    rule = FOLDER / "RULE.md"
    if not rule.exists():
        rule.write_text(RULE, encoding="utf-8")
    with psycopg.connect(settings.database_url) as conn, httpx.Client(timeout=30) as http:
        draw_file = FOLDER / "draw.json"
        if not draw_file.exists():
            response = http.get(settings.clinical_trials_base_url + "/studies", params=PARAMS)
            response.raise_for_status()
            (FOLDER / "candidate_response.json").write_text(response.text, encoding="utf-8")
            existing = set(list_trial_ids(conn))
            candidates = sorted({s["protocolSection"]["identificationModule"]["nctId"]
                                 for s in response.json()["studies"]} - existing)
            random.Random(SEED).shuffle(candidates)
            draw_file.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(),
                "params": PARAMS, "seed": SEED, "already_stored": sorted(existing),
                "selected": candidates[:60], "reserve": candidates[60:]}, indent=2), encoding="utf-8")
        draw = json.loads(draw_file.read_text(encoding="utf-8"))
        receipt_path = FOLDER / "receipts.jsonl"
        receipts = [json.loads(s) for s in receipt_path.read_text().splitlines()] if receipt_path.exists() else []
        done = {r["nct_id"] for r in receipts if r["papers"] is not None or r["registry"]["status"] not in ("new_version", "unchanged")}
        for nct in draw["selected"]:
            if nct in done:
                continue
            registry = ingest_trial(conn, http, settings, nct)
            conn.commit()
            papers = ingest_papers(conn, http, settings, nct) if registry.status in ("new_version", "unchanged") else None
            conn.commit()
            result = {"nct_id": nct, "registry": registry.model_dump(mode="json"),
                      "papers": papers.model_dump(mode="json") if papers else None}
            with receipt_path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(result) + "\n")
            print(nct, registry.status, papers.status if papers else "not searched", flush=True)


if __name__ == "__main__":
    main()

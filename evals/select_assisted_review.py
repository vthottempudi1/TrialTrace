##############################################################################
# evals/select_assisted_review.py
#
# PURPOSE: Freeze the list of findings for the assisted review of "unclear" findings (rule in
#          evals/validation_v1/assisted_review/INSTRUCTIONS.md): every pair the model called unclear in the one
#          validation v1 run, in a fixed order (SHA-256 of "assisted-review" + match id). Written BEFORE any
#          review. Refuses to overwrite. No model call.
#
# USE:     python -m evals.select_assisted_review
##############################################################################

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from trialtrace.config import Settings

FOLDER = Path(__file__).parent / "validation_v1"
OUT = FOLDER / "assisted_review"


def main() -> None:
    target = OUT / "selection.json"
    if target.exists():
        raise SystemExit(f"{target} already exists; the selection is frozen.")
    runs = json.loads((FOLDER / "run" / "scores.json").read_text(encoding="utf-8"))["runs"]
    items = []
    with psycopg.connect(Settings().database_url) as conn:
        already = conn.execute("SELECT count(*) FROM paper_match_reviews").fetchone()[0]
        for nct_id, run_id in runs.items():
            rows = conn.execute(
                "SELECT match_id, nct_id, outcome_role, outcome_position, pmid, input_hash FROM paper_matches "
                "WHERE nct_id = %s AND match_run_id = %s AND system = 'llm' AND status = 'ambiguous'", (nct_id, run_id)).fetchall()
            for match_id, nct, role, position, pmid, input_hash in rows:
                items.append({"match_id": match_id, "nct_id": nct, "outcome_role": role, "outcome_position": position,
                              "pmid": pmid, "input_hash": input_hash, "match_run_id": run_id})
    items.sort(key=lambda item: hashlib.sha256(f"assisted-review{item['match_id']}".encode()).hexdigest())
    OUT.mkdir(exist_ok=True)
    body = {"frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "rule": "all pairs the model called unclear (status ambiguous) in the validation v1 run, ordered by sha256('assisted-review' + match_id)",
            "reviews_in_database_before_freeze": already, "count": len(items), "findings": items}
    target.write_text(json.dumps(body, indent=2), encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT / "selection.sha256").write_text(digest + "\n", encoding="utf-8")
    print(f"{len(items)} findings frozen, sha256 {digest[:16]}; reviews already in the database: {already}")


if __name__ == "__main__":
    main()
